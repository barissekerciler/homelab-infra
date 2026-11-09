"""Secrets come from OpenBao through ExternalSecrets; SOPS is left only for bootstrap."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ["infrastructure/cert-manager/cert-issuers", "infrastructure", "apps/production", "apps/dev",
          "apps/base/postgresql"]
# OpenBao's iSCSI volume needs democratic-csi first, so its driver configs
# cannot come from OpenBao. The Talos bundle is in Bitwarden (talos/README.md).
SOPS_FILES = {
    "infrastructure/democratic-csi/iscsi/driver-config-secret.yaml",
    "infrastructure/democratic-csi/nfs/driver-config-secret.yaml",
}
# One credential that many namespaces read keeps one OpenBao value.
SHARED_KEYS = {
    "ghcr-pull": "shared/ghcr-pull",
    # A SeaweedFS bucket key: the bucket's one consumer here, and SeaweedFS's
    # identities file on the NAS (truenas/apps/seaweedfs/config.py).
    "openbao-snapshots-s3": "shared/openbao-snapshots-s3",
    "cnpg-backups-s3": "shared/cnpg-backups-s3",
}


def tracked_yaml():
    files = subprocess.run(["git", "ls-files", "*.yaml"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", check=True).stdout.split()
    return [f for f in files if (ROOT / f).exists()]


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


@lru_cache
def role_password_keys():
    """A PostgreSQL role's generated password: one OpenBao value under
    postgresql/, read in namespace postgresql (the DatabaseRole) and by its app."""
    keys = {}
    for name in tracked_yaml():
        text = (ROOT / name).read_text(encoding="utf-8")
        if re.search(r"(?m)^kind: DatabaseRole$", text):
            for row in yaml.safe_load_all(text):
                if row and row["kind"] == "DatabaseRole":
                    secret = row["spec"]["passwordSecret"]["name"]
                    keys[secret] = f"postgresql/{secret}"
    return keys


def shared_keys():
    return {**SHARED_KEYS, **role_password_keys()}


def external_secret_files():
    for name in tracked_yaml():
        text = (ROOT / name).read_text(encoding="utf-8")
        if re.search(r"(?m)^kind: ExternalSecret$", text):
            yield name, text, yaml.safe_load(text)


def test_secret_files_are_named_secret_yaml():
    """A file whose objects are all ExternalSecrets or Secrets is <name>-secret.yaml."""
    secret_files = []
    for name in tracked_yaml():
        kinds = set(re.findall(r"(?m)^kind: (\S+)$", (ROOT / name).read_text(encoding="utf-8", errors="replace")))
        if kinds and kinds <= {"ExternalSecret", "Secret"}:
            secret_files.append(name)
    assert len(secret_files) >= 30
    assert [name for name in secret_files if not name.endswith("-secret.yaml")] == []


def test_only_bootstrap_files_are_sops_encrypted():
    encrypted = {name for name in tracked_yaml()
                 if re.search(r"(?m)^sops:$", (ROOT / name).read_text(encoding="utf-8", errors="replace"))}
    assert encrypted == SOPS_FILES


def test_only_democratic_csi_decrypts():
    decrypting = set()
    for manifest in (ROOT / "clusters/homelab").glob("*.yaml"):
        for row in yaml.safe_load_all(manifest.read_text(encoding="utf-8")):
            if row and row["kind"] == "Kustomization" and "decryption" in row["spec"]:
                decrypting.add(row["metadata"]["name"])
    assert decrypting == {"democratic-csi"}


def test_external_secrets_read_their_own_openbao_key():
    files = list(external_secret_files())
    assert len(files) >= 30
    for name, text, row in files:
        spec = row["spec"]
        assert spec["secretStoreRef"] == {"kind": "ClusterSecretStore", "name": "openbao"}, name
        assert spec["target"]["name"] == row["metadata"]["name"], name
        keys = {item["remoteRef"]["key"] for item in spec["data"]}
        assert len(keys) == 1, name
        [key] = keys
        assert key.endswith("/" + row["metadata"]["name"]), name
        if row["metadata"]["name"] in shared_keys():
            assert key == shared_keys()[row["metadata"]["name"]], name
        assert text.startswith(f"# Values: OpenBao secret/{key} (KV v2).\n"), name
        for item in spec["data"]:
            assert item["remoteRef"]["property"] == item["secretKey"], name
        if "template" in spec["target"]:
            # Without Merge the template would drop the fetched keys.
            assert spec["target"]["template"]["mergePolicy"] == "Merge", name


@pytest.mark.parametrize("path", ACTIVE)
def test_openbao_key_starts_with_the_secret_namespace(path):
    for row in rendered(path):
        if row["kind"] == "ExternalSecret":
            key = row["spec"]["data"][0]["remoteRef"]["key"]
            name = row["metadata"]["name"]
            assert key == shared_keys().get(name, f"{row['metadata']['namespace']}/{name}")


@pytest.mark.parametrize("path", ACTIVE)
def test_no_plain_secrets_in_git(path):
    assert not [row["metadata"]["name"] for row in rendered(path) if row["kind"] == "Secret"]


def test_role_passwords_are_generated_once_into_openbao():
    # The main cluster's roles (apps/production), the pantry's (apps/dev) and
    # the superuser (apps/base/postgresql).
    rows = rendered("apps/production") + rendered("apps/dev") + rendered("apps/base/postgresql")
    roles = [row for row in rows if row["kind"] == "DatabaseRole"]
    pushes = {row["metadata"]["name"]: row for row in rows if row["kind"] == "PushSecret"}
    assert len(roles) >= 5
    for role in roles + [{"spec": {"passwordSecret": {"name": "postgres-superuser"}}}]:
        secret = role["spec"]["passwordSecret"]["name"]
        push = pushes[secret]["spec"]
        # Written once; a later sync never replaces a password the apps use.
        assert push["updatePolicy"] == "IfNotExists", secret
        assert push["secretStoreRefs"] == [{"kind": "SecretStore", "name": "openbao-passwords"}], secret
        assert push["selector"]["generatorRef"]["kind"] == "Password", secret
        [item] = push["data"]
        assert item["match"]["remoteRef"] == {"remoteKey": f"postgresql/{secret}", "property": "password"}
