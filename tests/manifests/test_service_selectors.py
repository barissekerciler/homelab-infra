"""No Service selects the pods of a Job or CronJob."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
# Every path a Flux Kustomization in clusters/homelab applies.
KUSTOMIZATIONS = ["apps/production", "apps/dev", "infrastructure", "infrastructure/configs"]


@lru_cache(maxsize=None)
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def job_pod_labels(row):
    template = (row["spec"]["jobTemplate"]["spec"]["template"] if row["kind"] == "CronJob"
                else row["spec"]["template"])
    return (template.get("metadata") or {}).get("labels") or {}


def test_job_pods_never_become_service_endpoints():
    # A running job pod with an app's labels joins its Service's endpoints and
    # refuses that app's connections while it runs.
    for path in KUSTOMIZATIONS:
        rows = rendered(path)
        services = [row for row in rows if row["kind"] == "Service" and row["spec"].get("selector")]
        for job in (row for row in rows if row["kind"] in ("Job", "CronJob")):
            labels = job_pod_labels(job)
            namespace = job["metadata"].get("namespace")
            for service in services:
                if service["metadata"].get("namespace") == namespace:
                    assert not service["spec"]["selector"].items() <= labels.items(), (
                        f'{job["metadata"]["name"]} matches Service {service["metadata"]["name"]}')
