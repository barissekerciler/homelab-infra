"""Umami: the dashboard behind the GitHub sign-in, only the tracker's two paths public, APP_SECRET generated into OpenBao."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
OAUTH2_PROXY = "http://oauth2-proxy.oauth2-proxy-home-bariss-dev.svc.cluster.local/"


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def find(kind, name, path="apps/production/umami"):
    [row] = [row for row in rendered(path) if row["kind"] == kind and row["metadata"]["name"] == name]
    return row


def test_the_dashboard_is_behind_the_github_sign_in():
    ingress = find("Ingress", "umami")
    annotations = ingress["metadata"]["annotations"]
    assert annotations["traefik.ingress.kubernetes.io/router.entrypoints"] == "websecure"
    assert annotations["traefik.ingress.kubernetes.io/router.middlewares"] == "umami-umami-auth@kubernetescrd"
    assert find("Middleware", "umami-auth")["spec"]["forwardAuth"]["address"] == OAUTH2_PROXY


def test_only_the_tracker_paths_are_public():
    route = find("IngressRoute", "umami-tracker")
    assert route["spec"]["entryPoints"] == ["web"]
    [item] = route["spec"]["routes"]
    assert item["match"] == "Host(`umami.bariss.dev`) && (Path(`/p`) || Path(`/api/e`))"
    assert "middlewares" not in item
    env = {row["name"]: row.get("value") for row in
           find("Deployment", "umami")["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["TRACKER_SCRIPT_NAME"] == "p"
    assert env["COLLECT_API_ENDPOINT"] == "/api/e"
    # The tunnel itself answers 404 for every other path on that host.
    config = yaml.safe_load(find("ConfigMap", "cloudflared", "infrastructure")["data"]["config.yaml"])
    rules = [rule for rule in config["ingress"] if rule.get("hostname") == "umami.bariss.dev"]
    assert [(rule.get("path"), rule["service"]) for rule in rules] == [
        ("^/(p|api/e)$", "http://traefik.traefik.svc.cluster.local:80"),
        (None, "http_status:404"),
    ]


def test_only_traefik_connects():
    [rule] = find("NetworkPolicy", "umami")["spec"]["ingress"]
    assert rule["from"] == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}]


def test_app_secret_is_generated_once_into_its_openbao_path():
    store = find("SecretStore", "openbao-secrets")["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert store == {"mountPath": "kubernetes", "role": "umami-secrets",
                     "serviceAccountRef": {"name": "umami-secrets"}}
    push = find("PushSecret", "umami-app")["spec"]
    assert push["updatePolicy"] == "IfNotExists"
    assert push["secretStoreRefs"] == [{"kind": "SecretStore", "name": "openbao-secrets"}]
    [item] = push["data"]
    assert item["match"]["remoteRef"] == {"remoteKey": "umami/umami-app", "property": "APP_SECRET"}
