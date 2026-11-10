import subprocess
import unittest
from pathlib import Path

import yaml
from private_values import home, need, oauth2_proxy_namespace


REPO_ROOT = Path(__file__).resolve().parents[2]


def render(path):
    output = subprocess.run(
        ["kustomize", "build", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(output) if item]


def resource(items, kind, name, namespace):
    matches = [
        item
        for item in items
        if item.get("kind") == kind
        and item.get("metadata", {}).get("name") == name
        and item.get("metadata", {}).get("namespace") == namespace
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}/{namespace}/{name}, found {len(matches)}")
    return matches[0]


def config_lines(release):
    return {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in release["spec"]["values"]["config"]["configFile"].splitlines()
        if "=" in line
    }


class HomeSignInManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.infrastructure = render(REPO_ROOT / "infrastructure")

    def test_only_the_owner_github_account_may_sign_in(self):
        release = resource(
            self.infrastructure, "HelmRelease", "oauth2-proxy", oauth2_proxy_namespace()
        )
        config = config_lines(release)
        self.assertEqual(config["provider"], '"github"')
        self.assertEqual(config["github_users"], f'[ "{need("GITHUB_USER")}" ]')
        self.assertEqual(config["upstreams"], '[ "static://202" ]')
        self.assertEqual(config["reverse_proxy"], "true")

    def test_sign_in_callback_is_served_on_every_protected_host(self):
        release = resource(
            self.infrastructure, "HelmRelease", "oauth2-proxy", oauth2_proxy_namespace()
        )
        ingress = release["spec"]["values"]["ingress"]
        self.assertEqual(ingress["path"], "/oauth2/")
        self.assertEqual(ingress["hosts"], [home(name) for name in (
            "cam", "agent-browser", "goldilocks", "openbao", "umami")])
        self.assertEqual(
            ingress["annotations"]["traefik.ingress.kubernetes.io/router.entrypoints"], "websecure"
        )

    def test_every_signed_in_host_resolves_on_lan_and_tailscale(self):
        # The cluster wildcard covers them unless a host is one of the Pi's names.
        from adguard_rules import CLUSTER, resolve

        release = resource(
            self.infrastructure, "HelmRelease", "oauth2-proxy", oauth2_proxy_namespace()
        )
        for host in release["spec"]["values"]["ingress"]["hosts"]:
            for client in ("local", "tailscale"):
                self.assertEqual(resolve(host, client), CLUSTER[client], f"{host} {client}")

    def test_oauth2_proxy_secret_comes_from_openbao(self):
        secret = resource(
            self.infrastructure, "ExternalSecret", "oauth2-proxy-secret", oauth2_proxy_namespace()
        )
        self.assertEqual({item["secretKey"] for item in secret["spec"]["data"]},
                         {"client-id", "client-secret", "cookie-secret"})
        self.assertEqual(secret["spec"]["secretStoreRef"]["name"], "openbao")


if __name__ == "__main__":
    unittest.main()
