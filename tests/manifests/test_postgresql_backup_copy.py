"""The main cluster's own backups reach Hetzner as plain files through the TrueNAS db-dumps share."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml
from private_values import need

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def rendered():
    result = subprocess.run(["kustomize", "build", "apps/production/postgresql"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def find(kind, name):
    return next(row for row in rendered() if row["kind"] == kind and row["metadata"]["name"] == name)


def test_no_logical_dumps_beside_the_backups():
    # Barman Cloud's base backups and WAL are the backup; pg_dump is gone.
    assert [row["metadata"]["name"] for row in rendered() if row["kind"] == "CronJob"] == \
        ["postgresql-backup-copy"]


def test_copies_the_bucket_before_the_hetzner_upload():
    job = find("CronJob", "postgresql-backup-copy")
    # The TrueNAS Cloud Sync task for apps/db-dumps starts at 00:45.
    assert (job["spec"]["schedule"], job["spec"]["timeZone"]) == ("30 0 * * *", "Europe/Berlin")
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    [container] = pod["containers"]
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["RCLONE_CONFIG_SEAWEEDFS_ENDPOINT"] == f"http://{need('NAS_IP')}:8333"
    assert container["envFrom"] == [{"secretRef": {"name": "cnpg-backups-s3"}}]
    script = container["args"][0]
    # Copy, never mirror: a new or emptied bucket never drops older backups.
    assert "rclone copy --stats-one-line -v seaweedfs:cnpg-backups /dumps/postgresql" in script
    assert "rclone sync" not in script
    # Files leave only by age, and only while fresh base backups arrive.
    assert script.index("-name backup.info -mtime -2") < script.index("-mtime +$((KEEP_DAYS - 1)) -delete")
    assert "set -eu" in script
    # The 1 GB base backup in one stream, within the memory limit.
    assert env["RCLONE_MULTI_THREAD_STREAMS"] == "0"
    # Above the bucket's retention, or the next copy fetches pruned files again.
    retention = find("ObjectStore", "seaweedfs")["spec"]["retentionPolicy"]
    assert retention.endswith("d") and int(env["KEEP_DAYS"]) > int(retention[:-1]) + 1
    # The owner of the share, like OpenBao's snapshot copy (openbao/ beside postgresql/).
    assert (pod["securityContext"]["runAsUser"], pod["securityContext"]["runAsGroup"]) == (999, 999)
    [volume] = pod["volumes"]
    claim = find("PersistentVolumeClaim", volume["persistentVolumeClaim"]["claimName"])
    pv = find("PersistentVolume", claim["spec"]["volumeName"])
    assert pv["spec"]["nfs"] == {"server": need("NAS_IP"), "path": f"{need('NAS_POOL')}/apps/db-dumps"}
