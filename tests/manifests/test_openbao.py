"""OpenBao runs on raft on a pinned iSCSI volume, unseals itself from a hand-made Secret and snapshots to SeaweedFS on the NAS every night."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess

import yaml
from private_values import home, need, oauth2_proxy_url

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def rendered():
    result = subprocess.run(["kustomize", "build", "apps/production/openbao"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def find(kind, name):
    return next(row for row in rendered() if row["kind"] == kind and row["metadata"]["name"] == name)


def server():
    return find("HelmRelease", "openbao")["spec"]["values"]["server"]


def test_one_raft_server_on_the_pinned_iscsi_claim():
    values = server()
    assert values["ha"]["enabled"] and values["ha"]["replicas"] == 1
    assert values["ha"]["raft"]["enabled"]
    assert re.search(r'storage "raft" \{\s*path = "/openbao/data"', values["ha"]["raft"]["config"])
    assert values["dataStorage"]["storageClass"] == "freenas-iscsi-csi"
    # The StatefulSet's volumeClaimTemplate "data" adopts this claim by name.
    claim = find("PersistentVolumeClaim", "data-openbao-0")
    assert claim["spec"]["volumeName"].startswith("pvc-")
    assert values["dataStorage"]["size"] == claim["spec"]["resources"]["requests"]["storage"]


def test_static_seal_key_comes_from_a_secret_that_is_not_in_git():
    values = server()
    config = values["ha"]["raft"]["config"]
    assert 'current_key = "file:///openbao/userconfig/openbao-seal/key"' in config
    assert {"type": "secret", "name": "openbao-seal", "defaultMode": 288} in values["extraVolumes"]
    assert not [row for row in rendered() if row["kind"] == "Secret"]


def test_chart_bumps_restart_the_pod_and_node_drains_are_not_blocked():
    values = server()
    assert values["updateStrategyType"] == "RollingUpdate"
    assert values["ha"]["disruptionBudget"]["enabled"] is False


def test_ui_sits_behind_the_github_sign_in():
    ingress = server()["ingress"]
    assert [host["host"] for host in ingress["hosts"]] == [home("openbao")]
    assert ingress["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"] == \
        "openbao-openbao-auth@kubernetescrd"
    assert find("Middleware", "openbao-auth")["spec"]["forwardAuth"]["address"] == \
        oauth2_proxy_url()


def test_nightly_snapshot_goes_to_its_bucket_on_the_nas_through_the_chart_agent():
    agent = find("HelmRelease", "openbao")["spec"]["values"]["snapshotAgent"]
    assert agent["enabled"] and agent["schedule"] == "5 23 * * *"
    assert agent["config"]["s3Host"] == f"{need('NAS_IP')}:8333"
    assert agent["config"]["s3Bucket"] == "openbao-snapshots"
    assert agent["config"]["s3Uri"] == "s3://openbao-snapshots"
    assert agent["config"]["s3ExpireDays"] == "14"
    assert "--no-ssl" in agent["config"]["s3cmdExtraFlag"].split()
    # The key for that bucket only, from OpenBao itself.
    secret = find("ExternalSecret", agent["s3CredentialsSecret"])
    assert {item["secretKey"] for item in secret["spec"]["data"]} == {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
    # The chart's CronJob takes the snapshot; no hand-written one beside it,
    # and no post-renderer that suspends it.
    assert "postRenderers" not in find("HelmRelease", "openbao")["spec"]
    assert [row["metadata"]["name"] for row in rendered() if row["kind"] == "CronJob"] == \
        ["openbao-snapshot-copy"]
    assert not [row for row in rendered() if row["kind"] == "ServiceAccount"]


def test_snapshots_reach_hetzner_as_plain_files_through_the_db_dumps_share():
    job = find("CronJob", "openbao-snapshot-copy")
    # UTC like the chart's snapshot at 23:05, and before the 00:45 Berlin upload in winter.
    assert job["spec"]["schedule"] == "25 23 * * *" and job["spec"]["timeZone"] == "Etc/UTC"
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    [container] = pod["containers"]
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["RCLONE_CONFIG_SEAWEEDFS_ENDPOINT"] == f"http://{need('NAS_IP')}:8333"
    assert env["RCLONE_CONFIG_SEAWEEDFS_ENV_AUTH"] == "true"
    # The bucket's own key, the one the snapshot agent writes with.
    assert container["envFrom"] == [{"secretRef": {"name": "openbao-snapshots-s3"}}]
    script = container["args"][0]
    assert "rclone copy" in script and "rclone sync" not in script
    assert "seaweedfs:openbao-snapshots /dumps/openbao" in script
    # Nothing is pruned unless a fresh snapshot arrived.
    assert script.index("-mtime -2") < script.index("-delete")
    # The dump job's owner on the share the Hetzner task uploads.
    assert (pod["securityContext"]["runAsUser"], pod["securityContext"]["runAsGroup"]) == (999, 999)
    [volume] = pod["volumes"]
    claim = find("PersistentVolumeClaim", volume["persistentVolumeClaim"]["claimName"])
    pv = find("PersistentVolume", claim["spec"]["volumeName"])
    assert pv["spec"]["nfs"] == {"server": need("NAS_IP"), "path": f"{need('NAS_POOL')}/apps/db-dumps"}
    assert pv["spec"]["claimRef"] == {"namespace": "openbao", "name": claim["metadata"]["name"]}
