"""Every workload's requests follow its own usage (VPA + Goldilocks).

Goldilocks creates a VPA for each Deployment, StatefulSet and DaemonSet; the
namespace's annotation decides whether VPA applies it. The namespace-defaults
component sets it on every rendered Namespace, so none silently keeps its
hand-sized requests.
"""
from functools import lru_cache
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
# Every Flux Kustomization path (clusters/homelab); those that create Namespaces
# list the infrastructure/goldilocks/namespace-defaults component.
PATHS = sorted({line.split("path:", 1)[1].strip().removeprefix("./")
                for f in (ROOT / "clusters/homelab").glob("*.yaml")
                for line in f.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("path: ./")})
MODE = "goldilocks.fairwinds.com/vpa-update-mode"
POLICY = "goldilocks.fairwinds.com/vpa-resource-policy"
# Namespaces where VPA only recommends: VPA itself, and claude-agents, whose
# LimitRange keeps memory request = limit on purpose.
RECOMMEND_ONLY = {"vpa", "claude-agents"}


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def namespaces():
    found = {row["metadata"]["name"]: row for path in PATHS for row in rendered(path)
             if row["kind"] == "Namespace"}
    assert "cert-manager" in found and "media" in found, PATHS
    return found


@pytest.mark.parametrize("name", sorted(namespaces()))
def test_namespace_sets_the_vpa_mode(name):
    annotations = namespaces()[name]["metadata"].get("annotations") or {}
    assert MODE in annotations, f"Namespace {name} needs the {MODE} annotation"
    if name in RECOMMEND_ONLY:
        assert annotations[MODE] == "off"
        return
    assert annotations[MODE] == "Initial"
    # VPA sets requests only; limits stay as written in Git, with a floor for tiny pods.
    [policy] = json.loads(annotations[POLICY])["containerPolicies"]
    assert policy == {"containerName": "*", "controlledValues": "RequestsOnly",
                      "minAllowed": {"cpu": "10m", "memory": "32Mi"}}


def release(name, namespace):
    [row] = [row for row in rendered("infrastructure") if row["kind"] == "HelmRelease"
             and row["metadata"]["name"] == name and row["metadata"]["namespace"] == namespace]
    return row["spec"]["values"]


def test_recommender_keeps_small_floors():
    args = release("vpa", "vpa")["recommender"]["extraArgs"]
    # VPA's defaults (25m and 250 MB per pod) would raise most pods here.
    assert "--pod-recommendation-min-cpu-millicores=10" in args
    assert "--pod-recommendation-min-memory-mb=32" in args
    # History lives in VerticalPodAutoscalerCheckpoints, which survive restarts.
    assert not any(arg.startswith("--storage=") for arg in args)


def test_goldilocks_covers_every_namespace_behind_sign_in():
    values = release("goldilocks", "goldilocks")
    assert values["controller"]["flags"]["on-by-default"] is True
    ingress = values["dashboard"]["ingress"]
    assert ingress["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"] == \
        "goldilocks-goldilocks-auth@kubernetescrd"
