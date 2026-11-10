"""Every HelmRelease links the values.yaml of the chart version it pins, on the line above `version:`."""

from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]


def helm_releases():
    files = subprocess.run(["git", "ls-files", "*.yaml"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", check=True).stdout.split()
    for name in files:
        path = ROOT / name
        if not path.exists() or "kind: HelmRelease" not in path.read_text(encoding="utf-8"):
            continue
        for document in re.split(r"^---$", path.read_text(encoding="utf-8"), flags=re.M):
            row = yaml.safe_load(document)
            if isinstance(row, dict) and row.get("kind") == "HelmRelease":
                yield f'{name} {row["metadata"]["name"]}', document, str(row["spec"]["chart"]["spec"]["version"])


def values_link(document, version):
    lines = document.split("\n")
    index = lines.index(f"      version: {version}")
    match = re.fullmatch(r"      # (https://\S+)", lines[index - 1])
    return match and match.group(1)


def test_every_helm_release_links_its_pinned_values():
    releases = list(helm_releases())
    assert releases
    wrong = {}
    for name, document, version in releases:
        link = values_link(document, version)
        if not link or not re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", link):
            wrong[name] = (version, link)
    assert wrong == {}
