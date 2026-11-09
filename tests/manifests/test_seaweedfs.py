"""SeaweedFS is a TrueNAS app that publishes only S3, each bucket key only reaches its own bucket, and the backup clients use it on the NAS."""

from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib

import yaml
from private_values import need

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "truenas/apps/seaweedfs"


@lru_cache
def values():
    return yaml.safe_load((APP / "values.yaml").read_text(encoding="utf-8"))


@lru_cache
def config():
    spec = importlib.util.spec_from_file_location("seaweedfs_config", APP / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def find(path, kind, name):
    return next(row for row in rendered(path) if row["kind"] == kind and row["metadata"]["name"] == name)


def endpoint():
    return f"http://{need('NAS_IP')}:8333"


def test_only_s3_is_published_on_the_lan_address():
    network = values()["network"]
    published = {name: port for name, port in network.items()
                 if isinstance(port, dict) and port.get("bind_mode")}
    assert list(published) == ["s3_port"]
    s3 = published["s3_port"]
    assert (s3["bind_mode"], s3["port_number"], s3["host_ips"]) == ("published", 8333, [need("NAS_IP")])


def test_all_state_lives_on_the_dataset_as_its_owner():
    v = values()
    assert v["run_as"] == {"user": 1000, "group": 1000}
    storage = v["storage"]
    base = f"{need('NAS_POOL')}/apps/seaweedfs"
    for part in ("master", "volume", "filer", "admin", "worker"):
        assert storage[f"{part}_data"] == {"type": "host_path", "host_path_config": {
            "acl_enable": False, "path": f"{base}/data/{part}"}}, part
    [extra] = storage["additional_storage"]
    assert extra["type"] == "host_path" and extra["read_only"] is True
    assert (extra["mount_path"], extra["host_path_config"]["path"]) == ("/etc/seaweedfs", f"{base}/config")
    flags = {(flag["container"], flag["flag"]): flag["value"] for flag in v["seaweedfs"]["additional_flags"]}
    assert flags[("s3", "config")] == "/etc/seaweedfs/s3.json"


def test_no_secret_in_the_app_definition():
    sw = values()["seaweedfs"]
    assert sw["admin_password"] == "<admin_password>" and sw["admin_user"] == "<admin_user>"


def test_each_bucket_identity_only_touches_its_own_bucket():
    module = config()
    key = {"AWS_ACCESS_KEY_ID": "a" * 20, "AWS_SECRET_ACCESS_KEY": "b" * 40}
    admin = {"admin_access_key_id": "c" * 20, "admin_secret_access_key": "d" * 40}
    identities = json.loads(module.s3_json(admin, {bucket: key for bucket in module.BUCKETS}))["identities"]
    found = {identity["name"]: identity for identity in identities}
    assert set(found) == set(module.BUCKETS) | {"admin"}
    for bucket in module.BUCKETS:
        actions = found[bucket]["actions"]
        assert actions and all(action.endswith(":" + bucket) for action in actions)
    # Each bucket key is the one its consumer in the cluster reads.
    assert module.BUCKETS == {"cnpg-backups": "shared/cnpg-backups-s3",
                              "openbao-snapshots": "shared/openbao-snapshots-s3"}


def test_jwt_keys_sign_volume_and_filer_access():
    module = config()
    names = ("jwt_signing_key", "jwt_signing_read_key", "jwt_filer_signing_key", "jwt_filer_signing_read_key")
    jwt = tomllib.loads(module.security_toml({name: name.upper() for name in names}))["jwt"]
    assert jwt["signing"]["key"] == "JWT_SIGNING_KEY"
    assert jwt["signing"]["read"]["key"] == "JWT_SIGNING_READ_KEY"
    assert jwt["filer_signing"]["key"] == "JWT_FILER_SIGNING_KEY"
    assert jwt["filer_signing"]["read"]["key"] == "JWT_FILER_SIGNING_READ_KEY"


def test_backup_clients_use_s3_on_the_nas():
    store = find("apps/production/postgresql", "ObjectStore", "seaweedfs")["spec"]["configuration"]
    assert store["endpointURL"] == endpoint()
    agent = find("apps/production/openbao", "HelmRelease", "openbao")["spec"]["values"]["snapshotAgent"]
    assert agent["config"]["s3Host"] == f"{need('NAS_IP')}:8333"
    for path, job in (("apps/production/postgresql", "postgresql-backup-copy"),
                      ("apps/production/openbao", "openbao-snapshot-copy")):
        cron = find(path, "CronJob", job)
        assert not cron["spec"].get("suspend"), job
        [container] = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        env = {item["name"]: item.get("value") for item in container["env"]}
        assert env["RCLONE_CONFIG_SEAWEEDFS_ENDPOINT"] == endpoint(), job


def test_nothing_of_seaweedfs_runs_in_the_cluster():
    assert not (ROOT / "apps/production/seaweedfs").exists()
    rows = rendered("apps/production") + rendered("infrastructure/sources")
    assert not [row for row in rows if "seaweedfs" in row["metadata"]["name"]
                and row["kind"] in {"HelmRelease", "HelmRepository", "Deployment", "StatefulSet"}]
