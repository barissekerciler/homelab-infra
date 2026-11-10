"""Nothing answers on the network that should not: the tunnel, NodePorts, open proxies and status ports."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def one(path, kind, name):
    rows = [row for row in rendered(path) if row["kind"] == kind and row["metadata"]["name"] == name]
    assert len(rows) == 1, f"expected one {kind}/{name} in {path}"
    return rows[0]


def ingress_from(policy):
    return [peer for rule in policy["spec"]["ingress"] for peer in rule["from"]]


def test_the_tunnel_never_serves_home_hosts():
    config = yaml.safe_load(one("infrastructure", "ConfigMap", "cloudflared")["data"]["config.yaml"])
    hostnames = [rule.get("hostname") for rule in config["ingress"]]
    # cloudflared matches "*.bariss.dev" by suffix, so the home block must come first.
    home = hostnames.index("*.home.bariss.dev")
    assert config["ingress"][home]["service"] == "http_status:404"
    assert home < hostnames.index("*.bariss.dev")
    deployment = one("infrastructure", "Deployment", "cloudflared")
    env = {row["name"]: row.get("value") for row in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    # debug logs the headers of all public traffic.
    assert env["CLOUDFLARED_LOGLEVEL"] == "info"


def test_no_service_answers_on_node_ports():
    for path in ("infrastructure", "apps/production", "apps/dev"):
        for row in rendered(path):
            if row["kind"] == "Service":
                assert row["spec"].get("type", "ClusterIP") == "ClusterIP", row["metadata"]["name"]


def test_passwordless_services_admit_only_their_clients():
    gluetun = one("apps/production", "NetworkPolicy", "gluetun")
    # Traefik by name; tenant namespaces by a label they set themselves, so
    # this repository names none of them.
    assert ingress_from(gluetun) == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}},
        {"namespaceSelector": {"matchLabels": {"home.bariss.dev/vpn-proxy-client": "true"}}},
    ]
    redis = one("apps/production", "NetworkPolicy", "redis")
    assert ingress_from(redis) == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "media"}}}]


def test_node_metrics_need_a_token():
    values = one("infrastructure", "HelmRelease", "kube-prometheus-stack")["spec"]["values"]["prometheus-node-exporter"]
    # Host network: without the proxy, 9100 answers anyone on the LAN.
    assert values["kubeRBACProxy"]["enabled"] is True
    monitor = values["prometheus"]["monitor"]
    assert monitor["scheme"] == "https" and monitor["bearerTokenFile"].endswith("serviceaccount/token")
    role = one("infrastructure", "Role", "node-exporter-metrics")
    [rule] = role["rules"]
    assert rule["verbs"] == ["get"] and rule["resourceNames"] == ["kube-prometheus-stack-prometheus-node-exporter"]
    binding = one("infrastructure", "RoleBinding", "node-exporter-metrics")
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "kube-prometheus-stack-prometheus",
                                    "namespace": "monitoring"}]


def test_vikunja_has_no_open_sign_up():
    values = one("apps/production", "HelmRelease", "vikunja")["spec"]["values"]
    config = yaml.safe_load(values["vikunja"]["configMaps"]["config"]["data"]["config.yml"])
    assert config["service"]["enableregistration"] is False
