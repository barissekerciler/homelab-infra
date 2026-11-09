"""Jenkins checks the GitHub webhook signature, since /github-webhook/ is public through the tunnel."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JENKINS = ROOT / "apps/production/jenkins"


def jcasc():
    return yaml.safe_load((JENKINS / "jcasc.yaml").read_text(encoding="utf-8"))


def test_hook_secret_is_required_with_sha256():
    configs = jcasc()["unclassified"]["gitHubPluginConfig"]["hookSecretConfigs"]
    assert configs == [{"credentialsId": "github-webhook-secret", "signatureAlgorithm": "SHA256"}]


def test_secret_comes_from_openbao_and_stays_system_scoped():
    credentials = jcasc()["credentials"]["system"]["domainCredentials"][0]["credentials"]
    string = next(c["string"] for c in credentials if "string" in c)
    assert string["id"] == "github-webhook-secret"
    assert string["scope"] == "SYSTEM"
    assert string["secret"] == "${GITHUB_WEBHOOK_SECRET}"

    release = yaml.safe_load((JENKINS / "release.yaml").read_text(encoding="utf-8"))
    env = {e["name"]: e for e in release["spec"]["values"]["controller"]["containerEnv"]}
    assert env["GITHUB_WEBHOOK_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "jenkins-jcasc-secrets",
        "key": "github-webhook-secret",
    }
    secret = yaml.safe_load((JENKINS / "jenkins-jcasc-secret.yaml").read_text(encoding="utf-8"))
    assert secret["kind"] == "ExternalSecret"
    assert "github-webhook-secret" in {item["secretKey"] for item in secret["spec"]["data"]}
