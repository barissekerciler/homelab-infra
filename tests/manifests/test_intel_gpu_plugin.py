"""homelab's iGPU is shared by the pods that need it."""

from pathlib import Path
import subprocess

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def render(path):
    output = subprocess.run(
        ["kustomize", "build", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [item for item in yaml.safe_load_all(output) if item]


def resource(items, kind, name):
    matches = [
        item
        for item in items
        if item.get("kind") == kind
        and item.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}/{name}, found {len(matches)}")
    return matches[0]


def test_two_pods_can_share_the_igpu():
    # Jellyfin and Frigate each request gpu.intel.com/i915: "1" on homelab.
    daemonset = resource(render(REPO_ROOT / "infrastructure"), "DaemonSet", "intel-gpu-plugin")
    plugin = next(
        item for item in daemonset["spec"]["template"]["spec"]["containers"]
        if item["name"] == "intel-gpu-plugin"
    )
    assert "-shared-dev-num=2" in plugin.get("args", [])
