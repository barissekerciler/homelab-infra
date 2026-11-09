import subprocess
import unittest
from pathlib import Path

import yaml
from private_values import home, need


REPO_ROOT = Path(__file__).resolve().parents[2]


def render(path):
    output = subprocess.run(
        ["kustomize", "build", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(output) if item]


def resource(items, kind, name):
    matches = [
        item
        for item in items
        if item.get("kind") == kind
        and item.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}/{name}, found {len(matches)}")
    return matches[0]


class MailPrivateAccessManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mail = render(REPO_ROOT / "apps" / "production" / "mail")
        cls.infrastructure = render(REPO_ROOT / "infrastructure")

    def test_public_wireguard_accepts_only_server_smtp(self):
        deployment = resource(self.mail, "Deployment", "stalwart")
        init_containers = deployment["spec"]["template"]["spec"]["initContainers"]
        wireguard = next(item for item in init_containers if item["name"] == "wireguard")
        script = "\n".join(wireguard["args"])
        public_accepts = [
            line.strip()
            for line in script.splitlines()
            if "STALWART_WG_INPUT" in line
            and "-p tcp" in line
            and "-j ACCEPT" in line
        ]

        self.assertEqual(
            public_accepts,
            [
                "iptables -A STALWART_WG_INPUT -p tcp --dport 25 -j ACCEPT",
            ],
        )

    def test_private_routes_terminate_home_tls_and_reencrypt_to_stalwart(self):
        transport = resource(self.mail, "ServersTransportTCP", "stalwart-tls")
        self.assertEqual(
            transport["spec"],
            {
                "tls": {
                    "serverName": f"mx.{need('DOMAIN')}",
                    "insecureSkipVerify": False,
                },
            },
        )

        client_tls = resource(self.mail, "TLSOption", "stalwart-clients")
        self.assertEqual(
            client_tls["spec"],
            {"minVersion": "VersionTLS12"},
        )

        imap_client_tls = resource(
            self.mail, "TLSOption", "stalwart-imap-clients"
        )
        self.assertEqual(
            imap_client_tls["spec"],
            {
                "minVersion": "VersionTLS12",
                "alpnProtocols": ["imap"],
            },
        )

        expectations = {
            "stalwart-submissions": ("submissions", 465, "stalwart-clients"),
            "stalwart-imaps": ("imaps", 993, "stalwart-imap-clients"),
        }
        for name, (entrypoint, port, tls_option) in expectations.items():
            route = resource(self.mail, "IngressRouteTCP", name)
            self.assertEqual(route["spec"]["entryPoints"], [entrypoint])
            self.assertEqual(
                route["spec"]["tls"],
                {"options": {"name": tls_option}},
            )
            self.assertEqual(len(route["spec"]["routes"]), 1)
            rule = route["spec"]["routes"][0]
            self.assertEqual(rule["match"], f"HostSNI(`{home('mx')}`)")
            self.assertEqual(
                rule["services"],
                [
                    {
                        "name": "stalwart",
                        "port": port,
                        "tls": True,
                        "serversTransport": "stalwart-tls",
                    },
                ],
            )

    def test_traefik_defines_submission_and_imap_entrypoints(self):
        values = yaml.safe_load((REPO_ROOT / "infrastructure/traefik/release.yaml").read_text())["spec"]["values"]
        service_spec = values["service"]["spec"]
        self.assertEqual(service_spec["ipFamilyPolicy"], "SingleStack")
        self.assertEqual(service_spec["ipFamilies"], ["IPv4"])

        ports = values["ports"]
        for name, port in (("submissions", 465), ("imaps", 993)):
            self.assertEqual(
                ports[name],
                {
                    "port": port,
                    "exposedPort": port,
                    "hostPort": port,
                    "protocol": "TCP",
                    "expose": {"default": True},
                },
            )

    def test_split_dns_uses_private_home_name_for_mail_clients(self):
        adguard = next(
            item
            for item in self.infrastructure
            if item.get("kind") == "ConfigMap"
            and item.get("metadata", {}).get("name", "").startswith(
                "adguardhome-config-"
            )
        )
        config = yaml.safe_load(adguard["data"]["AdGuardHome.yaml"])
        rules = config["user_rules"]
        from adguard_rules import CLUSTER, resolve

        for client in ("local", "tailscale"):
            self.assertEqual(resolve(home("mx"), client), CLUSTER[client])
        # The public mx.<domain> is not rewritten.
        public = f"mx.{need('DOMAIN')}"
        self.assertFalse([rule for rule in rules if public in rule])
        self.assertEqual(resolve(public, "local"), set())


if __name__ == "__main__":
    unittest.main()
