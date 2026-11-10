"""Immich and Nextcloud run as the TrueNAS apps did, on the TrueNAS data in place."""

from functools import lru_cache
from pathlib import Path
import subprocess

import pytest
import yaml
from private_values import need

ROOT = Path(__file__).resolve().parents[2]
# The CloudNativePG primary (apps/production/postgresql).
POSTGRES = "postgres-rw.postgresql.svc.cluster.local"


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def immich_values():
    return yaml.safe_load((ROOT / "apps/production/immich/release.yaml").read_text())["spec"]["values"]


@pytest.mark.parametrize("app,claims", [
    ("immich", {"immich-library": "apps/immich-secure/userdata"}),
    ("nextcloud", {"nextcloud-html": "apps/nextcloud-secure/appdata",
                   "nextcloud-data": "apps/nextcloud-secure/userdata"}),
])
def test_truenas_directories_are_retained_static_nfs_volumes(app, claims):
    rows = rendered(f"apps/production/{app}")
    pvs = {row["metadata"]["name"]: row for row in rows if row["kind"] == "PersistentVolume"}
    pvcs = {row["metadata"]["name"]: row for row in rows if row["kind"] == "PersistentVolumeClaim"}
    for claim, path in claims.items():
        pvc = pvcs[claim]
        pv = pvs[pvc["spec"]["volumeName"]]
        assert pv["spec"]["nfs"] == {"server": need("NAS_IP"), "path": f"{need('NAS_POOL')}/{path}"}
        assert pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
        for row in (pv, pvc):
            assert row["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
    for row in rows:
        if row["kind"] in ("Namespace", "PersistentVolumeClaim"):
            assert row["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"


def test_immich_keeps_the_truenas_user():
    values = immich_values()
    for part in ("server", "machine-learning"):
        context = values[part]["defaultPodOptions"]["securityContext"]
        assert (context["runAsUser"], context["runAsGroup"]) == (568, 568)
    # Both nodes mount NFS.
    assert "nodeSelector" not in values["server"]["defaultPodOptions"]


def test_machine_learning_survives_loading_the_clip_model():
    probes = immich_values()["machine-learning"]["controllers"]["main"]["containers"]["main"]["probes"]
    for name in ("liveness", "readiness"):
        spec = probes[name]["spec"]
        assert spec["timeoutSeconds"] >= 10, name
        # The SigLIP2 model takes minutes to load on one core.
        assert spec["periodSeconds"] * spec["failureThreshold"] >= 300, name


def test_immich_server_has_room_for_parallel_raw_thumbnails():
    memory = immich_values()["server"]["controllers"]["main"]["containers"]["main"]["resources"]["limits"]["memory"]
    # Six iPhone DNG decodes at once went past 4Gi and OOM-killed the server.
    assert memory.endswith("Gi") and float(memory[:-2]) > 4


def test_immich_server_is_replaced_not_surged():
    # homelab has no CPU request room for a surge pod, and thumbs is an RWO volume:
    # a RollingUpdate sat Pending until the Helm upgrade timed out.
    assert immich_values()["server"]["controllers"]["main"]["strategy"] == "Recreate"


def test_immich_machine_learning_is_replaced_not_surged():
    # The model cache is an RWO volume: a surge pod on the other node waits on
    # Multi-Attach while the old pod holds the volume.
    main = immich_values()["machine-learning"]["controllers"]["main"]
    assert main["strategy"] == "Recreate"
    assert "rollingUpdate" not in main


def test_immich_reaches_its_database_on_the_cluster_postgresql():
    env = immich_values()["controllers"]["main"]["containers"]["main"]["env"]
    assert (env["DB_HOSTNAME"], env["DB_USERNAME"], env["DB_DATABASE_NAME"]) == (POSTGRES, "immich", "immich")
    assert env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"] == {"name": "immich-db", "key": "password"}


def nextcloud_release():
    return yaml.safe_load((ROOT / "apps/production/nextcloud/release.yaml").read_text())["spec"]


def test_nextcloud_keeps_its_volumes_and_database():
    values = nextcloud_release()["values"]
    # Code on iSCSI, so the entrypoint's upgrade copy works; data stays on NFS.
    assert values["persistence"] == {"enabled": True, "existingClaim": "nextcloud-main"}
    mounts = {row["mountPath"]: row["name"] for row in values["nextcloud"]["extraVolumeMounts"]}
    claims = {row["name"]: row.get("persistentVolumeClaim", {}).get("claimName")
              for row in values["nextcloud"]["extraVolumes"]}
    assert claims[mounts["/var/www/html/data"]] == "nextcloud-data"
    assert values["nextcloud"]["datadir"] != "/var/www/html/data"
    database = values["externalDatabase"]
    assert (database["host"], database["database"]) == (f"{POSTGRES}:5432", "nextcloud")
    # User and password from the role's generated secret, not chart values.
    assert database["existingSecret"] == {"enabled": True, "secretName": "nextcloud-db",
                                          "usernameKey": "username", "passwordKey": "password"}
    assert "password" not in database and "valuesFrom" not in nextcloud_release()
    assert "nodeSelector" not in values

