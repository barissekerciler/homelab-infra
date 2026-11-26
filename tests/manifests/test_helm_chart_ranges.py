"""Every HelmRelease takes a range of chart versions inside one major.

`N.x` admits the minors and patches of major N, so they arrive by themselves
and a new major is a manual change. A 0.x chart has no stable major: it takes
`0.N.x` (patches only) or `0.x` (every 0.x release), and the line above
`version:` says why.
"""

from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
MAJOR = re.compile(r"[1-9]\d*\.x")
ZERO = re.compile(r"0\.(?:[1-9]\d*\.)?x")


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
                yield f'{name} {row["metadata"]["name"]}', document, row["spec"]["chart"]["spec"]["version"]


def comment_above_version(document):
    lines = document.split("\n")
    index = next(i for i, line in enumerate(lines) if re.match(r"\s+version:", line))
    return re.fullmatch(r"\s+# \S.*", lines[index - 1]) is not None


def test_every_helm_release_takes_one_major():
    releases = list(helm_releases())
    assert releases
    wrong = {}
    for name, document, version in releases:
        if not isinstance(version, str) or not (MAJOR.fullmatch(version) or ZERO.fullmatch(version)):
            wrong[name] = version
        elif ZERO.fullmatch(version) and not comment_above_version(document):
            wrong[name] = f"{version} without a comment saying why"
    assert wrong == {}
