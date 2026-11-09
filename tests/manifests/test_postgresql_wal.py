"""WAL and checkpoint settings keep sync writes on the NAS's HDD mirror down; WAL is archived."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess

import yaml
from private_values import need

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def rendered():
    result = subprocess.run(["kustomize", "build", "apps/production/postgresql"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def find(kind, name):
    return next(row for row in rendered() if row["kind"] == kind and row["metadata"]["name"] == name)


def mib(value):
    units = {"MB": 1, "Mi": 1, "GB": 1024, "Gi": 1024}
    match = re.fullmatch(r"(\d+)(MB|Mi|GB|Gi)", value)
    return int(match[1]) * units[match[2]]


def settings():
    return find("Cluster", "postgres")["spec"]["postgresql"]["parameters"]


def test_checkpoints_stay_timed_and_wal_is_compressed():
    assert settings()["checkpoint_timeout"] == "30min"
    # Requested checkpoints start at max_wal_size / 1.9; keep that above the
    # busiest 30 minutes measured in October 2026 (5.9 GB of all writes).
    assert mib(settings()["max_wal_size"]) / 1.9 > 5.9 * 1024
    assert settings()["wal_compression"] in {"lz4", "zstd"}
    # A lost replica's slot must not fill the 200Gi volume.
    assert mib(settings()["max_slot_wal_keep_size"]) < mib(find("Cluster", "postgres")["spec"]["storage"]["size"])


def test_memory_request_covers_shared_buffers():
    resources = find("Cluster", "postgres")["spec"]["resources"]
    assert mib(resources["requests"]["memory"]) >= mib(settings()["shared_buffers"])
    assert mib(resources["limits"]["memory"]) > mib(resources["requests"]["memory"])


def test_wal_is_archived_and_base_backups_run_nightly_for_14_days():
    [plugin] = find("Cluster", "postgres")["spec"]["plugins"]
    assert plugin == {"name": "barman-cloud.cloudnative-pg.io", "isWALArchiver": True,
                      "parameters": {"barmanObjectName": "seaweedfs"}}
    store = find("ObjectStore", "seaweedfs")["spec"]
    assert store["retentionPolicy"] == "14d"
    config = store["configuration"]
    assert config["destinationPath"] == "s3://cnpg-backups/"
    # SeaweedFS on the NAS (truenas/apps/seaweedfs).
    assert config["endpointURL"] == f"http://{need('NAS_IP')}:8333"
    # A spread checkpoint would hold a base backup back for 27 minutes.
    assert config["data"]["immediateCheckpoint"] is True
    schedule = find("ScheduledBackup", "postgres-nightly")["spec"]
    assert schedule["method"] == "plugin" and schedule["cluster"] == {"name": "postgres"}
    seconds, minute, hour, *rest = schedule["schedule"].split()
    assert rest == ["*", "*", "*"] and hour.isdigit()
    assert not schedule.get("suspend")
