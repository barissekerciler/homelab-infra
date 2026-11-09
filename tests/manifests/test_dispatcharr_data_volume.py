"""Keep Dispatcharr's /data (plugins, scripts, uploads, backups, logos) on its pinned disk."""

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "apps/production/media/dispatcharr/deployment.yaml"


class DispatcharrDataVolumeManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployments = {
            row["metadata"]["name"]: row
            for row in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8"))
            if row and row["kind"] == "Deployment"
        }

    def test_web_and_worker_share_the_pinned_data_claim(self):
        # Both pods are pinned to homelab, so they can share the RWO disk.
        for name in ("dispatcharr", "dispatcharr-celery"):
            with self.subTest(deployment=name):
                pod = self.deployments[name]["spec"]["template"]["spec"]
                data = next(row for row in pod["volumes"] if row["name"] == "data")
                self.assertEqual(
                    data, {"name": "data", "persistentVolumeClaim": {"claimName": "dispatcharr-data-pvc"}}
                )
                for container in pod["initContainers"] + pod["containers"]:
                    mounts = {row["mountPath"]: row["name"] for row in container["volumeMounts"]}
                    self.assertEqual(mounts["/data"], "data")
                    self.assertEqual(mounts["/data/recordings"], "recordings")

    def test_uid_1000_can_write_the_fresh_ext4_root(self):
        for name in ("dispatcharr", "dispatcharr-celery"):
            with self.subTest(deployment=name):
                context = self.deployments[name]["spec"]["template"]["spec"]["securityContext"]
                self.assertEqual(context["fsGroup"], 1000)


if __name__ == "__main__":
    unittest.main()
