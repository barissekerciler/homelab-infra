# PostgreSQL

The cluster's PostgreSQL runs on CloudNativePG: the operator and the Barman
Cloud plugin are official charts in `infrastructure/cloudnative-pg`.

Two clusters, by how much the data matters. PostgreSQL backs up a whole
cluster, never single databases, so the split is the backup policy:

| Cluster | Directory | Holds | Backups |
| --- | --- | --- | --- |
| `postgres` (main) | `apps/production/postgresql` | data that matters: Immich, Nextcloud, Stalwart, Roundcube, Vikunja, Dispatcharr, Umami, anything important added later | WAL archive + nightly base backup, 14 days, copied to Hetzner |
| `postgres-pantry` | `apps/dev/postgresql` | handy but expendable: test and scratch databases | none |

A new database goes into the main cluster unless losing it would not matter.

Layout:

| Directory | Flux Kustomization | What |
| --- | --- | --- |
| `apps/base/postgresql` | `postgresql` (before `prod` and `dev`) | what both clusters share and exists once: Namespace, password store (`passwords.yaml`), superuser, PodMonitor |
| `apps/base/postgresql/cluster` | none of its own | the Cluster both overlays build on (image, superuser, initdb, settings, resources) |
| `apps/production/postgresql` | `prod` | the main cluster: its `cluster.yaml` patch (name, instances, size), backups, and its roles and databases in `databases/` |
| `apps/dev/postgresql` | `dev` | the pantry: its `cluster.yaml` patch |

A role and its database go next to the app that uses them. Tenants
(`docs/tenants.md`) keep theirs in their own repository, in the same
`postgresql` namespace and with the same password store.

- Cluster `postgres`, namespace `postgresql`, two instances with required
  anti-affinity: one on homelab, one on homelab2. Volumes: `freenas-iscsi-csi`,
  200Gi each, provisioned by the operator.
- Image: tensorchord's `cloudnative-vectorchord` (PostgreSQL 17,
  VectorChord, pgvector) for Immich; `vchord` is preloaded.
- Apps connect to the primary, `postgres-rw.postgresql.svc:5432`, directly. No
  pooler: every app keeps its own connection pool, and 200 connections are
  plenty. `postgres-ro` reaches the replica.
- The pantry: one instance (no PDB, so node drains are never blocked),
  `postgres-pantry-rw.postgresql.svc:5432`, 100Gi.
- Settings for the NAS's HDD mirror: 30-minute checkpoints, `max_wal_size`
  16GB, WAL compression. `shared_buffers` is 512MB and the memory request
  512Mi so the replica fits on homelab2 (7 GB); the limit is 3Gi.

## Roles, databases, passwords

Every role is a `DatabaseRole` and every database a `Database`, one file per
app in `apps/production/postgresql/databases/` (main cluster). A role that
exists in both clusters has one `DatabaseRole` per cluster and one password
Secret. A `Database`'s or `DatabaseRole`'s
cluster cannot change; moving one means recreating it (Flux `force`), and the
old copy stays in the old cluster (reclaim policy `retain`) until it is
dropped by hand.

Nobody types a password. In each `<app>.yaml` a `PushSecret` takes a fresh
40-character password from the `Password` generator `role-password` and writes
it to OpenBao at `secret/postgresql/<role>-db`, only if that key does not exist
(`updatePolicy: IfNotExists`). It writes through the namespace's own
`SecretStore` `openbao-passwords` (`passwords.yaml`), signed in as the
ServiceAccount `postgresql-passwords`; OpenBao's role of that name may only
create and read `secret/postgresql/*` (`apps/production/openbao/README.md`).
The ExternalSecret `<role>-db` reads it back twice: here, as the
`DatabaseRole`'s `passwordSecret`, and in the app's namespace, where the
template builds a `DATABASE_URL` if the app wants one. The superuser's
password follows the same path (`superuser.yaml`, `postgres-superuser-secret.yaml`).

Adding a database:

1. Copy `databases/vikunja.yaml` and `databases/vikunja-db-secret.yaml`, rename, list
   both in `databases/kustomization.yaml`. For the pantry, use
   `cluster: postgres-pantry`.
2. In the app's namespace add an ExternalSecret `<role>-db` reading
   `postgresql/<role>-db` (copy `apps/production/vikunja/vikunja-db-secret.yaml`) and
   point the app at `postgres-rw.postgresql.svc.cluster.local` (pantry:
   `postgres-pantry-rw`).

Rotating a password: delete the key in OpenBao
(`bao kv metadata delete secret/postgresql/<role>-db`). Within the hour the
PushSecret writes a new one, ESO copies it (10 minutes), CloudNativePG changes
the role and Reloader restarts the apps that read the Secret.

Large test databases may commit asynchronously: `ALTER DATABASE ... SET
synchronous_commit = off` on the database itself (a `Database` has no
parameters; backups keep it). A crash can lose the last ~0.6 s of commits
there, never corrupt data.

## Superuser access

```sh
primary=$(kubectl -n postgresql get cluster postgres -o jsonpath='{.status.currentPrimary}')
kubectl -n postgresql exec -it "$primary" -c postgres -- psql      # peer login as postgres
```

## Failover and node maintenance

A drained node's primary is switched over to the other instance first (the
operator's PodDisruptionBudgets), so tuppr's reboots move the primary instead
of dropping it. A manual switchover:

```sh
kubectl -n postgresql patch cluster postgres --subresource=status --type=merge \
  -p '{"status":{"targetPrimary":"postgres-2","phase":"Switchover in progress","phaseReason":"manual"}}'
```

Prefer the primary on homelab: homelab2 has 7 GB of RAM.

## Backups

Only the main cluster `postgres` is backed up; the pantry is not.

- WAL: the Barman Cloud plugin archives every segment (zstd) to the bucket
  `cnpg-backups` on SeaweedFS, a TrueNAS app at `http://10.0.0.203:8333`
  (`backups.yaml`, key `secret/shared/cnpg-backups-s3`,
  `truenas/apps/seaweedfs`).
- Base backups: `ScheduledBackup` `postgres-nightly` at 02:00 UTC (gzip,
  immediate checkpoint). The `ObjectStore` keeps 14 days: any point in time
  since the oldest kept base backup can be restored.
- Offsite: the CronJob `postgresql-backup-copy` (`backup-copy.yaml`) copies
  the bucket at 00:30 Berlin as plain files into `postgresql/` on the TrueNAS
  share `pool1/apps/db-dumps`, which the Cloud Sync task "Hetzner: database
  dumps" uploads, rclone-encrypted, at 00:45. It never deletes what the bucket
  drops: files leave the copy after 17 days (`KEEP_DAYS`, above the bucket's
  14), and only while a base backup from the last two days is there. A new or
  emptied bucket therefore never empties the copy. No `pg_dump`: the backups
  are the copy.

```sh
kubectl -n postgresql get backup                     # base backups and their phase
kubectl -n postgresql get cluster postgres -o jsonpath='{.status.firstRecoverabilityPoint}{"\n"}'
kubectl -n postgresql create -f - <<'EOF'            # a base backup now
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  generateName: postgres-manual-
spec:
  cluster:
    name: postgres
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
EOF
```

## Restore and point-in-time recovery

Restore into a new cluster next to the old one, check it, then move the apps
(or rebuild `postgres` from it). The recovery reads the base backup and replays
WAL up to the target time:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgres-restore
  namespace: postgresql
spec:
  instances: 1
  imageName: <cluster.yaml's imageName>
  postgresql:
    shared_preload_libraries: [vchord]
  bootstrap:
    recovery:
      source: origin
      recoveryTarget:
        targetTime: "2026-11-07 12:00:00+00"   # omit for the latest state
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          barmanObjectName: seaweedfs
          serverName: postgres
  storage:
    storageClass: freenas-iscsi-csi
    size: 200Gi
```

From Hetzner (SeaweedFS lost too): pull `/home/db-dumps/postgresql/` back with
a Cloud Sync PULL task, upload the files into any S3 bucket (`rclone copy
<folder> <remote>:cnpg-backups`), and point an `ObjectStore` at it; the layout
is Barman Cloud's own (`postgres/base`, `postgres/wals`).

Without a `plugins` entry the restored cluster does not archive into the same
path. To rebuild `postgres` itself after losing it, use the same `bootstrap`
and `externalClusters` in `cluster.yaml` (name `postgres`, its `plugins`
stanza with a new `serverName` parameter, so the new timeline does not mix
with the old WAL), and keep the roles and databases as they are: the
`DatabaseRole` and `Database` objects adopt what the restore brings back.
