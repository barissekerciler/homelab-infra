"""No workload is pinned to a node, so either node can run it when the other is down."""

from functools import lru_cache
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
# Every path a Flux Kustomization in clusters/homelab applies.
KUSTOMIZATIONS = ["apps/production", "apps/dev", "infrastructure", "infrastructure/configs"]
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "Job", "CronJob"}
# AdGuard answers DNS on homelab's LAN address with hostNetwork.
ALLOWED = {
    "adguard/adguardhome": "homelab",
}
PREFER_HOMELAB = [{"weight": 100, "preference": {"matchExpressions": [
    {"key": "kubernetes.io/hostname", "operator": "In", "values": ["homelab"]}]}}]


@lru_cache(maxsize=None)
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def pod_spec(row):
    if row["kind"] == "CronJob":
        return row["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return row["spec"]["template"]["spec"]


def pinned_node(spec):
    """The node a pod spec requires, or None."""
    if spec.get("nodeName"):
        return spec["nodeName"]
    if "kubernetes.io/hostname" in (spec.get("nodeSelector") or {}):
        return spec["nodeSelector"]["kubernetes.io/hostname"]
    required = (spec.get("affinity") or {}).get("nodeAffinity", {}).get(
        "requiredDuringSchedulingIgnoredDuringExecution", {})
    for term in required.get("nodeSelectorTerms", []):
        for expression in term.get("matchExpressions", []) + term.get("matchFields", []):
            if expression["key"] in ("kubernetes.io/hostname", "metadata.name"):
                return ",".join(expression.get("values", []))
    return None


def workloads():
    for path in KUSTOMIZATIONS:
        for row in rendered(path):
            if row["kind"] in WORKLOAD_KINDS:
                yield f'{row["metadata"].get("namespace", "")}/{row["metadata"]["name"]}', pod_spec(row)


def test_only_the_listed_workloads_name_a_node():
    pins = {name: pinned_node(spec) for name, spec in workloads() if pinned_node(spec)}
    assert pins == ALLOWED


def test_no_persistent_volume_is_tied_to_a_node():
    for path in KUSTOMIZATIONS:
        for row in rendered(path):
            if row["kind"] == "PersistentVolume":
                assert not row["spec"].get("nodeAffinity"), row["metadata"]["name"]


@pytest.mark.parametrize("release,path", [
    ("apps/production/immich/release.yaml", ["server", "defaultPodOptions"]),
    ("apps/production/media/jellyfin/release.yaml", []),
    ("apps/production/nextcloud/release.yaml", []),
])
def test_helm_releases_prefer_homelab_without_pinning(release, path):
    values = yaml.safe_load((ROOT / release).read_text())["spec"]["values"]
    for key in path:
        values = values[key]
    assert "nodeSelector" not in values
    assert pinned_node(values) is None
    assert values["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"] == PREFER_HOMELAB


def test_unpinned_workloads_prefer_the_large_node():
    for name, spec in workloads():
        if name in ALLOWED or name.startswith(("claude-", "flux-system/")):
            continue
        preferred = (spec.get("affinity") or {}).get("nodeAffinity", {}).get(
            "preferredDuringSchedulingIgnoredDuringExecution")
        if preferred is not None:
            assert preferred == PREFER_HOMELAB, name


def test_traefik_answers_on_every_node():
    values = yaml.safe_load((ROOT / "infrastructure/traefik/release.yaml").read_text())["spec"]["values"]
    # Talos has no ServiceLB: one pod per node holds the public ports as hostPorts.
    assert values["deployment"]["kind"] == "DaemonSet"
    # No NodePorts: chart 42 reads the type from service.spec and ignores
    # service.type (it then makes a LoadBalancer).
    assert values["service"]["spec"]["type"] == "ClusterIP"
    assert "type" not in values["service"]
    assert {name: port.get("hostPort") for name, port in values["ports"].items()} == {
        "web": 80, "websecure": 443, "submissions": 465, "imaps": 993}
