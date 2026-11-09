"""Give the non-root Dispatcharr worker a writable MEDIA_ROOT."""

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "apps/production/media/dispatcharr/deployment.yaml"


class DispatcharrMediaCacheManifestTest(unittest.TestCase):
    def test_celery_writes_m3u_and_epg_caches_under_app_media(self):
        # The web entrypoint creates /app/media as root; celery starts as uid
        # 1000 without it and cannot mkdir under the root-owned /app.
        celery = next(
            row
            for row in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8"))
            if row and row["metadata"]["name"] == "dispatcharr-celery"
        )
        pod = celery["spec"]["template"]["spec"]
        container = next(
            row for row in pod["containers"] if row["name"] == "dispatcharr-celery"
        )
        mount = next(
            row for row in container["volumeMounts"] if row["mountPath"] == "/app/media"
        )
        volume = next(row for row in pod["volumes"] if row["name"] == mount["name"])
        self.assertEqual(volume, {"name": mount["name"], "emptyDir": {}})


if __name__ == "__main__":
    unittest.main()
