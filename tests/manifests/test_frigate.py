"""Frigate records the living room camera on either node and keeps 30 days of it."""

from functools import lru_cache
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

from test_home_sign_in import config_lines
from private_values import home, need, oauth2_proxy_namespace, oauth2_proxy_url


REPO_ROOT = Path(__file__).resolve().parents[2]
FRIGATE = REPO_ROOT / "apps" / "production" / "frigate"
INFRASTRUCTURE = REPO_ROOT / "infrastructure"
ADGUARD_CONFIG = INFRASTRUCTURE / "adguardhome" / "adguardhome-config.yaml"
PRUNE = "kustomize.toolkit.fluxcd.io/prune"
# Not kept: a new pod starts empty, and the NAS and the Storage Box keep the copies.
EMPTY_DIRS = ("config", "media")
# Hetzner's host keys are the same for every Storage Box, so no box name is pinned.
STORAGE_BOX_HOSTS = "*.your-storagebox.de"
# rclone reads both remotes from the environment; no rclone.conf exists.
RCLONE_ENV = {
    "RCLONE_CONFIG_HETZNER_TYPE": "sftp",
    "RCLONE_CONFIG_HETZNER_PORT": "23",
    "RCLONE_CONFIG_HETZNER_KNOWN_HOSTS_FILE": "/etc/uploader/known_hosts",
    "RCLONE_CONFIG_CRYPT_TYPE": "crypt",
    "RCLONE_CONFIG_CRYPT_REMOTE": "hetzner:data",
}
RCLONE_SECRET_KEYS = (
    "RCLONE_CONFIG_HETZNER_HOST",
    "RCLONE_CONFIG_HETZNER_USER",
    "RCLONE_CONFIG_HETZNER_KEY_PEM",
    "RCLONE_CONFIG_CRYPT_PASSWORD",
    "RCLONE_CONFIG_CRYPT_PASSWORD2",
)
# The uploader and the cleanup job read RCLONE_SECRET_KEYS from here; Frigate never does.
UPLOADER_SECRET = "frigate-uploader-secrets"
# The Storage Box allows 10 simultaneous connections. An rclone run over sftp
# needs up to transfers + checkers + 1 of them (rclone's sftp docs).
BOX_CONNECTION_LIMIT = 10


def render(path):
    output = subprocess.run(
        ["kustomize", "build", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
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


@lru_cache
def rendered():
    return render(FRIGATE)


@lru_cache
def infrastructure():
    return render(INFRASTRUCTURE)


def pod_spec():
    return resource(rendered(), "Deployment", "frigate")["spec"]["template"]["spec"]


def frigate_container(pod):
    return next(item for item in pod["containers"] if item["name"] == "frigate")


def mounted_volumes(pod, container):
    """Map each mount path of the container to the pod volume behind it."""
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    return {mount["mountPath"]: volumes[mount["name"]] for mount in container["volumeMounts"]}


def frigate_config():
    configmap = resource(rendered(), "ConfigMap", "frigate-config")
    return yaml.safe_load(configmap["data"]["config.yml"])


def uploader_container(pod):
    return next(item for item in pod["containers"] if item["name"] == "uploader")


def uploader_files():
    return resource(rendered(), "ConfigMap", "frigate-uploader")["data"]


def cleanup_pod():
    job = resource(rendered(), "CronJob", "frigate-cleanup")
    return job["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def rclone_containers():
    """The uploader sidecar and the cleanup job, which share one rclone setup."""
    (cleanup,) = cleanup_pod()["containers"]
    return {"uploader": (pod_spec(), uploader_container(pod_spec())), "cleanup": (cleanup_pod(), cleanup)}


def plain_env(container):
    return {item["name"]: item["value"] for item in container.get("env", []) if "value" in item}


def secret_env(container):
    return {
        item["name"]: item["valueFrom"]["secretKeyRef"]
        for item in container.get("env", [])
        if "valueFrom" in item
    }


def full_command(container):
    return " ".join(container.get("command", []) + container.get("args", []))


def test_namespace_is_retained():
    namespace = resource(rendered(), "Namespace", "frigate")
    assert namespace["metadata"]["annotations"][PRUNE] == "disabled"


def test_recordings_stay_on_the_node_not_truenas():
    rows = rendered()
    # On the node's own disk, not in memory.
    volumes = {volume["name"]: volume for volume in pod_spec()["volumes"]}
    for name in EMPTY_DIRS:
        assert "medium" not in volumes[name]["emptyDir"], name
    # A burglar who unplugs TrueNAS first must not stop the recording. Only
    # the NAS mirror (its own pod) and its volume may use TrueNAS storage.
    mirror_only = {"frigate-nas", "frigate-nas-mirror"}
    for row in rows:
        if row["metadata"]["name"] not in mirror_only:
            assert "freenas-" not in json.dumps(row), row["metadata"]["name"]


def test_frigate_runs_on_either_node_with_an_igpu():
    deployment = resource(rendered(), "Deployment", "frigate")
    spec = deployment["spec"]
    pod = spec["template"]["spec"]
    # The Service and the NetworkPolicy select app: frigate.
    assert spec["template"]["metadata"]["labels"]["app"] == "frigate"
    # Both nodes have an Intel iGPU and the Cudy DNAT admits both.
    assert "nodeSelector" not in pod
    assert "nodeAffinity" not in pod.get("affinity", {})
    frigate = frigate_container(pod)
    assert frigate["resources"]["requests"]["gpu.intel.com/i915"] == "1"
    assert frigate["resources"]["limits"]["gpu.intel.com/i915"] == "1"


def test_camera_pods_win_scheduling_on_the_full_node():
    # homelab's CPU requests are nearly full; recording must not wait for room.
    priority = resource(rendered(), "PriorityClass", "frigate-camera")
    assert priority["preemptionPolicy"] == "PreemptLowerPriority"
    assert priority["globalDefault"] is False
    assert pod_spec()["priorityClassName"] == "frigate-camera"
    assert mirror_pod()["priorityClassName"] == "frigate-camera"
    # The nightly cleanup can wait; it must not evict anything.
    assert "priorityClassName" not in cleanup_pod()


def test_config_is_copied_from_git_at_start():
    pod = pod_spec()
    copies = [item for item in pod["initContainers"] if item["name"] == "copy-config"]
    assert len(copies) == 1
    copy = copies[0]
    script = copy["command"][-1]
    assert "cp -L /etc/frigate-git/config.yml /config/config.yml" in script
    source = mounted_volumes(pod, copy)
    assert source["/etc/frigate-git"]["configMap"]["name"] == "frigate-config"
    # The copy lands on the same volume that Frigate reads as /config.
    target = mounted_volumes(pod, frigate_container(pod))
    assert source["/config"]["name"] == target["/config"]["name"] == "config"
    assert target["/media/frigate"]["name"] == "media"


def test_detects_with_yolov9_built_from_official_weights():
    """YOLOv9-c, the largest YOLOv9 the iGPU keeps up with; the bundled SSDLite saw people in jackets."""
    pod = pod_spec()
    names = [item["name"] for item in pod["initContainers"]]
    # The model exists before copy-config decides between it and SSDLite.
    assert names.index("export-model") < names.index("copy-config")
    data = yaml.safe_load((FRIGATE / "configmap.yaml").read_text())["data"]
    script = data["export-model.sh"]
    model = frigate_config()["model"]
    assert model["model_type"] == "yolo-generic"
    assert (model["width"], model["height"]) == (320, 320)
    assert f'm={model["path"]}' in script
    assert "--imgsz 320" in script
    assert "github.com/WongKinYiu/yolov9/releases/download/v0.1/yolov9-c-converted.pt" in script
    # A failed export must not keep Frigate from starting.
    assert script.rstrip().endswith("exit 0")


def test_falls_back_to_bundled_model_without_download(tmp_path):
    """No GitHub at start must not mean no detection."""
    pod = pod_spec()
    copy = next(item for item in pod["initContainers"] if item["name"] == "copy-config")
    data = yaml.safe_load((FRIGATE / "configmap.yaml").read_text())["data"]
    awk = re.search(r"awk '(.*?)' /etc/frigate-git/config.yml", copy["command"][-1], re.S).group(1)
    (tmp_path / "config.yml").write_text(data["config.yml"], newline="\n")
    (tmp_path / "model-fallback.yml").write_text(data["model-fallback.yml"], newline="\n")
    awk = awk.replace("/etc/frigate-git/model-fallback.yml", (tmp_path / "model-fallback.yml").as_posix())
    out = subprocess.run(
        ["awk", awk, str(tmp_path / "config.yml")], check=True, capture_output=True, text=True
    ).stdout
    swapped = yaml.safe_load(out)
    original = yaml.safe_load(data["config.yml"])
    assert swapped["model"]["path"] == "/openvino-model/ssdlite_mobilenet_v2.xml"
    assert {k: v for k, v in swapped.items() if k != "model"} == {
        k: v for k, v in original.items() if k != "model"
    }


def test_no_service_variables_in_the_frigate_pod():
    # Frigate substitutes FRIGATE_* variables into its config, and Kubernetes
    # would inject FRIGATE_SERVICE_HOST, FRIGATE_PORT and the like for the
    # frigate Service.
    assert pod_spec()["enableServiceLinks"] is False


def test_only_traefik_and_home_assistant_reach_frigate():
    # Frigate trusts the proxy headers and defaults to admin, so only Traefik
    # may connect to 8971. The Pi may play the password-protected restream on
    # 8554 and nothing else (not 5000, 1984 or 8555).
    policies = {row["metadata"]["name"]: row for row in rendered() if row["kind"] == "NetworkPolicy"}
    assert set(policies) == {"frigate", "frigate-nas-mirror"}
    policy = policies["frigate"]
    assert policy["metadata"]["namespace"] == "frigate"
    assert policy["spec"]["podSelector"] == {"matchLabels": {"app": "frigate"}}
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    assert policy["spec"]["ingress"] == [
        {
            "from": [
                {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}},
            ],
            "ports": [{"protocol": "TCP", "port": 8971}],
        },
        {
            # Home Assistant on the Pi plays the go2rtc restream for its live view.
            "from": [{"ipBlock": {"cidr": need("PI_IP") + "/32"}}],
            "ports": [{"protocol": "TCP", "port": 8554}],
        },
    ]


def test_restream_asks_remote_clients_for_a_password():
    # go2rtc lets loopback clients in without it, so Frigate keeps reading
    # 127.0.0.1; Home Assistant has to log in.
    config = frigate_config()
    assert config["go2rtc"]["rtsp"] == {
        "username": "{FRIGATE_RESTREAM_USER}",
        "password": "{FRIGATE_RESTREAM_PASSWORD}",
    }
    inputs = config["cameras"]["living_room"]["ffmpeg"]["inputs"]
    assert [item["path"] for item in inputs] == ["rtsp://127.0.0.1:8554/living_room"]


def test_only_the_restream_leaves_the_cluster():
    services = {row["metadata"]["name"]: row["spec"] for row in rendered() if row["kind"] == "Service"}
    # The restream leaves through the pod's host port, not a Service.
    assert set(services) == {"frigate", "frigate-nas-mirror"}
    assert services["frigate"].get("type", "ClusterIP") == "ClusterIP"
    assert services["frigate-nas-mirror"].get("type", "ClusterIP") == "ClusterIP"
    # The restream is the only host port.
    host_ports = [port for container in pod_spec()["containers"]
                  for port in container.get("ports", []) if "hostPort" in port]
    assert host_ports == [{"name": "rtsp", "containerPort": 8554, "hostPort": 8554, "protocol": "TCP"}]


def secret_keys(rows, name):
    # Values live in OpenBao; Git lists the keys in the ExternalSecret.
    return {item["secretKey"] for item in resource(rows, "ExternalSecret", name)["spec"]["data"]}


def test_every_config_placeholder_comes_from_frigate_secrets():
    configmap = resource(rendered(), "ConfigMap", "frigate-config")["data"]["config.yml"]
    placeholders = set(re.findall(r"\{(FRIGATE_[A-Z0-9_]+)\}", configmap))
    assert placeholders
    assert placeholders <= secret_keys(rendered(), "frigate-secrets")


def test_secrets_come_from_openbao_and_are_split():
    rows = rendered()
    # The uploader's SSH key and the obscured crypt password and salt.
    assert secret_keys(rows, UPLOADER_SECRET) == set(RCLONE_SECRET_KEYS)
    assert not [row for row in rows if row["kind"] == "Secret"]
    for name in ("frigate-secrets", UPLOADER_SECRET):
        assert resource(rows, "ExternalSecret", name)["spec"]["secretStoreRef"]["name"] == "openbao"
    # The {FRIGATE_*} placeholders in config.yml come from frigate-secrets.
    assert frigate_container(pod_spec())["envFrom"] == [{"secretRef": {"name": "frigate-secrets"}}]


def test_frigate_never_sees_the_off_site_credentials():
    # Frigate's UI and API run in this container; whoever controls it must not
    # get the key that can delete the off-site copy, or the crypt password.
    rows = rendered()
    secrets = {row["metadata"]["name"]: {item["secretKey"] for item in row["spec"]["data"]}
               for row in rows if row["kind"] == "ExternalSecret"}
    pod = pod_spec()
    for container in pod.get("initContainers", []) + [frigate_container(pod)]:
        name = container["name"]
        sourced = set()
        for source in container.get("envFrom", []):
            if "secretRef" in source:
                sourced |= secrets[source["secretRef"]["name"]]
            if "configMapRef" in source:
                sourced |= set(resource(rows, "ConfigMap", source["configMapRef"]["name"])["data"])
        for item in container.get("env", []):
            sourced.add(item["name"])
            ref = item.get("valueFrom", {}).get("secretKeyRef")
            if ref:
                sourced.add(ref["key"])
                assert ref["name"] != UPLOADER_SECRET, name
        assert not [key for key in sourced if key.startswith("RCLONE_")], name
        assert UPLOADER_SECRET not in json.dumps(container), name
    # Nor can it read the Secret as a file.
    assert UPLOADER_SECRET not in json.dumps(pod["volumes"])


def test_service_exposes_only_8971():
    service = resource(rendered(), "Service", "frigate")
    assert service["metadata"]["namespace"] == "frigate"
    assert service["spec"]["selector"] == {"app": "frigate"}
    assert service["spec"]["ports"] == [{"name": "http", "port": 8971, "targetPort": 8971}]


def test_ingress_uses_github_forward_auth():
    rows = rendered()
    ingress = resource(rows, "Ingress", "frigate")
    assert ingress["metadata"]["namespace"] == "frigate"
    annotations = ingress["metadata"]["annotations"]
    assert annotations["traefik.ingress.kubernetes.io/router.entrypoints"] == "websecure"
    assert annotations["traefik.ingress.kubernetes.io/router.tls"] == "true"
    # <namespace>-<middleware name>@kubernetescrd
    assert (
        annotations["traefik.ingress.kubernetes.io/router.middlewares"]
        == "frigate-frigate-auth@kubernetescrd"
    )
    assert [rule["host"] for rule in ingress["spec"]["rules"]] == [home("cam")]
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": "frigate", "port": {"number": 8971}}

    middleware = resource(rows, "Middleware", "frigate-auth")
    assert middleware["metadata"]["namespace"] == "frigate"
    # Frigate reads the signed-in user from these headers (config proxy.header_map).
    assert middleware["spec"] == {
        "forwardAuth": {
            "address": oauth2_proxy_url(),
            "authResponseHeaders": ["X-Auth-Request-User", "X-Auth-Request-Email"],
        },
    }
    header = frigate_config()["proxy"]["header_map"]["user"]
    assert header.lower() == middleware["spec"]["forwardAuth"]["authResponseHeaders"][0].lower()


def test_oauth2_proxy_sets_x_auth_request_headers():
    # There is one oauth2-proxy release per domain; this is the home domain's one.
    releases = [
        item
        for item in infrastructure()
        if item.get("kind") == "HelmRelease"
        and item["metadata"]["name"] == "oauth2-proxy"
        and item["metadata"].get("namespace") == oauth2_proxy_namespace()
    ]
    assert len(releases) == 1
    release = releases[0]
    assert config_lines(release)["set_xauthrequest"] == "true"
    # The sign-in callback (/oauth2/) must be served on the camera host too.
    assert home("cam") in release["spec"]["values"]["ingress"]["hosts"]


def test_cam_host_resolves_on_lan_and_tailscale():
    from adguard_rules import CLUSTER, resolve
    for client in ("local", "tailscale"):
        assert resolve(home("cam"), client) == CLUSTER[client], client


def test_uploader_reads_media_read_only():
    pod = pod_spec()
    uploader = uploader_container(pod)
    media = [mount for mount in uploader["volumeMounts"] if mount["mountPath"] == "/media/frigate"]
    assert [mount.get("readOnly") for mount in media] == [True]
    mounts = mounted_volumes(pod, uploader)
    assert mounts["/media/frigate"]["name"] == "media"
    assert mounts["/etc/uploader"]["configMap"]["name"] == "frigate-uploader"
    # Heartbeat and pause switch; an emptyDir survives container restarts.
    assert "emptyDir" in mounts["/state"]
    assert uploader["command"] == ["sh", "/etc/uploader/upload.sh"]
    # Frigate itself keeps writing the same volume.
    frigate = mounted_volumes(pod, frigate_container(pod))
    assert frigate["/media/frigate"]["name"] == "media"


def test_upload_loop_survives_failures_and_uploads_encrypted():
    script = uploader_files()["upload.sh"]
    # One failed run must not end the loop.
    assert not re.search(r"^\s*set -[a-z]*e", script, re.MULTILINE)
    # Both copies send the filtered tree to the crypt remote.
    copies = [line for line in script.splitlines() if line.strip().startswith("rclone copy")]
    assert len(copies) == 2
    for line in copies:
        assert "rclone copy /media/frigate crypt: " in line, line
        assert "--filter-from /etc/uploader/filter.txt" in line, line


def heartbeat_branches(script):
    """Split the if/else around the heartbeat into its success and failure branches."""
    lines = script.splitlines()
    start = next(index for index, line in enumerate(lines) if "rclone rcat hetzner:status/heartbeat" in line)
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    middle = lines.index(f"{indent}else", start)
    end = lines.index(f"{indent}fi", middle)
    return lines[start], lines[start + 1 : middle], lines[middle + 1 : end]


def test_upload_loop_backs_off_after_failed_runs():
    script = uploader_files()["upload.sh"]
    lines = [line.strip() for line in script.splitlines()]
    # Both copies record their exit status for the heartbeat test.
    for index, line in enumerate(lines):
        if line.startswith("rclone copy"):
            assert lines[index + 1] == "status=$?", line
    condition, success, failure = heartbeat_branches(script)
    # The heartbeat is written only after a good copy, and touched only
    # after the box accepted it.
    assert condition.strip().startswith('if [ "$status" -eq 0 ] && date +%s | rclone rcat')
    success = [line.strip() for line in success]
    assert success == ["touch /state/heartbeat", "failures=0", "sleep 5"]
    assert script.count("touch /state/heartbeat") == 1
    # A failed run waits min(5 * 2^n, 120) s for n failures in a row
    # (10, 20, 40, 80, 120, 120 ...), so a refused login cannot get the home
    # IP banned.
    failure = "\n".join(line.strip() for line in failure)
    assert "failures=$((failures + 1))" in failure
    assert "delay=$((5 * (1 << failures)))" in failure
    assert re.search(r'if \[ "\$failures" -ge 5 \]; then\s+delay=120\s', failure)
    assert 'sleep "$delay"' in failure


# Stands in for rclone, sleep and touch. Each "rclone copy" is logged; copy
# number n exits with $RESULT_n (default 0). The copy after $STOP_AFTER ends
# the loop.
FAKE_TOOLS = """
calls=0
rclone() {
  case "$1" in
    rcat) cat > /dev/null; return 0 ;;
    copy) ;;
    *) return 0 ;;
  esac
  calls=$((calls + 1))
  echo "$*" >> copies.log
  if [ "$calls" -gt "$STOP_AFTER" ]; then exit 0; fi
  eval "result=\\${RESULT_$calls:-0}"
  return "$result"
}
sleep() { :; }
touch() { :; }
"""


def upload_runs(tmp_path, results):
    """Runs upload.sh until one copy after `results`; returns each copy's --max-age."""
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("needs a POSIX sh")
    script = uploader_files()["upload.sh"]
    shebang, rest = script.split("\n", 1)
    (tmp_path / "upload.sh").write_text(f"{shebang}\n{FAKE_TOOLS}\n{rest}", encoding="utf-8", newline="\n")
    env = {**os.environ, "STOP_AFTER": str(len(results))}
    env.update({f"RESULT_{index}": str(code) for index, code in enumerate(results, 1)})
    subprocess.run([sh, "upload.sh"], cwd=tmp_path, env=env, check=True, timeout=30)
    copies = (tmp_path / "copies.log").read_text(encoding="utf-8").splitlines()
    assert len(copies) == len(results) + 1
    return [re.search(r"--max-age (\S+)", line).group(1) for line in copies]


def test_upload_loop_reconciles_only_hourly_while_runs_succeed(tmp_path):
    assert upload_runs(tmp_path, [0, 0, 0]) == ["72h", "15m", "15m", "15m"]


def test_upload_loop_retries_a_failed_reconcile(tmp_path):
    assert upload_runs(tmp_path, [1, 0]) == ["72h", "72h", "15m"]


def test_upload_loop_reconciles_after_a_failed_fast_run(tmp_path):
    # A run that fails for longer than 15 min would leave the files from the
    # start of the outage to the hourly reconcile; the next run catches up
    # on the last 72 h instead.
    assert upload_runs(tmp_path, [0, 1, 1, 0]) == ["72h", "15m", "72h", "72h", "15m"]


def test_remote_is_sub1_on_port_23_with_a_pinned_host_key():
    connections = 0
    for name, (pod, container) in rclone_containers().items():
        env = plain_env(container)
        assert {key: env.get(key) for key in RCLONE_ENV} == RCLONE_ENV, name
        # Only the uploader's Secret, not the camera or MQTT credentials.
        assert "envFrom" not in container, name
        assert secret_env(container) == {
            key: {"name": UPLOADER_SECRET, "key": key} for key in RCLONE_SECRET_KEYS
        }, name
        assert mounted_volumes(pod, container)["/etc/uploader"]["configMap"]["name"] == (
            "frigate-uploader"
        ), name
        connections += int(env["RCLONE_TRANSFERS"]) + int(env["RCLONE_CHECKERS"]) + 1
    # The uploader and the nightly cleanup may run at once, and Home
    # Assistant's heartbeat check takes one more connection.
    assert connections + 1 <= BOX_CONNECTION_LIMIT
    known_hosts = uploader_files()["known_hosts"]
    assert known_hosts.startswith(f"[{STORAGE_BOX_HOSTS}]:23 ")
    lines = known_hosts.splitlines()
    assert all(line.startswith(f"[{STORAGE_BOX_HOSTS}]:23 ") for line in lines)
    assert "ssh-ed25519" in [line.split()[1] for line in lines]


def test_uploader_restarts_when_heartbeat_is_stale():
    probe = uploader_container(pod_spec())["livenessProbe"]
    command = " ".join(probe["exec"]["command"])
    # find exits 0 when the file is old, so the probe tests its output.
    assert "find /state/heartbeat -mmin -30 | grep -q ." in command
    # A paused uploader skips cycles on purpose and is not restarted.
    assert "[ -e /state/pause ]" in command


def test_cleanup_keeps_30_days():
    job = resource(rendered(), "CronJob", "frigate-cleanup")
    spec = job["spec"]
    assert spec["concurrencyPolicy"] == "Forbid"
    # The Service and NetworkPolicy select app: frigate; the job's pod
    # must not become a Service endpoint.
    labels = spec["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    assert labels.get("app") != "frigate"
    (container,) = cleanup_pod()["containers"]
    command = full_command(container)
    assert "rclone delete crypt: --min-age 30d" in command
    assert "rclone rmdirs crypt: --leave-root" in command
    assert command.index("rclone delete") < command.index("rclone rmdirs")


def mirror_deployment():
    return resource(rendered(), "Deployment", "frigate-nas-mirror")


def mirror_pod():
    return mirror_deployment()["spec"]["template"]["spec"]


def mirror_container():
    (container,) = mirror_pod()["containers"]
    return container


def mirror_script():
    configmap = resource(rendered(), "ConfigMap", "frigate-nas-mirror")
    return configmap["data"]["nas-mirror.sh"]


def push_container(pod):
    return next(item for item in pod["containers"] if item["name"] == "nas-push")


def push_script():
    return resource(rendered(), "ConfigMap", "frigate-nas-push")["data"]["nas-push.sh"]


PUSH_ENV = {
    "RCLONE_CONFIG": "/dev/null",
    "RCLONE_CONFIG_NAS_TYPE": "webdav",
    "RCLONE_CONFIG_NAS_URL": "http://frigate-nas-mirror.frigate.svc.cluster.local:8080",
    # rclone serve webdav keeps modification times, which the 30-day cleanup reads.
    "RCLONE_CONFIG_NAS_VENDOR": "rclone",
}


def test_nas_mirror_is_a_separate_pod_on_either_node():
    # A NAS that is down leaves only this pod waiting for its mount; Frigate
    # keeps starting and recording.
    deployment = mirror_deployment()
    spec = deployment["spec"]
    pod = mirror_pod()
    assert "nodeSelector" not in pod
    assert "nodeAffinity" not in pod.get("affinity", {})
    # Not app: frigate, so the Frigate Service never routes to it and the
    # Traefik-only NetworkPolicy does not select it.
    assert spec["selector"]["matchLabels"] == {"app": "frigate-nas-mirror"}
    assert spec["template"]["metadata"]["labels"] == {"app": "frigate-nas-mirror"}
    # The Frigate pod mounts no claim at all, so an unreachable NAS cannot stop it.
    assert not [volume for volume in pod_spec()["volumes"] if "persistentVolumeClaim" in volume]


def test_nas_mirror_serves_the_share_to_the_frigate_pod_only():
    pod = mirror_pod()
    container = mirror_container()
    mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
    volumes = mounted_volumes(pod, container)
    assert set(mounts) == {"/nas", "/etc/nas-mirror"}
    # The copy is written here, so no readOnly.
    assert not mounts["/nas"].get("readOnly")
    assert volumes["/nas"]["persistentVolumeClaim"]["claimName"] == "frigate-nas"
    assert volumes["/etc/nas-mirror"]["configMap"]["name"] == "frigate-nas-mirror"
    assert container["command"] == ["sh", "/etc/nas-mirror/nas-mirror.sh"]
    assert container["ports"] == [{"name": "webdav", "containerPort": 8080, "protocol": "TCP"}]
    service = resource(rendered(), "Service", "frigate-nas-mirror")["spec"]
    assert service["selector"] == {"app": "frigate-nas-mirror"}
    assert service["ports"] == [{"name": "webdav", "port": 8080, "targetPort": 8080}]
    # The WebDAV server has no login.
    policy = resource(rendered(), "NetworkPolicy", "frigate-nas-mirror")["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app": "frigate-nas-mirror"}}
    assert policy["policyTypes"] == ["Ingress"]
    assert policy["ingress"] == [{
        "from": [{"podSelector": {"matchLabels": {"app": "frigate"}}}],
        "ports": [{"protocol": "TCP", "port": 8080}],
    }]


def test_nas_push_reads_media_read_only():
    pod = pod_spec()
    container = push_container(pod)
    mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
    volumes = mounted_volumes(pod, container)
    assert mounts["/media/frigate"]["readOnly"] is True
    assert volumes["/media/frigate"]["name"] == "media"
    # The filter is the uploader's own file, not a copy of it.
    assert volumes["/etc/uploader"]["configMap"] == {
        "name": "frigate-uploader", "items": [{"key": "filter.txt", "path": "filter.txt"}],
    }
    assert volumes["/etc/nas-push"]["configMap"]["name"] == "frigate-nas-push"
    # Its own heartbeat, not the uploader's.
    assert "emptyDir" in volumes["/state"]
    assert volumes["/state"]["name"] != mounted_volumes(pod, uploader_container(pod))["/state"]["name"]
    assert container["command"] == ["sh", "/etc/nas-push/nas-push.sh"]
    assert plain_env(container) == PUSH_ENV
    assert secret_env(container) == {}
    assert "envFrom" not in container


def test_nas_push_loop():
    script = push_script()
    # One failed run must not end the loop.
    assert not re.search(r"^\s*set -[a-z]*e", script, re.MULTILINE)
    # Plain files to the NAS only; the mirror pod cleans the NAS.
    assert "hetzner:" not in script
    assert "rclone delete" not in script
    copies = [line for line in script.splitlines() if line.strip().startswith("rclone copy")]
    assert len(copies) == 2
    for line in copies:
        assert "rclone copy /media/frigate nas: " in line, line
        assert "--filter-from /etc/uploader/filter.txt" in line, line


def push_branches(script):
    """Split the if/else on the copy's exit status into its success and failure branches."""
    lines = script.splitlines()
    beat = next(index for index, line in enumerate(lines) if line.strip() == "touch /state/heartbeat")
    start = max(
        index for index in range(beat) if lines[index].strip() == 'if [ "$status" -eq 0 ]; then'
    )
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    middle = lines.index(f"{indent}else", start)
    end = lines.index(f"{indent}fi", middle)
    return [line.strip() for line in lines[start + 1 : middle]], [
        line.strip() for line in lines[middle + 1 : end]
    ]


def test_nas_push_backs_off_after_failed_runs():
    script = push_script()
    lines = [line.strip() for line in script.splitlines()]
    # Both copies record their exit status for the test that follows.
    for index, line in enumerate(lines):
        if line.startswith("rclone copy"):
            assert lines[index + 1] == "status=$?", line
    success, failure = push_branches(script)
    # The heartbeat is touched only after a good copy.
    assert success == ["touch /state/heartbeat", "failures=0", "sleep 5"]
    assert script.count("touch /state/heartbeat") == 1
    # A failed copy waits min(5 * 2^n, 120) s for n failures in a row
    # (10, 20, 40, 80, 120, 120 ...), so a dead NAS cannot spin.
    failure = " ".join(failure)
    assert "failures=$((failures + 1))" in failure
    assert "delay=$((5 * (1 << failures)))" in failure
    assert re.search(r'if \[ "\$failures" -ge 5 \]; then\s+delay=120\s', failure)
    assert 'sleep "$delay"' in failure


def test_nas_mirror_serves_webdav_and_cleans_old_files():
    script = mirror_script()
    assert not re.search(r"^\s*set -[a-z]*e", script, re.MULTILINE)
    assert "rclone serve webdav /nas --addr :8080 &" in script
    # Only files older than 30 days leave the NAS.
    assert "rclone delete /nas --min-age 30d && rclone rmdirs /nas --leave-root" in script
    assert script.count("rclone delete") == 1
    # A stopped server ends the script, so the kubelet restarts the container.
    assert script.rstrip().endswith("exit 1")


def test_nas_push_and_mirror_restart_when_stuck():
    probe = push_container(pod_spec())["livenessProbe"]
    command = " ".join(probe["exec"]["command"])
    assert "find /state/heartbeat -mmin -30 | grep -q ." in command
    # Listing the share's root fails on a hung NAS mount too.
    probe = mirror_container()["livenessProbe"]
    assert probe["httpGet"] == {"path": "/", "port": "webdav"}


def test_nas_push_and_mirror_are_unprivileged():
    for name, container in (("mirror", mirror_container()), ("nas-push", push_container(pod_spec()))):
        assert secret_env(container) == {}, name
        assert "envFrom" not in container, name
        context = container["securityContext"]
        assert context["allowPrivilegeEscalation"] is False, name
        assert context["capabilities"] == {"drop": ["ALL"]}, name
        # The NAS dataset belongs to TrueNAS's apps user, as Immich's and
        # Jellyfin's data does; the NFS server squashes root.
        assert (context["runAsUser"], context["runAsGroup"]) == (568, 568), name
        assert context["runAsNonRoot"] is True, name
    # A local share: no remotes, no credentials.
    assert plain_env(mirror_container()) == {"RCLONE_CONFIG": "/dev/null"}


def test_the_pi_gets_the_lan_answers():
    # Home Assistant asks the Pi's AdGuard through Tailscale split DNS, from the
    # Pi's Tailscale address, and cannot reach the nodes' Tailscale addresses.
    clients = {client["name"]: client["ids"]
               for client in yaml.safe_load(ADGUARD_CONFIG.read_text(encoding="utf-8"))["clients"]["persistent"]}
    assert "{{ .tailscale_pi }}" in clients["local"]
    # The exact address wins over the Tailscale group's range, which covers it.
    pi = ipaddress.ip_address(need("TAILSCALE_PI_IP"))
    assert any(pi in ipaddress.ip_network(cidr, strict=False) for cidr in clients["tailscale"])


def test_home_names_answer_aaaa_with_an_empty_noerror():
    # The rewrites answer A only; an upstream NXDOMAIN for AAAA makes musl
    # (Home Assistant's image) drop the A answer as well.
    rules = yaml.safe_load(ADGUARD_CONFIG.read_text(encoding="utf-8"))["user_rules"]
    assert f"||{home()}^$dnstype=AAAA,dnsrewrite=NOERROR;;" in rules
