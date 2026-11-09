# homelab-infra

GitOps configuration for my homelab: a two-node [Talos Linux](https://www.talos.dev/)
Kubernetes cluster that [Flux](https://fluxcd.io/) builds from this repository,
with storage on TrueNAS, secrets in OpenBao and nightly encrypted backups to a
Hetzner Storage Box. Nothing is port-forwarded: public hosts go through a
Cloudflare tunnel, and the rest is reachable on the LAN and over Tailscale only.

## Documentation

- **[Architecture and GitOps](docs/gitops.md)**: what runs where and how it
  fits together, with diagrams: Flux and the order it applies things in, the
  network, secrets, monitoring, image automation, CI, backups and disaster
  recovery.
- **[Design decisions and trade-offs](docs/design-decisions.md)**: what a
  review tends to flag, and why it is the way it is.
- **[Tenants](docs/tenants.md)**: private apps from a separate repository.
- **[All docs and runbooks](docs/README.md)**: Talos, PostgreSQL, OpenBao,
  SeaweedFS, Frigate and the rest.

## Stack

| Area | Components |
| --- | --- |
| OS and upgrades | Talos Linux, [tuppr](https://github.com/home-operations/tuppr) for Talos/Kubernetes patch upgrades |
| GitOps | Flux (kustomize, helm, image automation), Flux Operator Web UI, checks on Jenkins |
| Networking | Cilium (CNI, NetworkPolicies, no kube-proxy, WireGuard between the nodes), Traefik, cert-manager, cloudflared, AdGuard Home |
| Storage | democratic-csi (TrueNAS iSCSI/NFS) with pinned volumes, SeaweedFS (S3 for backups, a TrueNAS app) |
| Secrets | OpenBao + External Secrets Operator; SOPS/age only for the storage driver bootstrap |
| Databases | CloudNativePG: a backed-up main cluster and an expendable "pantry" cluster |
| Observability | kube-prometheus-stack, Grafana (dashboards and alerts to Telegram in Git), VPA + Goldilocks |
| Auth | oauth2-proxy with GitHub sign-in in front of admin UIs |
| Apps | Immich, Nextcloud, Jellyfin and the *arr stack, Frigate, Stalwart + Roundcube, Vikunja, Redlib, Jenkins, a Claude Code box |

## Repository layout

```
clusters/homelab/   Flux bootstrap and the cluster's Flux Kustomizations
infrastructure/     controllers, chart sources, image automation, monitoring
apps/               applications (base/, production/, dev/)
tenants/            Flux tenants that pull private repositories
talos/              Talos machine config patches and runbook
truenas/            apps on the NAS host
scripts/, tests/    validation and pytest checks for the manifests
docs/               how the pieces fit together
```

## Working on it

```bash
./scripts/validate.sh
uv run --no-project --with pytest --with pyyaml python -m pytest tests
```

Tests that compare the manifests with private values (addresses, host names)
read them from `.env` (see `.env.example`) and are skipped without it.

See [AGENTS.md](AGENTS.md) for the rules the repository follows.

## License

[MIT](LICENSE)
