"""Every image Flux deploys outside a chart's own defaults follows an ImagePolicy.

Container images and the image tags set in HelmRelease values carry an
`$imagepolicy` marker; the markers name policies that flux-system-apps takes
(label `image-automation: flux-system-apps`), and Jobs that follow a policy
are recreated on change.
"""

from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
AUTOMATION = ROOT / "infrastructure/image-update-automation"
MARKER = re.compile(r'#\s*\{"\$imagepolicy":\s*"flux-system:([a-z0-9-]+)(?::(tag|name|digest))?"\}')
IMAGE = re.compile(r"^\s*(?:- )?image:\s+(\S+)")
TAG = re.compile(r"^\s+tag:\s+\S")
LABEL = {"image-automation": "flux-system-apps"}
TUPPR = {"talos", "kubelet"}


def tracked(*patterns):
    files = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", check=True).stdout.split()
    return [ROOT / name for name in files if (ROOT / name).exists()]


def documents(path):
    return [row for row in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(row, dict)]


def policies():
    rows = {}
    for path in (AUTOMATION / "policies").glob("*.yaml"):
        for row in documents(path):
            if row.get("kind") == "ImagePolicy":
                rows[row["metadata"]["name"]] = row
    return rows


def repositories():
    return {row["metadata"]["name"]: row for row in documents(AUTOMATION / "sources.yaml")
            if row.get("kind") == "ImageRepository"}


def manifest_files():
    return tracked("apps/*.yaml", "infrastructure/*.yaml")


def test_every_image_line_has_a_marker():
    missing = []
    for path in manifest_files():
        if path.is_relative_to(AUTOMATION):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = IMAGE.match(line)
            # A shell variable in a script is not an image Flux deploys.
            if match and not match.group(1).startswith("$") and not MARKER.search(line):
                missing.append(f"{path.relative_to(ROOT)}:{number}")
            if path.name == "release.yaml" and TAG.match(line) and not MARKER.search(line):
                missing.append(f"{path.relative_to(ROOT)}:{number}")
    assert missing == []


def test_markers_name_policies_of_the_apps_automation():
    known = policies()
    wrong = []
    for path in manifest_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name, _ in MARKER.findall(line):
                if name in TUPPR:
                    continue
                if name not in known or known[name]["metadata"].get("labels", {}) != LABEL:
                    wrong.append(f"{path.relative_to(ROOT)}:{number} {name}")
    assert wrong == []


def test_policies_have_repositories_and_the_label():
    repos = repositories()
    wrong = {}
    for name, row in policies().items():
        labels = row["metadata"].get("labels", {})
        if row["spec"]["imageRepositoryRef"]["name"] not in repos:
            wrong[name] = "no ImageRepository"
        elif name in TUPPR and labels:
            wrong[name] = "tuppr's policies stay with flux-system-tuppr"
        elif name not in TUPPR and labels != LABEL:
            wrong[name] = labels
    assert wrong == {}


def test_apps_automation_selects_the_label():
    row = documents(AUTOMATION / "image-update-automation.yaml")[0]
    assert row["spec"]["policySelector"] == {"matchLabels": LABEL}


def test_latest_is_pinned_by_digest():
    loose = []
    for path in manifest_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = IMAGE.match(line)
            if match and re.search(r":latest$", match.group(1)):
                loose.append(f"{path.relative_to(ROOT)}:{number}")
    assert loose == []


def test_jobs_following_a_policy_are_forced():
    unforced = []
    for path in manifest_files():
        text = path.read_text(encoding="utf-8")
        if "kind: Job" not in text:
            continue
        for document in re.split(r"^---$", text, flags=re.M):
            row = yaml.safe_load(document)
            if isinstance(row, dict) and row.get("kind") == "Job" and MARKER.search(document):
                annotations = row["metadata"].get("annotations", {})
                if annotations.get("kustomize.toolkit.fluxcd.io/force") != "enabled":
                    unforced.append(f"{path.relative_to(ROOT)} {row['metadata']['name']}")
    assert unforced == []
