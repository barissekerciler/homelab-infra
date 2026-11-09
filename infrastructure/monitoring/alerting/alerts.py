"""Generate Grafana's alert rules.

    python infrastructure/monitoring/alerting/alerts.py

writes rules.json next to this file; Flux ships it in the grafana-alerting
ConfigMap, which Grafana's alert sidecar loads. tests/manifests/test_monitoring.py
checks that the JSON matches this script.

Every rule queries Prometheus (A), keeps the last value (B) and compares it
with a threshold (C). Annotations are Grafana templates: .Labels holds the
series' labels and .Values.B.Value the number. The severity label picks the
Telegram contact point (policies.yaml).
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("rules.json")
# kube-prometheus-stack provisions the datasource with this uid.
PROMETHEUS = {"type": "prometheus", "uid": "prometheus"}
EXPR = {"type": "__expr__", "uid": "__expr__"}
HOUR, DAY = 3600, 86400
# A nightly job whose last success is older than this missed a night.
NIGHTLY = 26 * HOUR
NAS = 'job="truenas"'
GRAFANA = "https://grafana.home.bariss.dev"

# Prometheus' own rules (kube-prometheus-stack) that reach Telegram: every
# critical one, and the warnings outside the namespaces where failing pods are
# routine (CI builds, the Claude box, the tenant's -dev apps). The node
# saturation warnings fire under normal load on these two nodes.
QUIET_NAMESPACES = "jenkins|claude-agents|.+-dev"
QUIET_ALERTS = "NodeSystemSaturation|NodeDiskIOSaturation|NodeMemoryMajorPagesFaults"
# Labels worth reading in a forwarded alert, in this order; the namespace is
# in its summary.
DETAIL_LABELS = ["deployment", "statefulset", "daemonset", "job_name", "cronjob", "persistentvolumeclaim",
                 "pod", "container", "node", "job"]


def rule(uid, title, expr, threshold, summary, description, severity=None, pending="5m",
         no_data="OK", exec_err="KeepLast", link=None):
    """One Grafana-managed rule. threshold is (type, *params), e.g. ("gt", 0)."""
    kind, *params = threshold
    return {
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            {"refId": "A", "relativeTimeRange": {"from": 600, "to": 0}, "datasourceUid": PROMETHEUS["uid"],
             "model": {"refId": "A", "datasource": PROMETHEUS, "expr": expr, "instant": True, "range": False}},
            {"refId": "B", "datasourceUid": EXPR["uid"],
             "model": {"refId": "B", "datasource": EXPR, "type": "reduce", "expression": "A", "reducer": "last"}},
            {"refId": "C", "datasourceUid": EXPR["uid"],
             "model": {"refId": "C", "datasource": EXPR, "type": "threshold", "expression": "B",
                       "conditions": [{"evaluator": {"type": kind, "params": params}}]}},
        ],
        # Missing series mean "nothing wrong" unless a rule says otherwise. A
        # failed query keeps the last state: "Prometheus unreachable" reports it.
        "noDataState": no_data,
        "execErrState": exec_err,
        "for": pending,
        "labels": {"severity": severity} if severity else {},
        # The message shows runbook_url once, under all of the rule's alerts.
        "annotations": {"summary": summary, "description": description} | ({"runbook_url": link} if link else {}),
    }


details = "".join(f"{{{{ with .Labels.{name} }}}}{name}={{{{ . }}}} {{{{ end }}}}" for name in DETAIL_LABELS)
forwarded = (
    'ALERTS{alertstate="firing",severity="critical"}'
    f' or ALERTS{{alertstate="firing",severity="warning",namespace!~"{QUIET_NAMESPACES}",'
    f'alertname!~"{QUIET_ALERTS}"}}'
)

rules = [
    rule("prometheus-unreachable", "Prometheus unreachable", "vector(1)", ("lt", 1),
         "Grafana cannot query Prometheus",
         "Every other alert is stuck in its last state until Prometheus answers again.",
         severity="critical", no_data="Alerting", exec_err="Alerting"),
    # Prometheus has no annotations in ALERTS, so the alert's name moves to the
    # label "alert" (Grafana keeps alertname for the rule's title), the
    # message lists the labels that say where it fired and links to the rule
    # in Grafana's alert list. The severity is Prometheus' own.
    rule("prometheus-alerts", "Prometheus alert",
         f'max without (alertname, alertstate) (label_replace({forwarded}, "alert", "$1", "alertname", "(.+)"))',
         ("gt", 0),
         "{{ .Labels.alert }}{{ with .Labels.namespace }} in {{ . }}{{ end }}",
         details, pending="0s", link=f"{GRAFANA}/alerting/list?search={{{{ .Labels.alert }}}}"),
    rule("truenas-down", "TrueNAS unreachable", f"up{{{NAS}}}", ("lt", 1),
         "Prometheus cannot reach the NAS",
         "node-exporter on TrueNAS (truenas/apps/node-exporter) does not answer: the NAS, the app or the"
         " network is down. Every volume and the backups live on the NAS.",
         severity="critical", no_data="Alerting"),
    rule("zfs-pool-not-online", "ZFS pool not online", f'node_zfs_zpool_state{{{NAS},state!="online"}}',
         ("gt", 0),
         "ZFS pool {{ .Labels.zpool }} is {{ .Labels.state }}",
         "TrueNAS mails the details. A degraded mirror runs on one disk until the other is replaced.",
         severity="critical", pending="1m"),
    # The warning drops out where the critical takes over, so only one fires.
    rule("pool1-space-low", "pool1 space low",
         f'node_filesystem_avail_bytes{{{NAS},mountpoint="/mnt/pool1"}} >= 750e9', ("lt", 1.5e12),
         "pool1 has {{ humanize .Values.B.Value }}B free",
         "Below 1.5 TB (critical below 750 GB), snapshots and the cluster's iSCSI zvols already taken off.",
         severity="warning", pending="15m"),
    rule("pool1-space-critical", "pool1 space critical",
         f'node_filesystem_avail_bytes{{{NAS},mountpoint="/mnt/pool1"}}', ("lt", 750e9),
         "pool1 has only {{ humanize .Values.B.Value }}B free",
         "Below 750 GB. Delete old snapshots or data before the pool fills and writes start to fail.",
         severity="critical", pending="15m"),
    # drivetemp reports whole degrees for the SATA disks; chip targetN is the
    # disk on SATA port N+1 (the Homelab dashboard names them).
    rule("nas-disk-warm", "NAS disk warm",
         f'node_hwmon_temp_celsius{{{NAS},chip=~"target.*"}} < 55', ("gte", 50),
         "NAS disk {{ .Labels.chip }} at {{ .Values.B.Value }} °C",
         "50 °C or more (critical from 55 °C); the disks are rated to 70 °C, below 45 °C is comfortable.",
         severity="warning", pending="10m"),
    rule("nas-disk-hot", "NAS disk hot",
         f'node_hwmon_temp_celsius{{{NAS},chip=~"target.*"}}', ("gte", 55),
         "NAS disk {{ .Labels.chip }} at {{ .Values.B.Value }} °C",
         "55 °C or more. Check the case fans and the room.",
         severity="critical"),
    # A missing series is an alert too: no metric, no proof of a backup.
    rule("postgres-backup-stale", "PostgreSQL backup stale",
         'time() - max(barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{namespace="postgresql"})',
         ("gt", NIGHTLY),
         "PostgreSQL's last base backup is {{ humanizeDuration .Values.B.Value }} old",
         "The postgres-nightly ScheduledBackup writes one to SeaweedFS every night"
         " (apps/production/postgresql/README.md).",
         severity="critical", pending="30m", no_data="Alerting"),
    rule("backup-job-late", "Backup job late",
         "time() - max by (namespace, cronjob) (kube_cronjob_status_last_successful_time"
         '{cronjob=~"openbao-snapshot|openbao-snapshot-copy|postgresql-backup-copy"})',
         ("gt", NIGHTLY),
         "{{ .Labels.cronjob }} last succeeded {{ humanizeDuration .Values.B.Value }} ago",
         "Missed its nightly run; the copies to pool1/apps/db-dumps feed the Hetzner sync.",
         severity="critical", pending="30m", no_data="Alerting"),
    rule("flux-not-ready", "Flux resource not ready",
         "max by (kind, exported_namespace, name) (flux_resource_info"
         '{kind=~"Kustomization|HelmRelease|GitRepository",ready!="True",suspended!="True"})',
         ("gt", 0),
         "{{ .Labels.kind }} {{ .Labels.exported_namespace }}/{{ .Labels.name }} is not ready",
         "Not ready for 15 minutes; flux get all -A shows why.",
         severity="warning", pending="15m"),
    # cert-manager renews 30 days before expiry, so 14 days left means renewal fails.
    rule("certificate-expiring", "Certificate expiring",
         "min by (exported_namespace, name) (certmanager_certificate_expiration_timestamp_seconds) - time()",
         ("lt", 14 * DAY),
         "Certificate {{ .Labels.exported_namespace }}/{{ .Labels.name }} expires in"
         " {{ humanizeDuration .Values.B.Value }}",
         "cert-manager should have renewed it 30 days before expiry; kubectl describe certificate shows why not.",
         severity="warning", pending="1h"),
]

config = {"apiVersion": 1, "groups": [
    {"orgId": 1, "name": "homelab", "folder": "Homelab", "interval": "1m", "rules": rules},
]}

OUT.write_bytes((json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
