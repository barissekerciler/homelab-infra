"""Umami: the dashboard behind the GitHub sign-in, only the tracker's two paths public, secrets generated into OpenBao."""

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
    [rule] = ingress["spec"]["rules"]
    hosts = find("HelmRelease", "oauth2-proxy", "infrastructure")["spec"]["values"]["ingress"]["hosts"]
    assert rule["host"] in hosts


def test_only_the_tracker_paths_are_public():
    route = find("IngressRoute", "umami-tracker")
    assert route["spec"]["entryPoints"] == ["web"]
    paths = {}
    for item in route["spec"]["routes"]:
        assert item["match"].startswith("Host(`blog.bariss.dev`) && Path(`")
        # Fixed, above the blog's own route (100): never ranked by rule length.
        assert item["priority"] == 200
        [middleware] = item["middlewares"]
        paths[item["match"].split("Path(`")[1].rstrip("`)")] = \
            find("Middleware", middleware["name"])["spec"]["replacePath"]["path"]
    assert paths == {"/p.js": "/script.js", "/api/e": "/api/send"}
    env = {row["name"]: row.get("value") for row in
           find("Deployment", "umami")["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["COLLECT_API_ENDPOINT"] == "/api/e"


def test_only_traefik_and_the_password_job_connect():
    policy = find("NetworkPolicy", "umami")
    [rule] = policy["spec"]["ingress"]
    assert rule["from"] == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}},
        {"podSelector": {"matchLabels": {"app": "umami-admin-password"}}},
    ]
    job = find("Job", "umami-admin-password")
    assert job["spec"]["template"]["metadata"]["labels"] == {"app": "umami-admin-password"}


def test_secrets_are_generated_once_into_the_namespaces_own_openbao_path():
    store = find("SecretStore", "openbao-app-secrets")["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert store["role"] == "app-secrets"
    pushes = [row for row in rendered("apps/production/umami") if row["kind"] == "PushSecret"]
    assert {row["metadata"]["name"] for row in pushes} == {"umami-app", "umami-admin"}
    for push in pushes:
        spec = push["spec"]
        assert spec["updatePolicy"] == "IfNotExists"
        assert spec["secretStoreRefs"] == [{"kind": "SecretStore", "name": "openbao-app-secrets"}]
        [item] = spec["data"]
        assert item["match"]["remoteRef"]["remoteKey"] == f"umami/{push['metadata']['name']}"
