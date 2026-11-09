import unittest
from pathlib import Path

import yaml


APP = Path(__file__).resolve().parents[2] / "apps/production/media/recyclarr"
SONARR_ANIME = "20e0fc959f1f1704bed501f23bdae76f"  # [Anime] Remux-1080p
RADARR_ANIME = "722b624f9af1e492284c4bc842153a38"  # [Anime] Remux-1080p


class EnvVar(str):
    pass


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!env_var", lambda loader, node: EnvVar(loader.construct_scalar(node)))


def documents(name):
    return [d for d in yaml.safe_load_all((APP / name).read_text()) if d]


def recyclarr_config():
    configmap = documents("configmap.yaml")[0]
    return yaml.load(configmap["data"]["recyclarr.yml"], Loader=Loader)


class RecyclarrTest(unittest.TestCase):
    def test_kustomization_lists_every_manifest(self):
        resources = documents("kustomization.yaml")[0]["resources"]
        self.assertEqual(sorted(resources), sorted(p.name for p in APP.glob("*.yaml") if p.name != "kustomization.yaml"))

    def test_media_includes_recyclarr(self):
        kustomization = APP.parent / "kustomization.yaml"
        self.assertIn("recyclarr", yaml.safe_load(kustomization.read_text())["resources"])

    def test_syncs_trash_anime_profiles(self):
        config = recyclarr_config()
        sonarr = config["sonarr"]["sonarr"]
        radarr = config["radarr"]["radarr"]
        self.assertEqual(sonarr["base_url"], "http://sonarr.media.svc.cluster.local:8989")
        self.assertEqual(radarr["base_url"], "http://radarr.media.svc.cluster.local:7878")
        self.assertIn(SONARR_ANIME, [p["trash_id"] for p in sonarr["quality_profiles"]])
        self.assertIn(RADARR_ANIME, [p["trash_id"] for p in radarr["quality_profiles"]])

    def test_api_keys_come_from_openbao(self):
        config = recyclarr_config()
        keys = {config["sonarr"]["sonarr"]["api_key"], config["radarr"]["radarr"]["api_key"]}
        self.assertTrue(all(isinstance(k, EnvVar) for k in keys), keys)

        secret = documents("recyclarr-secret.yaml")[0]
        self.assertEqual(secret["kind"], "ExternalSecret")
        self.assertEqual({item["secretKey"] for item in secret["spec"]["data"]}, keys)

        container = documents("cronjob.yaml")[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["envFrom"], [{"secretRef": {"name": secret["metadata"]["name"]}}])

    def test_runs_one_sync_per_job(self):
        cronjob = documents("cronjob.yaml")[0]
        self.assertEqual(cronjob["spec"]["concurrencyPolicy"], "Forbid")
        container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        self.assertTrue(container["image"].startswith("ghcr.io/recyclarr/recyclarr:"))
        self.assertIn("@sha256:", container["image"])
        self.assertEqual(container["args"][0], "sync")


if __name__ == "__main__":
    unittest.main()
