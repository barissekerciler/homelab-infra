"""The CloudNativePG cluster runs a pinned PostgreSQL 17 image with VectorChord for Immich."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGE = re.compile(
    r"^ghcr\.io/tensorchord/cloudnative-vectorchord:(?P<pg>\d+)\.\d+-(?P<vchord>[\d.]+)@sha256:[0-9a-f]{64}$"
)


@lru_cache
def rendered():
    result = subprocess.run(["kustomize", "build", "apps/production"], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def cluster():
    [row] = [row for row in rendered() if row["kind"] == "Cluster" and row["metadata"]["name"] == "postgres"]
    return row["spec"]


def version(text):
    return tuple(int(part) for part in text.split("."))


def test_image_is_pinned_and_in_immich_supported_ranges():
    match = IMAGE.match(cluster()["imageName"])
    assert match, cluster()["imageName"]
    # A major PostgreSQL upgrade is its own step.
    assert match["pg"] == "17"
    assert (0, 3) <= version(match["vchord"]) < (2, 0)


def test_vchord_is_preloaded_and_created_by_the_superuser():
    assert "vchord" in cluster()["postgresql"]["shared_preload_libraries"]
    [immich] = [row for row in rendered() if row["kind"] == "Database" and row["spec"]["name"] == "immich"]
    extensions = [row["name"] for row in immich["spec"]["extensions"]]
    assert {"vector", "vchord", "cube", "earthdistance"} <= set(extensions)
    # earthdistance needs cube first.
    assert extensions.index("cube") < extensions.index("earthdistance")


def test_two_instances_one_per_node():
    spec = cluster()
    assert spec["instances"] == 2
    assert spec["affinity"] == {"enablePodAntiAffinity": True, "topologyKey": "kubernetes.io/hostname",
                                "podAntiAffinityType": "required"}
    assert spec["storage"]["storageClass"] == "freenas-iscsi-csi"
