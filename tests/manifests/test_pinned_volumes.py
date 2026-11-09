"""Every claim names its volume and every volume names its claim, so a rebuilt cluster binds them again."""

from functools import lru_cache
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PATHS = ["apps/production", "apps/dev"]
PROVISIONED = {"freenas-iscsi-csi", "freenas-nfs-csi", "local-path"}


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


@pytest.mark.parametrize("path", PATHS)
def test_claims_are_pinned_to_volumes_in_git(path):
    rows = rendered(path)
    pvs = {row["metadata"]["name"]: row for row in rows if row["kind"] == "PersistentVolume"}
    claims = [row for row in rows if row["kind"] == "PersistentVolumeClaim"
              and row["spec"].get("storageClassName") in PROVISIONED]
    for claim in claims:
        name = f"{claim['metadata'].get('namespace')}/{claim['metadata']['name']}"
        assert claim["spec"].get("volumeName") in pvs, f"{name} has no volume in Git"
        assert claim["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled", name


@pytest.mark.parametrize("path", PATHS)
def test_volumes_are_retained_and_used(path):
    rows = rendered(path)
    used = {(row["metadata"].get("namespace"), row["spec"].get("volumeName"))
            for row in rows if row["kind"] == "PersistentVolumeClaim"}
    for pv in (row for row in rows if row["kind"] == "PersistentVolume"):
        name = pv["metadata"]["name"]
        assert pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain", name
        assert pv["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled", name
        claim = pv["spec"].get("claimRef")
        if "csi" in pv["spec"] or "local" in pv["spec"]:
            # Provisioned volumes would otherwise be free for any matching claim.
            assert claim, f"{name} has no claimRef"
        if claim:
            assert (claim["namespace"], name) in used, f"{name} points at a claim that does not use it"
        else:
            assert any(volume == name for _, volume in used), f"{name} is not used by a claim"


@pytest.mark.parametrize("path", PATHS + ["infrastructure"])
def test_iscsi_volumes_carry_the_session_timeout_secret(path):
    # democratic-csi writes the 30-minute iSCSI replacement timeout from this
    # node-stage secret; a static PV only gets it when it names the secret.
    for pv in (row for row in rendered(path) if row["kind"] == "PersistentVolume"):
        csi = pv["spec"].get("csi") or {}
        if csi.get("driver") == "org.democratic-csi.iscsi":
            assert csi.get("nodeStageSecretRef") == {
                "name": "node-stage-secret-freenas-iscsi-csi-democratic-csi-iscsi",
                "namespace": "democratic-csi-iscsi",
            }, pv["metadata"]["name"]


def test_cloudnative_pg_volumes_come_back_from_backups_not_from_git():
    # CloudNativePG provisions and names its instances' claims itself (one per
    # instance, new ones on every replica rebuild), so they cannot be pinned in
    # Git. A rebuilt cluster restores from the Barman Cloud backups instead:
    # the main cluster archives its WAL and has a nightly base backup. The
    # pantry (apps/dev/postgresql) has no backups on purpose.
    rows = rendered("apps/production") + rendered("apps/dev")
    clusters = [row for row in rows if row["kind"] == "Cluster"]
    assert sorted(row["metadata"]["name"] for row in clusters) == ["postgres", "postgres-pantry"]
    for cluster in (row for row in clusters if row["metadata"]["name"] == "postgres"):
        [plugin] = cluster["spec"]["plugins"]
        assert plugin["name"] == "barman-cloud.cloudnative-pg.io" and plugin["isWALArchiver"] is True
        assert cluster["spec"]["storage"]["storageClass"] == "freenas-iscsi-csi"
        assert any(row["kind"] == "ScheduledBackup" and row["spec"]["cluster"]["name"] == cluster["metadata"]["name"]
                   for row in rows)


def test_flux_never_prunes_the_postgresql_cluster():
    # Deleting a Cluster deletes its instances' claims (the volumes stay Retain,
    # but the cluster would come back empty).
    for cluster in (row for row in rendered("apps/production") + rendered("apps/dev") if row["kind"] == "Cluster"):
        assert cluster["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
