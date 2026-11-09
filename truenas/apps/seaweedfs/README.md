# SeaweedFS on TrueNAS

S3 for the cluster's backups: CloudNativePG's Barman Cloud plugin (WAL and
base backups) and OpenBao's snapshot agent write to S3, and this is where.
It is the TrueNAS catalog app `seaweedfs` (train stable, app version 4.52),
installed by hand with the settings in `values.yaml`; TrueNAS Apps owns the
workload, Flux does not. MinIO's community edition is archived upstream and
ships no images, so this is SeaweedFS (Apache 2.0).

## Endpoint

`http://10.0.0.203:8333`, plain HTTP on the LAN. Path-style addressing; any
region name works (`us-east-1`).

| Bucket | Key (OpenBao, KV v2) | Used by |
| --- | --- | --- |
| `cnpg-backups` | `secret/shared/cnpg-backups-s3` | CloudNativePG (`apps/production/postgresql`) |
| `openbao-snapshots` | `secret/shared/openbao-snapshots-s3` | OpenBao's snapshot agent (`apps/production/openbao`) |

Each bucket key holds `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` and may
only read, list, write and tag its own bucket; it cannot create it. The admin
key (every bucket) is `secret/seaweedfs/seaweedfs-s3-config`
(`admin_access_key_id`, `admin_secret_access_key`); nothing in the cluster
reads it.

## Storage

Everything lives in the dataset `pool1/apps/seaweedfs` (its own encryption
root, refquota 200 GiB, owner 1000:1000, mode 700, no NFS share):

- `config/`: `security.toml` (the JWT keys) and `s3.json` (the S3
  identities), mode 600, mounted read-only at `/etc/seaweedfs` in every
  container. `config.py` writes both from OpenBao.
- `data/master`, `data/volume`, `data/filer`, `data/admin`, `data/worker`:
  one folder per container. The volume files, the filer's metadata and the
  master's state.

The app runs as 1000:1000. Volumes are 1024 MB, the form's minimum; their
number follows the free space. The master's vacuum reclaims deleted objects.

## Ports

The app runs one container per part (master, volume server, filer, S3
gateway, admin UI, maintenance worker) on its own Docker network. Only S3 is
published, on the LAN address. Everything else stays on that network:

- The master's HTTP admin calls (`/dir/assign`, `/col/delete`, `/vol/grow`)
  have no key check in SeaweedFS.
- The volume server and the filer take only reads and writes signed with the
  JWT keys (`secret/seaweedfs/seaweedfs-security`; tokens live 10 to 60
  seconds).
- gRPC between the containers is plain.
- The admin UI is not published; the login typed into the app form
  (`secret/seaweedfs/seaweedfs-admin`) only guards it inside the app.

In the cluster, the `postgresql` and `openbao` namespaces have no egress
policy and reach the NAS directly. The Claude box's policy blocks the whole
NAS, and `scripts/check-network-policies.sh` checks that 8333 stays blocked
from there.

## Install or rebuild

1. Create the dataset `pool1/apps/seaweedfs` as above, then `config` and the
   five `data/` folders, owner 1000:1000, mode 700.
2. Write the key files (OpenBao CLI signed in, `ssh truenas`):

   ```sh
   python truenas/apps/seaweedfs/config.py
   ```

3. Install SeaweedFS from Apps > Discover Apps (train stable) with the
   settings in `values.yaml`, and type the admin login into its two fields.
4. Create the buckets with the admin key:

   ```sh
   export RCLONE_CONFIG_SW_TYPE=s3 RCLONE_CONFIG_SW_PROVIDER=SeaweedFS RCLONE_CONFIG_SW_ENDPOINT=http://10.0.0.203:8333
   export RCLONE_CONFIG_SW_ACCESS_KEY_ID=$(bao kv get -mount=secret -field=admin_access_key_id seaweedfs/seaweedfs-s3-config)
   export RCLONE_CONFIG_SW_SECRET_ACCESS_KEY=$(bao kv get -mount=secret -field=admin_secret_access_key seaweedfs/seaweedfs-s3-config)
   rclone mkdir sw:cnpg-backups
   rclone mkdir sw:openbao-snapshots
   ```

5. Take a base backup and a snapshot at once
   (`apps/production/postgresql/README.md`, `apps/production/openbao/README.md`).

TrueNAS offers new app versions under Apps; update from there.

## Keys

A changed key (`bao kv patch`) reaches SeaweedFS when `config.py` runs again
and the app restarts (Apps > seaweedfs > Restart). A bucket key also reaches
its consumer through its ExternalSecret within 10 minutes.

Adding a bucket:

1. Put a key in OpenBao: `bao kv put secret/shared/<bucket>-s3
   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...` (random, 20 and 40
   characters).
2. Add the bucket to `BUCKETS` in `config.py`, run it, restart the app and
   create the bucket as in step 4.
3. Add an ExternalSecret `<bucket>-s3` in the consumer's namespace and the
   name to `SHARED_KEYS` in `tests/manifests/test_external_secrets.py`.

## Offsite copy and restore

The dataset has no Cloud Sync task: buckets stay on the NAS. The database
backups reach the Hetzner Storage Box as plain files through
`pool1/apps/db-dumps` instead: the CronJobs `postgresql-backup-copy` and
`openbao-snapshot-copy` copy their buckets there every night, and the Cloud
Sync task "Hetzner: database dumps" uploads it. Another bucket added here for
other backups therefore stays local unless a copy job is added for it.

Losing this dataset loses no backup older than the last nightly copy:
rebuild it empty (above), take a new base backup and snapshot, and restore
from `db-dumps` if needed (`apps/production/postgresql/README.md`).
