# Talos

homelab (10.0.0.150, control plane, also runs workloads) and homelab2
(10.0.0.151, worker) run Talos Linux v1.14.5 with Kubernetes v1.36.6. The
nodes have no SSH and no package manager: `talosctl` configures them, and
Flux builds everything else from Git (`docs/gitops.md`).

| File | Content |
| --- | --- |
| `schematic.yaml` | Image Factory extensions: i915, intel-ucode, iscsi-tools, util-linux-tools, tailscale |
| `patches/common.yaml` | Both nodes: inotify, user namespaces (Talos default 0), iSCSI disk timeout (udev), kubelet, nameservers, pod/service CIDRs, workload isolation off |
| `patches/controlplane.yaml` | homelab: no control-plane taint, certSANs, privileged Pod Security default, no Talos CNI and no kube-proxy: Cilium replaces both (see [NetworkPolicies](#networkpolicies)) |
| node patches | Hostname, install disk, static address by MAC, and homelab's Tailscale address in the certSANs. They are kept in the private tenant repository (`talos/nodes/`), not here |
| `patches/tailscale.yaml` | Tailscale extension settings (no auth key, see below) |

The `talosctl gen secrets` bundle (cluster CA, etcd, Kubernetes and OS keys, tokens)
is not in Git: it is the attachment `talos-secrets.yaml` of the Bitwarden item
"Talos secrets bundle". `talosctl` regenerates the client config and the kubeconfig
from it, so it is the one thing a rebuild needs besides this directory.

Talos 1.14 splits the machine config into typed documents (`HostnameConfig`,
`ResolverConfig`, `KubeletConfig`, `KubeNodeConfig`, ...). A v1alpha1 field and
the document that replaces it may not both be set; the patches use the
documents wherever `talosctl validate` asks for them.

Workload isolation (`SecurityProfileConfig`) is off: it moves the kubelet and
pods into their own PID and mount namespace, and democratic-csi runs
`iscsiadm` by entering PID 1's namespaces.

Schematic ID `e1449fb707121cea4febe13313f0ffa1bf5be363223467a4a9b3103974f33fbd`:

- ISO: `https://factory.talos.dev/image/<ID>/v1.14.5/metal-amd64.iso`
- Kernel, initramfs and kernel arguments: `kernel-amd64`, `initramfs-amd64.xz`
  and `cmdline-metal-amd64` under `https://factory.talos.dev/image/<ID>/v1.14.5/`
- Installer: `factory.talos.dev/metal-installer/<ID>:v1.14.5`

## Machine configs

Machine configs hold the secrets, so they are generated into a temporary
directory and never committed. Save the bundle from Bitwarden as
`$T/secrets.yaml` first (`$T` is any scratch directory outside the
repository), then run from the repository root (Git Bash):

```sh
ID=e1449fb707121cea4febe13313f0ffa1bf5be363223467a4a9b3103974f33fbd
talosctl gen config homelab https://10.0.0.150:6443 \
  --with-secrets $T/secrets.yaml \
  --kubernetes-version 1.36.6 \
  --install-image factory.talos.dev/metal-installer/$ID:v1.14.5 \
  --with-docs=false --with-examples=false \
  --config-patch @talos/patches/common.yaml \
  --config-patch @talos/patches/tailscale.yaml \
  --config-patch-control-plane @talos/patches/controlplane.yaml \
  --output $T/out
NODES=<private checkout>/talos/nodes
talosctl machineconfig patch $T/out/controlplane.yaml --patch @$NODES/homelab.yaml -o $T/out/homelab.yaml
talosctl machineconfig patch $T/out/worker.yaml --patch @$NODES/homelab2.yaml -o $T/out/homelab2.yaml
talosctl validate --mode metal --config $T/out/homelab.yaml
talosctl validate --mode metal --config $T/out/homelab2.yaml
```

`$T/out/talosconfig` is the client config: copy it to `~/.talos/config`, then
`talosctl config endpoint 10.0.0.150` and `talosctl config node 10.0.0.150`.
`talosctl kubeconfig` writes the kubeconfig. Both work over Tailscale too
(`-e <homelab's Tailscale address>` and the same address in the kubeconfig's
`server`): the node patch puts it in the certificates.

To change a running node, edit the patch and apply the regenerated file:
`talosctl apply-config -n 10.0.0.150 --file $T/out/homelab.yaml` (most
changes apply without a reboot; the command says when one is needed).
Changes to the cluster manifests Talos renders (CoreDNS) reach the cluster
only with `talosctl -n 10.0.0.150 upgrade-k8s --to <current version>`; check
them first with `--dry-run`.

## NetworkPolicies

[Cilium](https://docs.cilium.io/en/v1.20/) (`infrastructure/cilium`) is the
CNI. It routes pod traffic between the nodes over VXLAN inside WireGuard
(UDP 51871; OpenBao and several apps speak plain HTTP in the cluster),
replaces kube-proxy (Services, hostPorts and NodePorts in eBPF) and enforces the standard
`networking.k8s.io` NetworkPolicies; this repository uses no Cilium policy
kinds. Talos deploys no CNI and no kube-proxy (`patches/controlplane.yaml`),
and the agents reach the API through KubePrism (`localhost:7445`), Talos's
API load balancer on every node.

What the policies in this repository depend on:

- Cilium gives every set of pod labels (namespace included) an identity and
  carries it through the tunnel, so pod and namespace selectors work across
  nodes. Pods no policy selects are not filtered.
- Services, hostPorts and NodePorts are translated when a connection opens,
  before any policy sees it, so a rule sees the pod behind a Service. The
  claude-code and claude-agents egress policies rely on that: their ipBlock
  excludes 10.42.0.0/16 and 10.43.0.0/16 and allows pods by namespace.
- An ipBlock matches addresses outside the cluster and, with
  `policyCIDRMatchMode: [nodes]`, the nodes' own addresses: the API server and
  host-network pods such as AdGuard. Pods match only pod and namespace
  selectors. A `0.0.0.0/0` block does not select the pod's own node, so the
  claude-code and claude-agents egress rules list both node addresses.
- hostPorts and NodePorts answer on the addresses of Cilium's `devices`: the
  LAN NIC (`en+`) and `tailscale0`, so Traefik answers on the nodes' LAN and
  Tailscale addresses. A hostPort's backend is on the same node, so the client
  address reaches the pod (Frigate's 8554 rule for the Pi, Traefik's client
  addresses).
- Everything from the pod's own node passes: kubelet probes and any process or
  host-network pod on that node, root or not. Host-network traffic from the
  other node arrives as that node's address; it matches an ipBlock that covers
  it, never a pod or namespace selector.
- It does not fail open. The eBPF programs stay in the kernel while an agent
  restarts and keep enforcing; a node without a running agent starts no new
  pods. `kubectl -n kube-system get ds cilium` must show 2/2 ready.

Check it with `./scripts/check-network-policies.sh`. It checks that the agents
run, then, from a throwaway pod in claude-agents on each node in turn, it must
reach DNS, the internet, the Kubernetes API, Traefik, AdGuard and PostgreSQL
(`postgres-rw`), and time out on OpenBao, Immich, Nextcloud, Stalwart,
Frigate, the NAS (SeaweedFS's S3 included), the camera DNAT and
claude-browser's MCP port (every line `ok`). Hubble runs inside each agent (no
relay, no UI) and shows the flows and verdicts of its own node; pick the agent
on the pod's node:

```sh
kubectl -n kube-system get pods -l k8s-app=cilium -o wide
kubectl -n kube-system exec <cilium pod> -c cilium-agent -- hubble observe --namespace claude-agents --verdict DROPPED
kubectl -n kube-system exec <cilium pod> -c cilium-agent -- cilium-dbg status --verbose
```

Flux upgrades Cilium like any HelmRelease: bump the chart version in
`infrastructure/cilium/release.yaml` one minor at a time (the
[upgrade guide](https://docs.cilium.io/en/stable/operations/upgrade/) lists
what each minor changes) and only to a version whose compatibility table
lists the cluster's Kubernetes minor.

## Reinstalling a node

Run it from a PC on the LAN (10.0.0.0/24): in maintenance mode the node has
no Tailscale.

1. Boot the node into maintenance mode from a USB stick with the ISO. Talos
   then runs in RAM and touches no disk. With the kernel argument
   `ip=10.0.0.150::10.0.0.1:255.255.255.0:homelab:<interface>:off:10.0.0.87:1.1.1.1`
   (homelab2: `10.0.0.151`, `homelab2`; `<interface>` is `enx` plus the NIC's MAC
   from the node patch) it comes up on its
   own address; otherwise it takes one from DHCP.
2. Check it: `talosctl -n <ip> -e <ip> get disks --insecure` and `get links
   --insecure`. homelab installs to `sda` (intecko 256 GB SATA), homelab2 to
   `nvme0n1` (Samsung MZVLW256HEHP), and the link must have the MAC in the
   node's patch.
3. `talosctl apply-config --insecure -n <ip> -e <ip> --file $T/out/homelab.yaml`.
   This is the only step that erases anything: Talos wipes the install disk,
   installs, and reboots on its static address.
4. A new cluster only: `talosctl -n 10.0.0.150 bootstrap` once on homelab.
5. Check `talosctl -n <node> get extensions` (five extensions) and
   `talosctl -n <node> services` (`ext-tailscale` and `ext-iscsid` Running).
6. A new cluster only: bootstrap Flux as in `docs/gitops.md`.

## Tailscale

The extension keeps its state in `/var/lib/tailscale` on the node's disk and
logs in only when that state is missing (`TS_AUTH_ONCE`). The stored state
(`tailscaled.state` and `profile-data/`) keeps both nodes' Tailscale names and
addresses, so no auth key exists. AdGuard runs on homelab and binds to its LAN
and Tailscale addresses; AdGuard's rewrite rules use both nodes' addresses (the
Tailscale ones come from OpenBao), and homelab's node patch adds its Tailscale
address to the certSANs.

After a reinstall the state is gone: `talosctl -n <node> logs ext-tailscale`
prints a login URL. Open it, then in the admin console delete the old machine,
give the new one the old address and turn off its key expiry.

## Rebuilding the cluster

Every PV lives on TrueNAS and is pinned in Git, so a rebuilt cluster binds the
same volumes; the claude-agents volumes that the Claude Code box makes with
kubectl are pinned in `apps/production/claude-agents/agent-volumes.yaml`.

## Upgrades

tuppr (`infrastructure/tuppr`) does them. `upgrades/talos.yaml` (TalosUpgrade)
and `upgrades/kubernetes.yaml` (KubernetesUpgrade) hold the target versions;
Flux image automation (`flux-system-tuppr`) moves them to the newest patch of
the current minor (Talos `~1.14` from `ghcr.io/siderolabs/talos`, Kubernetes
`~1.36` from `ghcr.io/siderolabs/kubelet`) and pushes to main. tuppr then
upgrades in its window (04:30-06:30 Europe/Berlin), one node at a time from the
other node, only while every node is Ready and both `postgres` instances are
ready (the primary's PDB blocks a drain until it can switch over). It keeps
each node's Image Factory schematic, so the extensions come along. The kernel
comes with Talos.

A failed upgrade leaves its node cordoned, on the old version; homelab2 cannot
hold homelab's pods, so most of them stay Pending until
`kubectl uncordon <node>`.

- Status: `kubectl get talosupgrade,kubernetesupgrade`.
- New minor (Talos 1.15, Kubernetes 1.37): change the policy range and the
  version by hand, one minor at a time; Kubernetes only once Flux,
  cert-manager and Cilium support it.
- By hand, without tuppr:

```sh
talosctl -n 10.0.0.151 upgrade --image factory.talos.dev/metal-installer/$ID:<version>
talosctl -n 10.0.0.150 upgrade --image factory.talos.dev/metal-installer/$ID:<version>
talosctl -n 10.0.0.150 upgrade-k8s --to <1.36.x>
```

Talos boots the new image and keeps the old one; `talosctl rollback` returns
to it. homelab is the only control plane, so its upgrade takes the Kubernetes
API away for a few minutes.

## Without SSH

- Logs: `talosctl -n <node> logs kubelet`, `dmesg`, `services`.
- Files: `talosctl -n <node> read /sys/fs/ext4/<sdX>/errors_count` (an
  aborted ext4 volume still shows `rw` in /proc/mounts).
- Shell tools such as e2fsck: `kubectl debug node/<node> -it --profile=sysadmin --image=alpine`,
  host filesystem under `/host`.
