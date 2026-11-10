# Docs

- [Architecture and GitOps](gitops.md): overview diagram and hosts, Flux and
  the order it applies things in, CRDs from charts, bootstrap, network, the
  GitHub sign-in for admin UIs, secrets, Helm, monitoring, image automation,
  CI, backups and disaster recovery.
- [Design decisions and trade-offs](design-decisions.md): what a review tends
  to flag, and why it is the way it is.
- [Tenants](tenants.md): how a private repository runs on the cluster as a
  Flux tenant.

Runbooks sit next to what they describe:

| Runbook | Covers |
| --- | --- |
| [Talos](../talos/README.md) | Machine configs, Cilium and the NetworkPolicies, reinstalling a node, Tailscale, upgrades |
| [PostgreSQL](../apps/production/postgresql/README.md) | The two CloudNativePG clusters, roles and passwords, failover, backups, restore |
| [OpenBao](../apps/production/openbao/README.md) | Adding secrets, keys kept outside the cluster, bootstrap, snapshots, restore |
| [SeaweedFS](../truenas/apps/seaweedfs/README.md) | The S3 staging area for backups on TrueNAS: buckets, keys, install, offsite copy |
| [node-exporter](../truenas/apps/node-exporter/README.md) | The NAS's metrics for Prometheus |
| [Umami](../apps/production/umami/README.md) | Web analytics: the dashboard, the tracker on the blog, first sign-in, secrets |
| [Frigate](../apps/production/frigate/README.md) | The camera, offsite recordings, restore after losing the house |
| [Claude Code box](../apps/production/claude-code/README.md) | The 24/7 Claude Code box: image, rights, sign-in |
| [claude-agents](../apps/production/claude-agents/README.md) | The box's sandbox namespace and its browser |
