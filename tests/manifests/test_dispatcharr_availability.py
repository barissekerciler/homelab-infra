"""Keep Dispatcharr available during host memory pressure and web startup."""

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "apps/production/media/dispatcharr/deployment.yaml"


class DispatcharrAvailabilityManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployments = {
            row["metadata"]["name"]: row
            for row in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8"))
            if row and row["kind"] == "Deployment"
        }

    def test_web_and_worker_prefer_the_large_host_and_share_a_node(self):
        for name in ("dispatcharr", "dispatcharr-celery"):
            with self.subTest(deployment=name):
                pod = self.deployments[name]["spec"]["template"]["spec"]
                self.assertNotIn("nodeSelector", pod)
                # dispatcharr-data-pvc is RWO; each pod matches the term itself,
                # so the first one may start on any node.
                term = pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
                self.assertEqual(term, [{
                    "labelSelector": {"matchExpressions": [
                        {"key": "app", "operator": "In", "values": ["dispatcharr"]},
                        {"key": "component", "operator": "In", "values": ["web", "celery"]},
                    ]},
                    "topologyKey": "kubernetes.io/hostname",
                }])

    def test_web_rollout_does_not_overlap_shared_proxy_state(self):
        self.assertEqual(
            self.deployments["dispatcharr"]["spec"].get("strategy"),
            {"type": "Recreate"},
        )

    def test_web_waits_for_django_before_startup_and_readiness_succeed(self):
        pod = self.deployments["dispatcharr"]["spec"]["template"]["spec"]
        web = next(row for row in pod["containers"] if row["name"] == "dispatcharr")

        for name in ("startupProbe", "readinessProbe"):
            with self.subTest(probe=name):
                probe = web[name]
                self.assertEqual(
                    probe.get("httpGet"),
                    {"path": "/api/core/version/", "port": "http"},
                )
                self.assertIsNone(probe.get("tcpSocket"))


if __name__ == "__main__":
    unittest.main()
