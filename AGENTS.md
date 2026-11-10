# AGENTS.md

Guidance for coding agents (and humans) working in this repository.

## Overview

A GitOps-managed homelab. Flux reconciles the `main` branch into a two-node
Talos Linux cluster. Secret values live in OpenBao; Git holds ExternalSecrets
that the External Secrets Operator turns into Secrets. SOPS with Age is left
only for the democratic-csi driver configs, which OpenBao's own volume needs.
All deployment changes go through Git, not direct cluster commands.

This repository is public. Private apps live in a separate repository that the
cluster runs as a Flux tenant ([docs/tenants.md](docs/tenants.md)); never copy
anything from it into this one.

## Reviewing this repository

These are deliberate decisions with known trade-offs, explained in
[docs/design-decisions.md](docs/design-decisions.md) and
[docs/gitops.md](docs/gitops.md). Do not report them as gaps unless you
found something those explanations miss:

- One control plane (`homelab`): a third, low-power node for an etcd quorum is
  pending; a second control plane alone would lower availability.
- All data on TrueNAS: the nodes hold none, and the offsite copies cover the
  NAS.
- Alertmanager off: Grafana Alerting sends to Telegram, rules in Git;
  Prometheus' alerts arrive through one rule reading `ALERTS`, without their
  descriptions. Nothing watches Grafana itself.
- Grafana without a volume: datasources, dashboards and alerting come from
  Git; silences and alert state are lost on restart.
- Prometheus keeps 35 days (at most 27 GB).
- SeaweedFS as a TrueNAS app (`truenas/apps/seaweedfs`): an S3 staging area
  for CloudNativePG and OpenBao backups, publishing only S3. The backups are
  copied every night to `pool1/apps/db-dumps` and from there, encrypted, to a
  Hetzner Storage Box; SeaweedFS's own dataset stays on the NAS.
  `postgres-pantry` is not backed up on purpose.
- Flux's deploy key can push: image automation commits to `main`. Kept on
  purpose; a read-only key with Renovate pull requests was considered and not
  taken.
- CI on the cluster's Jenkins (`Jenkinsfile`, job in
  `apps/production/jenkins/jcasc.yaml`): it builds this repository's branches
  and the pull requests opened from them, never pull requests from forks, in
  a pod without a ServiceAccount token.
- Public hosts only through the Cloudflare tunnel; no port forwards.
- Claude Code box (`apps/production/claude-code`): reads the cluster without
  Secrets, admin only in `claude-agents`, fenced by admission policies, egress
  NetworkPolicies and an OpenBao store that refuses its namespaces.
- Cilium always admits traffic from a pod's own node, so host processes and
  host-network pods get past NetworkPolicies on that node (`talos/README.md`).
- Private apps in a separate tenant repository: own apps and prototypes kept
  out of public view.
- Built GitOps-first: Flux created everything in the cluster from this
  repository, apart from the bootstrap Secrets.

## Layout

- `clusters/homelab/`: Flux bootstrap (`flux-system/`) and the cluster's Flux Kustomizations
- `infrastructure/`: core components, Helm chart sources and image automation
- `apps/`: applications: shared bases in `apps/base/`, environments in
  `apps/production/` and `apps/dev/`
- `tenants/`: Flux tenants: namespace, ServiceAccount, Git source and
  Kustomizations for each private repository
- `talos/`: Talos machine config patches, applied with `talosctl`, not by Flux
  (`talos/README.md`). homelab (10.0.0.150) is the schedulable control plane,
  homelab2 (10.0.0.151) the worker. Cilium (`infrastructure/cilium`) is the
  CNI, replaces kube-proxy and enforces NetworkPolicies (check:
  `scripts/check-network-policies.sh`). tuppr
  (`infrastructure/tuppr`) upgrades Talos and Kubernetes patch releases at
  night; Flux image automation bumps its target versions.
- `truenas/`: apps on the TrueNAS host (node-exporter, SeaweedFS), installed by hand
- `scripts/`: validation helpers
- `tests/`: pytest checks for the manifests
- `Jenkinsfile`: the checks Jenkins runs on every branch and pull request
  (`scripts/validate.sh`, the tests and a flate render of every Kustomization
  and HelmRelease)
- `docs/`: [gitops.md](docs/gitops.md) (overview diagram and hosts,
  Kustomization order, bootstrap, network, secrets, Helm, monitoring, image
  automation, CI, backups and disaster recovery),
  [design-decisions.md](docs/design-decisions.md),
  [tenants.md](docs/tenants.md), and [README.md](docs/README.md), the index of
  these and of the runbooks beside the components

## Flux flow

1. Flux watches `main` through the `flux-system` GitRepository.
2. `sources` creates the HelmRepositories.
3. `external-secrets`, `cert-manager` and `cert-issuers` run first;
   `infrastructure` waits for `sources` and `cert-issuers` and turns Ready once
   its charts' CRDs exist (health checks).
4. `infrastructure-configs`, `democratic-csi` and `postgresql` wait for
   `infrastructure`; `prod` (`apps/production`) and `dev` (`apps/dev`) wait
   for `infrastructure` and `postgresql`.
5. `tenants` waits for `infrastructure`; each tenant's Kustomizations wait for
   the platform Kustomizations they build on.
6. Helm applications use a `release.yaml` HelmRelease beside their resources,
   with pinned chart versions and inline values. The line above `version:`
   links that version's `values.yaml`; a chart bump updates the link too
   (`tests/manifests/test_helm_values_links.py`).

## Key components

- `flux` with image automation; `flux-operator` only for its Web UI behind
  GitHub sign-in (`infrastructure/oauth2-proxy/home-bariss-dev`)
- `jenkins`: CI, including this repository's checks (`Jenkinsfile`)
- `cilium`: CNI (VXLAN, WireGuard between the nodes), kube-proxy replacement
  and NetworkPolicy enforcement; hostPorts answer on the LAN NIC and
  `tailscale0` (`talos/README.md`)
- `traefik` (ingress, TLS), `cert-manager`, `cloudflared` (tunnel for public hosts)
- `gluetun` (`apps/production/gluetun`): VPN proxy for the clients its
  NetworkPolicy admits
- `democratic-csi`: TrueNAS iSCSI and NFS storage
- `external-secrets` + `openbao` (`apps/production/openbao`): every Secret except
  the bootstrap ones
- `postgresql`: CloudNativePG with two clusters, `postgres` (backed up) and
  `postgres-pantry` (expendable data); runbook `apps/production/postgresql/README.md`
- `seaweedfs`: S3 for backups (OpenBao snapshots, PostgreSQL WAL and base
  backups), a TrueNAS app outside the cluster (`truenas/apps/seaweedfs`)
- `monitoring`: kube-prometheus-stack and Grafana; dashboards and alerting
  (Telegram) in Git
- `vpa` + `goldilocks`: every Namespace gets VPA mode `Initial`
  (`infrastructure/goldilocks/namespace-defaults`); requests in Git are starting values
- `descheduler` (`infrastructure/descheduler`): an hourly CronJob that moves
  pods back to a node that returns nearly empty after a drain or reboot; it
  reads requests, as the scheduler does, and moves a pod at most once a day
- `umami` (`apps/production/umami`): web analytics for the blog; dashboard
  behind the GitHub sign-in, tracker on the blog's own host
- `reloader`, `metrics-server`, `intel-gpu-plugin`, `adguardhome`, `tuppr`

## Commands

```bash
./scripts/validate.sh              # kubeconform + kustomize build of everything
./scripts/validate.sh --cluster    # only the Flux cluster definitions
./scripts/validate.sh --kustomize  # only the kustomize overlays
uv run --no-project --with pytest --with pyyaml python -m pytest tests
```

Tests need kustomize 5.8 or newer on `PATH`. They read private values (LAN and
Tailscale addresses, host names, NAS paths) from `.env`, which Git ignores; copy
`.env.example` and fill it in. Tests that need a missing value are skipped.

Secrets (OpenBao CLI with an admin token):

```bash
kubectl -n openbao port-forward svc/openbao-active 8200
export BAO_ADDR=http://127.0.0.1:8200
bao kv put secret/<namespace>/<secret name> KEY=value   # new secret
bao kv patch secret/<namespace>/<secret name> KEY=value # change one key
```

Each Secret has an ExternalSecret in Git that reads OpenBao's KV v2 key
`secret/<namespace>/<secret name>` and lists every key (`spec.data`), so a new key
needs both the value in OpenBao and a line in Git. ESO refreshes every 10 minutes.
`tests/manifests/test_external_secrets.py` keeps the layout. Never print or
commit plaintext secret values.

## Rules

- **GitOps only**: commit to Git; Flux applies it. Do not change workloads with
  `kubectl apply`/`patch`.
- **No secrets in Git**: values go into OpenBao, Git gets an ExternalSecret. A
  config file that contains a secret becomes a ConfigMap template rendered by an
  ExternalSecret (`infrastructure/adguardhome`). The same goes for personal
  identifiers: email addresses, provider account names, Tailscale addresses and
  MAC addresses never appear in this repository ([docs/gitops.md](docs/gitops.md#secrets)),
  tests included: they read such values from `.env` (`tests/private_values.py`).
- **Docs stay current**: the docs and the architecture diagrams describe the
  cluster as it is, so a change that adds, removes or moves a component, a
  connection, a backup path or a decision updates them in the same commit:
  the component's README, [docs/gitops.md](docs/gitops.md) (its text and its
  mermaid diagrams), [docs/design-decisions.md](docs/design-decisions.md) and
  the review list above, the README's stack table, this file and the comments
  in the manifests. Describe the current state, not how it got there ("moved",
  "no longer", "now"), and delete what is gone. Before committing, search the
  Markdown for every name the change touches (`git grep -i <name> -- '*.md'`).
  A new README gets a line in [docs/README.md](docs/README.md).
- **Validate before pushing**: `./scripts/validate.sh` and the focused tests.
- **Exposure**: Services stay ClusterIP (no NodePort, LoadBalancer or
  externalIPs); reach a LAN host through a Traefik route. An app without a
  login of its own gets a NetworkPolicy that admits only its clients, and a
  new admin UI goes behind the GitHub sign-in
  ([docs/gitops.md](docs/gitops.md#network), `tests/manifests/test_exposure.py`).
- **Retention**: Namespaces, PersistentVolumes and PersistentVolumeClaims carry
  `kustomize.toolkit.fluxcd.io/prune: disabled`. Deleting a Namespace also
  deletes its PVCs, so keep that protection on Namespaces too. Delete PVCs only
  by hand.
- **Pinned volume IDs**: every provisioned PVC (`freenas-iscsi-csi`,
  `freenas-nfs-csi`) names its PV in `spec.volumeName`, and that PV is in Git
  with its `pvc-<uuid>` name, `csi.volumeHandle`, volume attributes, `Retain`
  and a `claimRef` to the claim (iSCSI PVs also keep `csi.nodeStageSecretRef`),
  so a rebuilt cluster binds the same disk instead of provisioning an empty one
  (`tests/manifests/test_pinned_volumes.py`). Provision first, then commit the
  PV and the claim together: create a throwaway claim in `claude-agents`,
  delete it (the PV stays `Released`), commit the PV with a `claimRef` to the
  real claim, and remove the throwaway claim's uid from the live PV once
  (`kubectl patch pv <name> --type json -p '[{"op":"remove","path":"/spec/claimRef/uid"}]'`).
  Never commit a `uid`. CloudNativePG is the exception: the operator owns its
  instances' claims, and a rebuilt cluster restores from the Barman Cloud backups.
- **Jobs** whose image follows automation carry
  `kustomize.toolkit.fluxcd.io/force: enabled` so Flux recreates them.
- **Chart CRDs**: `infrastructure` must not contain objects whose CRD a chart in
  it installs; put them under `infrastructure/configs/`. A new kind used after
  `infrastructure` needs its CRD in `infrastructure`'s health checks
  ([docs/gitops.md](docs/gitops.md)).
- **One reconciler**: no second GitOps controller for workloads Flux manages.
- **Official charts first**: an app with an official Helm chart is deployed as a
  HelmRelease of that chart; hand-written manifests only when there is none.
- **Kustomize structure**: one namespace per app; a file that holds only
  ExternalSecrets or Secrets is named `<name>-secret.yaml`.

## Prerequisites

- kustomize v5.8+ and kubeconform
- sops and age (only for the democratic-csi driver configs)
- bao, the OpenBao CLI (optional; the UI works too)
- flux CLI (optional, for status and manual reconciles)
