"""Apps reach PostgreSQL at the CloudNativePG primary, each with its own role."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
PRIMARY = "postgres-rw.postgresql.svc"
PANTRY = "postgres-pantry-rw.postgresql.svc"
# Names of a PostgreSQL server and poolers that no longer exist.
GONE = ("postgresql.postgresql.svc", "pgbouncer.pgbouncer.svc", "pgbouncer:5432")


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def test_nothing_names_the_old_server_or_pgbouncer():
    offenders = [
        str(path.relative_to(ROOT))
        for base in ("apps", "infrastructure")
        for path in (ROOT / base).rglob("*.y*ml")
        if any(name in path.read_text(encoding="utf-8", errors="ignore") for name in GONE)
    ]
    assert offenders == []
    assert not (ROOT / "apps/production/pgbouncer").exists()


def test_every_role_password_reaches_an_app_namespace():
    # Each DatabaseRole's password Secret is read in namespace postgresql (for
    # CloudNativePG) and by at least one app, from the same OpenBao key.
    rows = rendered("apps/production") + rendered("apps/dev") + rendered("apps/base/postgresql")
    roles = {row["spec"]["passwordSecret"]["name"] for row in rows if row["kind"] == "DatabaseRole"}
    readers = {}
    for row in rows:
        if row["kind"] == "ExternalSecret" and row["metadata"]["name"] in roles:
            readers.setdefault(row["metadata"]["name"], set()).add(row["metadata"]["namespace"])
    for secret in roles:
        assert "postgresql" in readers[secret], secret
        assert readers[secret] - {"postgresql"}, f"no app reads {secret}"


def test_database_urls_point_at_the_primary():
    rows = rendered("apps/production") + rendered("apps/dev")
    urls = [row["spec"]["target"]["template"]["data"]["DATABASE_URL"] for row in rows
            if row["kind"] == "ExternalSecret" and "DATABASE_URL" in
            ((row["spec"]["target"].get("template") or {}).get("data") or {})]
    for url in urls:
        assert any(f"@{host}.cluster.local:5432/" in url for host in (PRIMARY, PANTRY)), url
        assert "{{ .password }}" in url, url


def test_only_expendable_databases_live_in_the_pantry():
    # Backups are per cluster: whatever matters stays in the main cluster,
    # whose WAL and base backups are kept 14 days. The main cluster and its
    # databases are in apps/production; the pantry (apps/dev) holds only
    # tenants' expendable databases, none from this repository.
    clusters = {path: {row["spec"]["name"]: row["spec"]["cluster"]["name"]
                       for row in rendered(path) if row["kind"] == "Database"}
                for path in ("apps/production", "apps/dev")}
    assert set(clusters["apps/production"].values()) == {"postgres"}
    assert clusters["apps/dev"] == {}
    assert [row["metadata"]["name"] for row in rendered("apps/dev")
            if row["kind"] == "Cluster"] == ["postgres-pantry"]
    rows = rendered("apps/production") + rendered("apps/dev")
    # A database's owner exists in the database's cluster.
    roles = {(row["spec"]["name"], row["spec"]["cluster"]["name"]) for row in rows if row["kind"] == "DatabaseRole"}
    for row in rows:
        if row["kind"] == "Database":
            assert (row["spec"]["owner"], row["spec"]["cluster"]["name"]) in roles, row["spec"]["name"]
