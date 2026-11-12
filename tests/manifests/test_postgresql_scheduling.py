"""The replica finds room on the other node, and tuppr drains only while it is ready."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def find(path, kind, name):
    return next(row for row in rendered(path) if row["kind"] == kind and row["metadata"]["name"] == name)


def test_postgres_preempts_rather_than_waits():
    cluster = find("apps/production/postgresql", "Cluster", "postgres")
    assert cluster["spec"]["affinity"]["podAntiAffinityType"] == "required"
    priority = find("apps/production/postgresql", "PriorityClass", cluster["spec"]["priorityClassName"])
    assert priority["preemptionPolicy"] == "PreemptLowerPriority"
    assert priority["globalDefault"] is False
    assert priority["value"] > 0


def test_tuppr_waits_for_both_instances_and_may_read_them():
    checks = find("infrastructure/tuppr/upgrades", "TalosUpgrade", "cluster")["spec"]["healthChecks"]
    assert any(c["kind"] == "Cluster" and c["name"] == "postgres" and c["namespace"] == "postgresql"
               and "readyInstances" in c["expr"] for c in checks)
    role = find("apps/production/postgresql", "Role", "tuppr-health-check")
    assert {"get", "list", "watch"} <= set(role["rules"][0]["verbs"])
    binding = find("apps/production/postgresql", "RoleBinding", "tuppr-health-check")
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "tuppr", "namespace": "system-upgrade"}]
