# Design decisions and trade-offs

Things a review tends to flag, and why they are the way they are. How the
pieces fit together: [Architecture and GitOps](gitops.md).

- **One control plane.** `homelab` is the only control-plane node. If it goes
  down, pods on `homelab2` keep running, but nothing is scheduled or changed
  until it is back. Real HA needs three control planes for an etcd quorum; a
  second one alone would make things worse, because losing either node would
  then stop etcd. The spare machine I have draws too much power to run around
  the clock, so the third node waits for a low-power one.
- **The nodes hold no data.** Every volume is an iSCSI or NFS volume on
  TrueNAS, pinned in Git, so any pod can run on either node and a node can be
  reinstalled without losing anything. The NAS is therefore the one place the
  data lives; losing it is covered by the offsite copies
  ([Backups and disaster recovery](gitops.md#backups-and-disaster-recovery)).
- **Alerting through Grafana.** Alertmanager is off. Grafana Alerting sends
  the notifications to Telegram, with its rules, contact points and policy in
  Git. Prometheus' own alerts reach it through one rule that reads their
  `ALERTS` series, so the kube-prometheus-stack rules need no rewrite; the
  price is that a forwarded message carries the alert's name and labels but
  not its description. Nothing watches Grafana itself: if it or the cluster
  goes down, Telegram stays quiet.
- **Grafana has no volume.** Its datasources, dashboards and alerting come
  from Git on every start. Nothing in Grafana is state worth keeping; silences
  and the alerts' state are lost on restart, so an alert firing at the time
  sends once more.
- **Prometheus keeps 35 days** (at most 27 GB on a 30 Gi iSCSI volume), so
  30-day queries always have a full window.
- **Backups leave the cluster and the house.** SeaweedFS exists only because
  CloudNativePG's Barman Cloud plugin and OpenBao's snapshot agent write to S3,
  and it runs as a TrueNAS app, outside the cluster; MinIO's community edition
  is archived upstream and ships no images. Every night the database backups
  are copied out of it as plain files and uploaded, encrypted, to a Hetzner
  Storage Box that keeps 10 daily snapshots; nothing else in SeaweedFS leaves
  the NAS. Immich and Nextcloud are synced there too. The `postgres-pantry`
  cluster is not backed up on purpose: its data is expendable. Failure
  scenarios and what each one loses:
  [Backups and disaster recovery](gitops.md#backups-and-disaster-recovery).
- **Flux can push to Git, by choice.** Image automation commits new image tags
  to `main`, so the deploy key has write access, and whoever can read Secrets
  in `flux-system` could push to this repository. The alternative, a read-only
  key with Renovate opening pull requests, would take updates out of Flux; I
  keep them in Flux.
- **CI runs on the cluster's Jenkins.** The checks for this repository
  (`Jenkinsfile`) run in a pod in the `jenkins` namespace, with no
  ServiceAccount token. The repository is public, so Jenkins builds only its
  own branches and the pull requests opened from them, never pull requests
  from forks, whose Jenkinsfile would run in the cluster
  ([CI](gitops.md#ci)).
- **Public hosts go through a Cloudflare tunnel.** The tunnel connects outward,
  so the router forwards no ports, and it answers 404 for every
  `*.home.bariss.dev` name. Everything else is reachable on the LAN and over
  Tailscale only, and the admin UIs ask for a sign-in on top
  ([Network](gitops.md#network)).
- **Umami's tracker is public, on a host of its own.** The blog runs on
  Cloudflare Pages, outside the cluster. A Pages Function serves Umami's
  script and collect endpoint from the blog's own host and forwards them to
  `umami.bariss.dev`, where the tunnel and Traefik pass only those two paths;
  the dashboard and Umami's own sign-in stay on the LAN behind the GitHub
  sign-in. Visitors only ever see the blog's host, so a blocker that stops
  the tracker stops the blog ([Umami](../apps/production/umami/README.md)).
- **One sign-in host for the admin UIs.** Every `*.home.bariss.dev` route
  signs in through `auth.home.bariss.dev`, the GitHub App's only callback URL,
  so a new UI needs only the forwardAuth middleware. GitHub's wildcard
  matching for callback URLs would do the same, but GitHub warns against it
  and it stays off ([Sign-in](gitops.md#sign-in)).
- **NetworkPolicies do not fence a pod's own node.** Cilium always admits
  traffic from the node a pod runs on (the kubelet's probes need it), so any
  process or host-network pod on that node gets past the pod's policy. Every
  other pod, on either node, and the rest of the network are still filtered
  ([talos/README.md](../talos/README.md#networkpolicies)).
- **A Claude Code box in the cluster.** Claude Code runs around the clock with
  read access to the cluster (no Secrets) and admin rights only in its sandbox
  namespace `claude-agents`. Admission policies stop it from mounting NAS
  volumes, changing NetworkPolicies or Ingresses, opening NodePorts and
  creating Flux, External Secrets or cert-manager objects, which their
  controllers would apply with cluster-wide rights. Its egress policy keeps it
  away from the NAS, the camera, the IoT network and the namespaces that hold
  private data, and OpenBao's store does not serve its namespaces
  ([claude-code](../apps/production/claude-code/README.md)).
- **A private tenant.** My own apps and prototypes that I don't want public live
  in a private repository that runs as a Flux tenant
  ([tenants.md](tenants.md)). Keeping the platform here and the apps there is
  deliberate.
- **GitOps from the start.** The cluster was built from this repository: apart
  from the bootstrap Secrets, Flux created everything in it.
