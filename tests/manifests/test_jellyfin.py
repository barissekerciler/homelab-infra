"""Jellyfin runs the TrueNAS app's version on its copied config and the same library paths."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def values():
    return yaml.safe_load((ROOT / "apps/production/media/jellyfin/release.yaml").read_text())["spec"]["values"]


def test_keeps_the_truenas_user():
    v = values()
    context = v["podSecurityContext"]
    assert (context["runAsUser"], context["runAsGroup"]) == (568, 568)
    assert v["deploymentStrategy"] == {"type": "Recreate"}


def test_library_paths_match_the_truenas_mounts():
    v = values()
    claims = {row["name"]: row.get("persistentVolumeClaim", {}).get("claimName") for row in v["volumes"]}
    mounts = {row["mountPath"]: row["name"] for row in v["volumeMounts"]}
    assert claims["sonarr"] == "sonarr-nfs-content-pvc" and mounts["/sonarr"] == "sonarr"
    assert claims["radarr"] == "radarr-nfs-content-pvc" and mounts["/radarr"] == "radarr"
    assert mounts["/config/transcodes"] == mounts["/cache/transcodes"] == "transcodes"


def test_plays_dispatcharr_dvr_recordings_read_only():
    v = values()
    claims = {row["name"]: row.get("persistentVolumeClaim", {}) for row in v["volumes"]}
    mounts = {row["mountPath"]: row for row in v["volumeMounts"]}
    assert claims["dispatcharr-recordings"] == {"claimName": "dispatcharr-recordings-pvc", "readOnly": True}
    assert mounts["/recordings"] == {"name": "dispatcharr-recordings", "mountPath": "/recordings", "readOnly": True}


def test_prefers_homelab_quicksync_but_is_not_pinned():
    v = values()
    assert "nodeSelector" not in v
    preferred = v["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"]
    assert preferred[0]["preference"]["matchExpressions"][0]["values"] == ["homelab"]
    # The Intel device plugin grants the GPU; a hostPath device is blocked by the device cgroup.
    assert v["resources"]["limits"]["gpu.intel.com/i915"] == "1"
    # homelab's render group owns /dev/dri/renderD128.
    assert 993 in v["podSecurityContext"]["supplementalGroups"]


def test_config_and_cache_are_retained_iscsi_claims():
    v = values()
    assert v["persistence"]["config"]["existingClaim"] == "jellyfin-config"
    assert v["persistence"]["cache"]["existingClaim"] == "jellyfin-cache"
    claims = {row["metadata"]["name"]: row for row in yaml.safe_load_all(
        (ROOT / "apps/production/media/jellyfin/pvc.yaml").read_text())}
    for name in ("jellyfin-config", "jellyfin-cache"):
        assert claims[name]["spec"]["storageClassName"] == "freenas-iscsi-csi"
        assert claims[name]["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
