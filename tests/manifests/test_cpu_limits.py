"""Every active container has a CPU ceiling no larger than half of a four-core node."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
NODE_HALF_MILLICORES = 2000
# Containers allowed above half a node, with the reason.
CPU_CEILING_EXCEPTIONS: dict[str, int] = {}
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}

# Helm values paths that render a container, per active Flux HelmRelease.
HELM_RESOURCES = [
    ("infrastructure/cilium/release.yaml", ""),
    ("infrastructure/cilium/release.yaml", "operator"),
    ("infrastructure/cert-manager/controller/release.yaml", ""),
    ("infrastructure/cert-manager/controller/release.yaml", "cainjector"),
    ("infrastructure/cert-manager/controller/release.yaml", "webhook"),
    ("infrastructure/cert-manager/controller/release.yaml", "startupapicheck"),
    ("infrastructure/traefik/release.yaml", ""),
    ("infrastructure/flux-operator/release.yaml", ""),
    ("infrastructure/reloader/release.yaml", "reloader.deployment"),
    ("infrastructure/oauth2-proxy/home-bariss-dev/release.yaml", ""),
    ("apps/production/immich/release.yaml", "server.controllers.main.containers.main"),
    ("apps/production/immich/release.yaml", "machine-learning.controllers.main.containers.main"),
    ("apps/production/immich/release.yaml", "valkey.controllers.main.containers.main"),
    ("apps/production/media/jellyfin/release.yaml", ""),
    ("apps/production/vikunja/release.yaml", "vikunja"),
    ("apps/production/openbao/release.yaml", "server"),
    ("infrastructure/external-secrets/controller/release.yaml", ""),
    ("infrastructure/external-secrets/controller/release.yaml", "webhook"),
    ("infrastructure/external-secrets/controller/release.yaml", "certController"),
    ("apps/production/jenkins/release.yaml", "controller"),
    ("apps/production/jenkins/release.yaml", "controller.sidecars.configAutoReload"),
    ("apps/production/jenkins/release.yaml", "agent"),
    ("infrastructure/monitoring/kube-prometheus-stack/release.yaml", "prometheusOperator"),
    ("infrastructure/monitoring/kube-prometheus-stack/release.yaml",
     "prometheusOperator.prometheusConfigReloader"),
    ("infrastructure/monitoring/kube-prometheus-stack/release.yaml", "prometheus.prometheusSpec"),
    ("infrastructure/monitoring/kube-prometheus-stack/release.yaml", "kube-state-metrics"),
    ("infrastructure/monitoring/kube-prometheus-stack/release.yaml", "prometheus-node-exporter"),
    ("infrastructure/monitoring/kube-prometheus-stack/release.yaml", "prometheus-node-exporter.kubeRBACProxy"),
    ("infrastructure/monitoring/grafana/release.yaml", ""),
    ("infrastructure/monitoring/grafana/release.yaml", "sidecar"),
    ("infrastructure/cloudnative-pg/release.yaml", ""),
    ("infrastructure/cloudnative-pg/barman-cloud.yaml", ""),
] + [
    (f"infrastructure/democratic-csi/{driver}/release.yaml", key)
    for driver in ("iscsi", "nfs")
    for key in (
        "controller.driver", "controller.externalAttacher", "controller.externalProvisioner",
        "controller.externalResizer", "controller.externalSnapshotter",
        "node.driver", "node.driverRegistrar", "node.cleanup", "csiProxy",
    )
]


def millicores(value):
    value = str(value)
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def assert_bounded(resources, where):
    requests = (resources or {}).get("requests") or {}
    limits = (resources or {}).get("limits") or {}
    assert "cpu" in limits, f"{where} has no CPU limit"
    # Without an explicit request Kubernetes reserves the whole limit.
    assert "cpu" in requests, f"{where} has no CPU request"
    ceiling = CPU_CEILING_EXCEPTIONS.get(where, NODE_HALF_MILLICORES)
    assert millicores(requests["cpu"]) <= millicores(limits["cpu"]) <= ceiling, where


@lru_cache
def application_paths():
    """Paths the Flux Kustomizations in clusters/homelab apply, plus Flux itself."""
    paths = ["clusters/homelab/flux-system"]
    for manifest in (ROOT / "clusters/homelab").glob("*.yaml"):
        for row in yaml.safe_load_all(manifest.read_text(encoding="utf-8")):
            if row and row["kind"] == "Kustomization" and row["spec"]["sourceRef"]["kind"] == "GitRepository":
                paths.append(row["spec"]["path"].removeprefix("./"))
    return sorted(set(paths))


def helm_values(release_file):
    return yaml.safe_load((ROOT / release_file).read_text(encoding="utf-8"))["spec"]["values"]


def containers(path):
    result = subprocess.run(
        ["kustomize", "build", path], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=True,
    )
    for row in yaml.safe_load_all(result.stdout):
        if not row or row["kind"] not in WORKLOAD_KINDS:
            continue
        spec = row["spec"]
        if row["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        pod = spec["template"]["spec"]
        for key in ("initContainers", "containers"):
            for container in pod.get(key) or []:
                yield f"{path}: {row['kind']}/{row['metadata']['name']}/{container['name']}", container


def test_application_paths_are_discovered():
    assert {"apps/dev", "apps/production",
            "infrastructure", "infrastructure/democratic-csi"} <= set(application_paths())


@pytest.mark.parametrize("path", application_paths())
def test_rendered_containers_have_bounded_cpu(path):
    for where, container in containers(path):
        assert_bounded(container.get("resources"), where)


@pytest.mark.parametrize("values_file,key", HELM_RESOURCES)
def test_helm_components_have_bounded_cpu(values_file, key):
    node = helm_values(values_file)
    for part in filter(None, key.split(".")):
        node = node[part]
    assert_bounded(node.get("resources"), f"{values_file}: {key or 'root'}")


def test_media_is_not_pinned_or_bound_to_another_pods_node():
    # Both nodes mount NFS; the scheduler places media on either one.
    result = subprocess.run(
        ["kustomize", "build", "apps/production/media"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    )
    rows = {row["metadata"]["name"]: row for row in yaml.safe_load_all(result.stdout)
            if row and row["kind"] in ("Deployment", "CronJob")}
    spec = lambda name: (rows[name]["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                         if rows[name]["kind"] == "CronJob" else rows[name]["spec"]["template"]["spec"])
    for name in ("bazarr", "radarr", "rdt", "sonarr", "unpackerr", "seerr"):
        assert not spec(name).get("nodeSelector"), name
        assert "nodeAffinity" not in spec(name).get("affinity", {}), name
    # seerr's RWO volume detaches before the new pod starts.
    assert rows["seerr"]["spec"]["strategy"] == {
        "type": "RollingUpdate", "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1}}
    # sonarr-stalled-requeue talks to sonarr and rdt over HTTP, so nothing
    # ties them to one node.
    for name in ("sonarr", "rdt", "sonarr-stalled-requeue"):
        assert not spec(name).get("nodeSelector"), name
        assert "podAffinity" not in spec(name).get("affinity", {}), name
    assert not any("persistentVolumeClaim" in v for v in spec("sonarr-stalled-requeue").get("volumes", []))


@pytest.mark.parametrize("driver", ["iscsi", "nfs"])
@pytest.mark.parametrize("pod", ["controller", "node"])
def test_csi_driver_image_stays_on_the_stable_release(driver, pod):
    # The chart defaults to the floating `latest` tag, which will move to a
    # release that drops TrueNAS 25.x support.
    values = helm_values(f"infrastructure/democratic-csi/{driver}/release.yaml")
    image = values[pod]["driver"]["image"]
    assert image["registry"] == "ghcr.io/democratic-csi/democratic-csi"
    assert re.fullmatch(r"v1\.9\.\d+@sha256:[0-9a-f]{64}", image["tag"])
