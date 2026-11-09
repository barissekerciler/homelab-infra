"""Prometheus and Grafana: no node pinning, data kept, Grafana with its own login."""

from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml
from private_values import home


ROOT = Path(__file__).resolve().parents[2]

@lru_cache
def rendered(path="infrastructure"):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def resource(kind, name, namespace="monitoring", path="infrastructure"):
    rows = [row for row in rendered(path) if row["kind"] == kind and row["metadata"]["name"] == name
            and row["metadata"].get("namespace") == namespace]
    assert len(rows) == 1, f"expected one {kind}/{namespace}/{name}, found {len(rows)}"
    return rows[0]


def test_namespace_survives_prune():
    assert resource("Namespace", "monitoring", None)["metadata"]["annotations"][
        "kustomize.toolkit.fluxcd.io/prune"] == "disabled"


def test_nothing_is_pinned_to_a_node():
    for name in ("kube-prometheus-stack", "grafana"):
        text = yaml.safe_dump(resource("HelmRelease", name)["spec"]["values"])
        for key in ("nodeSelector", "nodeName", "affinity", "kubernetes.io/hostname"):
            assert key not in text, f"{name} sets {key}"


def test_prometheus_keeps_control_plane_scrapes_off_and_data_on_iscsi():
    # Talos binds the controller manager, scheduler and etcd metrics to
    # localhost, and Cilium replaces kube-proxy, so none has a target.
    values = resource("HelmRelease", "kube-prometheus-stack")["spec"]["values"]
    for component in ("kubeControllerManager", "kubeScheduler", "kubeProxy", "kubeEtcd"):
        assert values[component] == {"enabled": False}, component
    spec = values["prometheus"]["prometheusSpec"]
    claim = spec["storageSpec"]["volumeClaimTemplate"]["spec"]
    assert claim["storageClassName"] == "freenas-iscsi-csi"
    assert values["grafana"]["forceDeployDatasources"] is True


def test_grafana_uses_its_own_login():
    values = resource("HelmRelease", "grafana")["spec"]["values"]
    assert "auth.proxy" not in values["grafana.ini"]
    assert values["admin"]["existingSecret"] == "grafana-admin"


def test_admin_secret_comes_from_openbao():
    body = yaml.safe_load((ROOT / "infrastructure/monitoring/grafana/admin-secret.yaml").read_text(encoding="utf-8"))
    assert body["kind"] == "ExternalSecret"
    assert body["spec"]["secretStoreRef"]["name"] == "openbao"


def test_grafana_host_resolves_on_lan_and_tailscale():
    from adguard_rules import CLUSTER, resolve
    for client in ("local", "tailscale"):
        assert resolve(home("grafana"), client) == CLUSTER[client]


def test_rules_that_never_fit_two_nodes_are_off():
    disabled = resource("HelmRelease", "kube-prometheus-stack")["spec"]["values"]["defaultRules"]["disabled"]
    assert {"KubeCPUOvercommit", "KubeMemoryOvercommit", "PrometheusNotConnectedToAlertmanagers"} <= set(disabled)


def test_homelab_dashboard_is_provisioned():
    config = resource("ConfigMap", "grafana-dashboards-homelab")
    assert config["metadata"]["labels"] == {"grafana_dashboard": "1"}
    dashboard = json.loads(config["data"]["homelab.json"])
    targets = [target for panel in dashboard["panels"] for target in panel.get("targets", [])]
    assert targets and all(target["datasource"] == {"type": "prometheus", "uid": "prometheus"}
                           for target in targets)
    # Volume usage comes from the kubelet job only; other jobs can repeat the series.
    for target in targets:
        if "kubelet_volume_stats" in target["expr"]:
            assert 'kubelet_volume_stats_used_bytes{job="kubelet"}' in target["expr"]


def test_dashboard_json_matches_its_generator(tmp_path):
    out = tmp_path / "homelab.json"
    subprocess.run([sys.executable, str(ROOT / "infrastructure/monitoring/dashboards/homelab.py"), str(out)],
                   check=True, capture_output=True, timeout=60)
    committed = (ROOT / "infrastructure/monitoring/dashboards/homelab.json").read_bytes()
    assert out.read_bytes() == committed, "run infrastructure/monitoring/dashboards/homelab.py"


def test_node_panels_pick_their_job():
    dashboard = json.loads(resource("ConfigMap", "grafana-dashboards-homelab")["data"]["homelab.json"])
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            expr = target["expr"]
            if re.search(r"node_(cpu|memory|filesystem|network|hwmon|thermal|load1|disk|zfs)", expr):
                # The NAS's node-exporter would otherwise show up as a third cluster node.
                assert 'job="node-exporter"' in expr or 'job="truenas"' in expr, (panel["title"], expr)


def test_truenas_is_scraped_without_per_dataset_series():
    scrape = resource("ScrapeConfig", "truenas", path="infrastructure/configs")["spec"]
    assert scrape["jobName"] == "truenas"
    assert {"sourceLabels": ["__name__"], "action": "drop", "regex": "node_zfs_zpool_dataset_.+"}         in scrape["metricRelabelings"]


ALERTING = ROOT / "infrastructure/monitoring/alerting"


def alerting_files():
    config = resource("ConfigMap", "grafana-alerting")
    assert config["metadata"]["labels"] == {"grafana_alert": "1"}
    return config["data"]


def alert_rules():
    [group] = json.loads(alerting_files()["rules.json"])["groups"]
    return group["rules"]


def test_grafana_loads_alerting_from_labelled_configmaps():
    values = resource("HelmRelease", "grafana")["spec"]["values"]
    assert values["sidecar"]["alerts"] == {"enabled": True, "label": "grafana_alert", "labelValue": "1"}
    assert set(alerting_files()) == {"rules.json", "contact-points.yaml", "policies.yaml", "templates.yaml"}


def test_alert_rules_json_matches_its_generator(tmp_path):
    out = tmp_path / "rules.json"
    subprocess.run([sys.executable, str(ALERTING / "alerts.py"), str(out)], check=True, capture_output=True,
                   timeout=60)
    assert out.read_bytes() == (ALERTING / "rules.json").read_bytes(), "run infrastructure/monitoring/alerting/alerts.py"


def test_alert_rules_query_prometheus_and_say_what_fired():
    rules = alert_rules()
    assert len({rule["uid"] for rule in rules}) == len(rules) >= 10
    for rule in rules:
        query, reduce, threshold = rule["data"]
        assert query["datasourceUid"] == "prometheus" and query["model"]["instant"] is True, rule["uid"]
        assert reduce["model"]["type"] == "reduce" and threshold["model"]["type"] == "threshold", rule["uid"]
        assert rule["condition"] == threshold["refId"], rule["uid"]
        assert rule["annotations"]["summary"] and rule["annotations"]["description"], rule["uid"]
        # The message template is HTML for Telegram.
        assert not re.search(r"[<>&]", "".join(rule["annotations"].values())), rule["uid"]
        if rule["uid"] == "prometheus-alerts":
            # Prometheus' own severity routes the forwarded alerts.
            assert rule["labels"] == {}
        else:
            assert rule["labels"]["severity"] in {"critical", "warning"}, rule["uid"]


def test_every_critical_prometheus_alert_is_forwarded():
    [forward] = [rule for rule in alert_rules() if rule["uid"] == "prometheus-alerts"]
    expr = forward["data"][0]["model"]["expr"]
    assert 'ALERTS{alertstate="firing",severity="critical"} or' in expr
    # Jenkins is watched: a stuck pod there means CI is stuck.
    assert 'severity="warning",namespace!~"claude-agents|.+-dev"' in expr
    assert forward["noDataState"] == "OK"


def test_backups_alert_when_their_metrics_are_missing():
    rules = {rule["uid"]: rule for rule in alert_rules()}
    for uid in ("postgres-backup-stale", "backup-job-late", "truenas-down", "prometheus-unreachable"):
        assert rules[uid]["noDataState"] == "Alerting", uid
        assert rules[uid]["labels"]["severity"] == "critical", uid


def test_critical_alerts_ring_and_warnings_arrive_silently():
    [policy] = yaml.safe_load(alerting_files()["policies.yaml"])["policies"]
    assert policy["receiver"] == "telegram-warning"
    assert policy["routes"] == [{"receiver": "telegram-critical", "object_matchers": [["severity", "=", "critical"]],
                                 "repeat_interval": "4h"}]
    points = {point["name"]: point["receivers"] for point in
              yaml.safe_load(alerting_files()["contact-points.yaml"])["contactPoints"]}
    assert set(points) == {"telegram-critical", "telegram-warning"}
    for name, [receiver] in points.items():
        assert receiver["type"] == "telegram"
        assert receiver["settings"]["message"] == '{{ template "telegram.message" . }}'
        assert receiver["settings"].get("disable_notifications", False) is (name == "telegram-warning")
    assert '{{ define "telegram.message" ' in alerting_files()["templates.yaml"]


def test_telegram_credentials_come_from_openbao():
    for receivers in [point["receivers"] for point in
                      yaml.safe_load(alerting_files()["contact-points.yaml"])["contactPoints"]]:
        settings = receivers[0]["settings"]
        assert settings["bottoken"] == "${TELEGRAM_BOT_TOKEN}"
        assert settings["chatid"] == "${TELEGRAM_CHAT_ID}"
    env = resource("HelmRelease", "grafana")["spec"]["values"]["envValueFrom"]
    assert env == {"TELEGRAM_BOT_TOKEN": {"secretKeyRef": {"name": "grafana-telegram", "key": "bot-token"}},
                   "TELEGRAM_CHAT_ID": {"secretKeyRef": {"name": "grafana-telegram", "key": "chat-id"}}}
    secret = resource("ExternalSecret", "grafana-telegram")["spec"]
    assert {item["secretKey"] for item in secret["data"]} == {"bot-token", "chat-id"}


def test_flux_leaves_grafanas_variables_alone():
    # postBuild substitution would replace ${TELEGRAM_BOT_TOKEN} before Grafana sees it.
    [spec] = [row["spec"] for row in yaml.safe_load_all((ROOT / "clusters/homelab/infrastructure.yaml")
                                                        .read_text(encoding="utf-8"))
              if row and row["metadata"]["name"] == "infrastructure"]
    assert spec["path"] == "./infrastructure" and "postBuild" not in spec


def test_reloader_restarts_everything_but_prometheus():
    # The rule files ConfigMap changes with every PrometheusRule; the
    # config-reloader loads it, a restart would take Prometheus down.
    values = resource("HelmRelease", "kube-prometheus-stack")["spec"]["values"]
    assert values["prometheus"]["annotations"] == {"reloader.stakater.com/auto": "false"}
    reloader = yaml.safe_load((ROOT / "infrastructure/reloader/release.yaml").read_text(encoding="utf-8"))
    assert reloader["spec"]["values"]["reloader"]["autoReloadAll"] is True
