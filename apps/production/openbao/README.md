# OpenBao

The official OpenBao chart (`release.yaml`): one server, integrated raft
storage on the pinned iSCSI volume `data-openbao-0`, static auto-unseal, UI at
`https://openbao.home.bariss.dev` behind the GitHub sign-in. It holds every
Secret's values except the bootstrap ones (democratic-csi driver configs, which
its own volume needs). The External Secrets Operator copies them into the
cluster (`infrastructure/external-secrets`, role `external-secrets`: read on
`secret/*`). ESO writes only generated values, once each, through a role per
writer: `postgresql-passwords` (create and read on `secret/postgresql/*`,
`apps/base/postgresql/passwords.yaml`) and `umami-secrets` (`secret/umami/*`,
Umami's `APP_SECRET`, `apps/production/umami/generated-secrets.yaml`). Another
app's generated secrets get their own policy and role like these, written
with the root token (bootstrap below).

## Adding or changing a secret

KV v2 mount `secret`, key `<namespace>/<secret name>`, one field per Secret key:
the UI, or `bao kv put` / `bao kv patch` (CLI below). A new Secret or a new key
also needs its ExternalSecret in Git (copy any `*-secret.yaml` in `apps/`); the
cluster picks a change up within 10 minutes.

## Keys outside the cluster

Three values live in Bitwarden ("OpenBao"), not in Git:

- **Seal key**: 64 hex characters, no newline. The Secret `openbao/openbao-seal`
  (key `key`) holds it; OpenBao reads it at every start and unseals itself. A
  snapshot is useless without it.
- **Recovery key** (one share): for `bao operator generate-root` and other
  recovery operations. It does not unseal.
- **Root token**: for setup. Revoke it once an admin auth method exists.

## Bootstrap (new cluster)

Before Flux applies this directory, or right after:

```sh
kubectl create namespace openbao
kubectl -n openbao create secret generic openbao-seal --from-file=key=seal-key.hex
```

With existing data (the iSCSI volume or a restored snapshot), nothing else is
needed. A fresh install is initialized once:

```sh
kubectl -n openbao exec openbao-0 -- bao operator init -recovery-shares=1 -recovery-threshold=1 -format=json
```

and then configured with the root token:

```sh
bao secrets enable -path=secret kv-v2
bao policy write external-secrets - <<'EOF'
path "secret/data/*" {
  capabilities = ["read"]
}
path "secret/metadata/*" {
  capabilities = ["read", "list"]
}
EOF
bao auth enable kubernetes
bao write auth/kubernetes/config kubernetes_host=https://kubernetes.default.svc
bao policy write snapshot - <<'EOF'
path "sys/storage/raft/snapshot" {
  capabilities = ["read"]
}
EOF
bao write auth/kubernetes/role/snapshot bound_service_account_names=openbao-snapshot \
  bound_service_account_namespaces=openbao token_policies=snapshot token_ttl=10m token_max_ttl=15m
bao write auth/kubernetes/role/external-secrets bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets token_policies=external-secrets token_ttl=1h token_max_ttl=2h
bao policy write postgresql-passwords - <<'EOF'
path "secret/data/postgresql/*" {
  capabilities = ["create", "read", "update"]
}
path "secret/metadata/postgresql/*" {
  capabilities = ["create", "read", "update", "list"]
}
EOF
bao write auth/kubernetes/role/postgresql-passwords bound_service_account_names=postgresql-passwords \
  bound_service_account_namespaces=postgresql token_policies=postgresql-passwords token_ttl=1h token_max_ttl=2h
bao policy write umami-secrets - <<'EOF'
path "secret/data/umami/*" {
  capabilities = ["create", "read", "update"]
}
path "secret/metadata/umami/*" {
  capabilities = ["create", "read", "update", "list"]
}
EOF
bao write auth/kubernetes/role/umami-secrets bound_service_account_names=umami-secrets \
  bound_service_account_namespaces=umami token_policies=umami-secrets token_ttl=1h token_max_ttl=2h
```

## CLI

```sh
kubectl -n openbao port-forward svc/openbao-active 8200
export BAO_ADDR=http://127.0.0.1:8200
```

The ingress is behind the GitHub forwardAuth, so the CLI cannot use it.

## Backups

The chart's snapshot agent (`snapshotAgent` in `release.yaml`, CronJob
`openbao-snapshot`) saves a raft snapshot at 23:05 UTC and uploads it as
`bao_<date>-<time>.snapshot` to the bucket `openbao-snapshots` on SeaweedFS, a
TrueNAS app at `http://10.0.0.203:8333` (`truenas/apps/seaweedfs`), with that
bucket's own key (`secret/shared/openbao-snapshots-s3`). It deletes uploads
older than 14 days.

SeaweedFS keeps objects inside its own volume files, so the offsite copy does
not go through its dataset: the CronJob `openbao-snapshot-copy`
(`snapshot-copy.yaml`) copies the bucket as plain files into `openbao/` on the
TrueNAS dataset `pool1/apps/db-dumps` at 23:25 UTC and keeps them 30 days. The
Cloud Sync task "Hetzner: database dumps" uploads that dataset,
rclone-encrypted, at 00:45 Berlin time. The job fails, and prunes nothing, when
no snapshot from the last two days is there. The snapshots count as database
backups: the raft store is OpenBao's database, and the offsite copy keeps the
secrets when the house is lost. An extra snapshot now:

```sh
kubectl -n openbao create job --from=cronjob/openbao-snapshot openbao-snapshot-manual
```

Restore into a running server (the same seal key must be in `openbao-seal`;
on an empty volume, `bao operator init` first and use that temporary root
token). Take a snapshot from `/mnt/pool1/apps/db-dumps/openbao/` on the NAS,
from the Hetzner copy (rclone with the Cloud Sync task's encryption password
and salt), or from the bucket with its own key:

```sh
export RCLONE_CONFIG_SW_TYPE=s3 RCLONE_CONFIG_SW_PROVIDER=SeaweedFS RCLONE_CONFIG_SW_ENDPOINT=http://10.0.0.203:8333
export RCLONE_CONFIG_SW_ACCESS_KEY_ID=$(kubectl -n openbao get secret openbao-snapshots-s3 -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
export RCLONE_CONFIG_SW_SECRET_ACCESS_KEY=$(kubectl -n openbao get secret openbao-snapshots-s3 -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)
rclone lsl sw:openbao-snapshots
rclone copyto sw:openbao-snapshots/<name>.snapshot restore.snap
kubectl -n openbao cp restore.snap openbao-0:/tmp/restore.snap
kubectl -n openbao exec -it openbao-0 -- sh -c 'BAO_TOKEN=<token> bao operator raft snapshot restore -force /tmp/restore.snap'
```

Afterwards the snapshot's own root token and recovery key apply again.

## Rotating the seal key

Put the new key in `openbao-seal` beside the old one, set `current_key` and a new
`current_key_id` to the new key and `previous_key`/`previous_key_id` to the old
one, restart, then drop `previous_key` once OpenBao has re-wrapped its keys.
