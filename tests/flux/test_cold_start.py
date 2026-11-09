"""An empty cluster converges: no Kustomization applies a custom resource before its CRD exists."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CLUSTER = REPO_ROOT / "clusters" / "homelab"


def kustomizations():
    specs = {}
    for path in sorted(CLUSTER.glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if document and document["kind"] == "Kustomization":
                specs[document["metadata"]["name"]] = document["spec"]
    return specs


@lru_cache
def kinds(path):
    output = subprocess.run(["kustomize", "build", path], cwd=REPO_ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True).stdout
    return {(row["apiVersion"].rpartition("/")[0], row["kind"])
            for row in yaml.safe_load_all(output) if row}


# CRDs that an earlier Kustomization installs: cert-manager's (the
# cert-manager Kustomization) and External Secrets' (external-secrets).
EARLY_CRDS = {"cert-manager.io": "cert-manager", "external-secrets.io": "external-secrets",
              "generators.external-secrets.io": "external-secrets"}


def from_chart(group):
    # Built-in APIs, Flux's CRDs (flux-system) and EARLY_CRDS exist before
    # infrastructure applies.
    return ("." in group and not group.endswith((".k8s.io", ".toolkit.fluxcd.io"))
            and group not in EARLY_CRDS)


def waits_for(name, specs):
    seen, todo = set(), [name]
    while todo:
        for dependency in specs[todo.pop()].get("dependsOn", []):
            if dependency["name"] not in seen:
                seen.add(dependency["name"])
                todo.append(dependency["name"])
    return seen


def test_infrastructure_applies_before_any_chart_crd_exists():
    # Flux dry-runs the whole path first; one unknown kind keeps the
    # HelmReleases that would install it from ever being created.
    custom = sorted(kind for kind in kinds("infrastructure") if from_chart(kind[0]))
    assert not custom, f"move {custom} to infrastructure/configs"


def test_later_kustomizations_wait_for_the_crds_they_use():
    specs = kustomizations()
    checked = {check["name"] for check in specs["infrastructure"]["healthChecks"]
               if check["kind"] == "CustomResourceDefinition"}
    used = set()
    for name, spec in specs.items():
        if "infrastructure" in waits_for(name, specs):
            used |= {f"{kind.lower()}s.{group}" for group, kind in kinds(spec["path"].removeprefix("./"))
                     if from_chart(group)}
    assert checked == used


def test_early_crds_are_installed_before_their_users_apply():
    specs = kustomizations()
    for name, spec in specs.items():
        for group, owner in EARLY_CRDS.items():
            if name == owner or not any(used == group for used, _ in kinds(spec["path"].removeprefix("./"))):
                continue
            assert owner in waits_for(name, specs), f"{name} uses {group} before {owner}"
    assert specs["external-secrets"].get("wait") is True
