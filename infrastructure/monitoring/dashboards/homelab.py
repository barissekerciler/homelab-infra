"""Generate the Homelab Grafana dashboard.

    python infrastructure/monitoring/dashboards/homelab.py

writes homelab.json next to this file; Flux ships it as the
grafana-dashboards-homelab ConfigMap. tests/manifests/test_monitoring.py checks
that the JSON matches this script.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("homelab.json")
DS = {"type": "prometheus", "uid": "prometheus"}

# Categorical slots in fixed order (dark-surface steps) and the status steps.
SLOT = ["#3987e5", "#d95926", "#199e70", "#c98500"]
NODE = {"homelab": SLOT[0], "homelab2": SLOT[1]}
GOOD, WARN, SERIOUS, CRIT = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
NEUTRAL = "#3987e5"

# Cluster nodes only; the NAS has its own job and row.
NODES = 'job="node-exporter"'
NAS = 'job="truenas"'


def nodename(node=None):
    """Join a node-exporter series to its node name; with a node, keep only that one.

    max by drops the pod label: right after a node-exporter pod is replaced the old
    and new pod's node_uname_info overlap, and a plain join would fail on duplicates.
    """
    selector = f'{{nodename="{node}"}}' if node else ""
    return f"* on (instance) group_left (nodename) max by (instance, nodename) (node_uname_info{selector})"


NODENAME = nodename()
RUNNING = '* on (namespace, pod) group_left () (max by (namespace, pod) (kube_pod_status_phase{phase=~"Running|Pending"}) == 1)'

panels = []
_id = 0


def nid():
    global _id
    _id += 1
    return _id


def target(expr, legend="", ref="A", instant=False, fmt="time_series"):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref,
         "range": not instant, "instant": instant}
    if fmt != "time_series":
        t["format"] = fmt
    return t


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in pairs]}


def row(title, y):
    panels.append({"type": "row", "id": nid(), "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})


def node_overrides(names, colours=None):
    """Colour follows the entity; a second measure of the same entity is dashed."""
    out = []
    for name in names:
        colour = (colours or NODE)[name.split(" ")[0]]
        props = [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}]
        if any(word in name for word in ("NVMe", "out", "requested")):
            props.append({"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 6]}})
        out.append({"matcher": {"id": "byName", "options": name}, "properties": props})
    return out


def stat(title, expr, x, y, w=3, unit="none", thresholds=None, desc="", no_value="0", decimals=None,
         link=None, mappings=None):
    p = {
        "type": "stat", "id": nid(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": 4, "w": w, "x": x, "y": y},
        "targets": [target(expr, instant=True)],
        "fieldConfig": {"defaults": {
            "unit": unit, "noValue": no_value, "color": {"mode": "thresholds"},
            "thresholds": thresholds or steps((None, GOOD)),
        }, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background_solid", "graphMode": "none", "textMode": "value",
                    "justifyMode": "center", "orientation": "auto", "wideLayout": True,
                    "showPercentChange": False},
    }
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    if link:
        p["fieldConfig"]["defaults"]["links"] = [{"title": link[0], "url": link[1], "targetBlank": True}]
    if mappings:
        p["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {
            str(value): {"text": text, "index": i} for i, (value, text) in enumerate(mappings)}}]
    panels.append(p)
    return p


def table(title, expr, x, y, w, h, desc, no_value, hide, order, rename=None, unit=None):
    """One row per series of an instant query; labels become columns, ordered by `order`."""
    defaults = {"noValue": no_value, "custom": {"align": "auto"}}
    if unit:
        defaults["unit"] = unit
    p = {
        "type": "table", "id": nid(), "title": title, "datasource": DS, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, "", "A", True, "table")],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {name: True for name in hide},
            "indexByName": {name: i for i, name in enumerate(order)},
            "renameByName": rename or {}}}],
    }
    panels.append(p)
    return p


def panel_link(title, panel):
    """A data link that opens one panel of this dashboard full screen."""
    return {"title": title, "url": f"/d/homelab-overview/homelab?viewPanel={panel['id']}", "targetBlank": False}


def bargauge(title, targets, x, y, w, h, unit="percentunit", thresholds=None, desc="", maximum=1,
             min_size=16, overrides=None, decimals=1):
    panels.append({
        "type": "bargauge", "id": nid(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": targets,
        "fieldConfig": {"defaults": {
            "unit": unit, "min": 0, "max": maximum, "decimals": decimals,
            "color": {"mode": "thresholds"},
            "thresholds": thresholds or steps((None, GOOD), (0.7, WARN), (0.85, SERIOUS), (0.95, CRIT)),
        }, "overrides": overrides or []},
        "options": {"orientation": "horizontal", "displayMode": "basic", "valueMode": "text",
                    "namePlacement": "left", "showUnfilled": True, "sizing": "manual",
                    "minVizHeight": min_size, "maxVizHeight": 28, "minVizWidth": 8,
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "legend": {"showLegend": False}},
    })


def timeseries(title, targets, x, y, w, h, unit, names=None, desc="", minimum=None, maximum=None,
               stack=False, draw="line", extra_overrides=None, legend_calcs=("lastNotNull", "max"),
               colours=None, decimals=None):
    custom = {"drawStyle": draw, "lineWidth": 2, "fillOpacity": 12 if draw == "line" else 80,
              "gradientMode": "opacity" if draw == "line" else "none", "showPoints": "never",
              "spanNulls": True, "lineInterpolation": "smooth", "barAlignment": 0,
              "axisSoftMin": 0, "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "axisBorderShow": False, "axisPlacement": "auto"}
    defaults = {"unit": unit, "color": {"mode": "palette-classic"}, "custom": custom}
    if decimals is not None:
        defaults["decimals"] = decimals
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    panels.append({
        "type": "timeseries", "id": nid(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": targets,
        "fieldConfig": {"defaults": defaults,
                        "overrides": node_overrides(names or [], colours) + (extra_overrides or [])},
        "options": {"legend": {"displayMode": "table", "placement": "right",
                               "calcs": list(legend_calcs), "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    })


y = 0
# ---- Headline tiles -------------------------------------------------------
stat("Nodes ready", 'sum(kube_node_status_condition{condition="Ready",status="true"})', 0, y,
     thresholds=steps((None, CRIT), (2, GOOD)), desc="Nodes whose Ready condition is true (of 2).")
stat("Pods running", 'sum(kube_pod_status_phase{phase="Running"})', 3, y,
     thresholds=steps((None, "#3987e5")))
BAD_PHASE = 'phase=~"Pending|Failed|Unknown"'
BAD_WAIT = 'reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerConfigError|CreateContainerError"'
trouble = stat("Pods in trouble",
               f'(sum(kube_pod_status_phase{{{BAD_PHASE}}}) or vector(0))'
               f' + (sum(kube_pod_container_status_waiting_reason{{{BAD_WAIT}}}) or vector(0))',
               6, y, thresholds=steps((None, GOOD), (1, CRIT)),
               desc="Pending, Failed or Unknown pods plus containers stuck in CrashLoopBackOff or an image pull"
                    " error. The Workloads row lists them with the reason.")
stat("Restarts (24h)", 'round(sum(increase(kube_pod_container_status_restarts_total[24h])))', 9, y,
     thresholds=steps((None, GOOD), (1, WARN), (10, CRIT)), desc="Container restarts in the last 24 hours.")
stat("Flux not ready", 'count(flux_resource_info{ready!="True"}) or vector(0)', 12, y,
     thresholds=steps((None, GOOD), (1, CRIT)), link=("Flux UI", "https://flux.home.bariss.dev"),
     desc="Flux resources (Kustomizations, HelmReleases, sources) whose Ready condition is not True.")
stat("Alerts firing",
     'count(ALERTS{alertstate="firing",severity=~"warning|critical"}) or vector(0)', 15, y,
     thresholds=steps((None, GOOD), (1, SERIOUS)),
     desc="Firing Prometheus alerts with severity warning or critical. Prometheus UI lists them.")
stat("Volumes over 80%",
     'count(max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes{job=\"kubelet\"} / kubelet_volume_stats_capacity_bytes{job=\"kubelet\"}) > 0.8) or vector(0)',
     18, y, thresholds=steps((None, GOOD), (1, WARN)), desc="Mounted claims that are more than 80% full.")
stat("Next TLS expiry", 'min(certmanager_certificate_expiration_timestamp_seconds) - time()', 21, y,
     unit="s", decimals=0, thresholds=steps((None, CRIT), (7 * 86400, SERIOUS), (14 * 86400, GOOD)),
     no_value="-", desc="Time until the first cert-manager certificate expires.")
y += 4


def cores(node=None):
    """Cores per node, counted once even while an old and a new node-exporter pod overlap."""
    return f'count by (nodename) (max by (nodename, cpu) (node_cpu_seconds_total{{mode="idle",{NODES}}} {nodename(node)}))'


# Busy CPU: neither idle nor waiting on disk (iowait is idle time too).
def cpu_used(node=None):
    return (f'1 - sum by (nodename) (rate(node_cpu_seconds_total{{mode=~"idle|iowait",{NODES}}}[5m]) {nodename(node)})'
            f' / {cores(node)}')


# max by (nodename) keeps one series per node across node-exporter pod replacements.
def mem_used(node=None):
    return (f'max by (nodename) (1 - (node_memory_MemAvailable_bytes{{{NODES}}} / node_memory_MemTotal_bytes{{{NODES}}})'
            f' {nodename(node)})')


def reserved(resource, node):
    return (f'sum by (node) (kube_pod_container_resource_requests{{resource="{resource}"}} {RUNNING})'
            f' / sum by (node) (kube_node_status_allocatable{{resource="{resource}",node="{node}"}})')


# Talos mounts / read-only; images, logs and emptyDirs live on /var (EPHEMERAL).
def disk(node=None):
    return (f'max by (nodename) (1 - (node_filesystem_avail_bytes{{mountpoint="/var",{NODES}}}'
            f' / node_filesystem_size_bytes{{mountpoint="/var",{NODES}}}) {nodename(node)})')


def per_node(*measures):
    """One query per node and measure: Grafana keeps the query order, not the series order in a query."""
    rows = [(f"{node} {label}".strip(), expr(node)) for node in NODE for label, expr in measures]
    return [target(expr, legend, chr(ord("A") + i), True) for i, (legend, expr) in enumerate(rows)]


# ---- Node status: Talos release, versions, uptime -----------------------------------
def by_node(expr):
    """Give a node-exporter series kube-state-metrics' node label, so the table can merge on it."""
    return f'max by (node) (label_replace({expr}, "node", "$1", "nodename", "(.+)"))'


def value_column(ref, name, unit, extra=None):
    props = [{"id": "displayName", "value": name}, {"id": "unit", "value": unit}] + (extra or [])
    return {"matcher": {"id": "byName", "options": f"Value #{ref}"}, "properties": props}


usage = [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
         {"id": "thresholds", "value": steps((None, GOOD), (0.75, WARN), (0.9, CRIT))}]
panels.append({
    "type": "table", "id": nid(), "title": "Nodes", "datasource": DS,
    "description": "Per node: the Talos release (OS), Kubernetes and kernel version, whether it is Ready, "
                   "time since boot, and CPU, memory and /var used now. `t -n <IP> version` shows the same.",
    "gridPos": {"h": 4, "w": 24, "x": 0, "y": y},
    "targets": [
        target("max by (node, internal_ip, os_image, kubelet_version, kernel_version) (kube_node_info)",
               "", "A", True, "table"),
        target('max by (node) (kube_node_status_condition{condition="Ready",status="true"})', "", "B", True, "table"),
        target(by_node(f"(time() - node_boot_time_seconds{{{NODES}}}) {NODENAME}"), "", "C", True, "table"),
        target(by_node(cpu_used()), "", "D", True, "table"),
        target(by_node(mem_used()), "", "E", True, "table"),
        target(by_node(disk()), "", "F", True, "table"),
    ],
    "fieldConfig": {"defaults": {"custom": {"align": "auto"}, "noValue": "-"}, "overrides": [
        value_column("B", "Ready", "none", [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "1": {"text": "Ready", "color": GOOD, "index": 0},
                "0": {"text": "Not ready", "color": CRIT, "index": 1}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}}]),
        value_column("C", "Up", "dtdurations", [{"id": "decimals", "value": 0}]),
        value_column("D", "CPU", "percentunit", usage),
        value_column("E", "Memory", "percentunit", usage),
        value_column("F", "Disk (/var)", "percentunit", usage),
    ]},
    "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
    "transformations": [
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value #A": True},
            "indexByName": {name: i for i, name in enumerate(
                ["node", "os_image", "kubelet_version", "kernel_version", "internal_ip",
                 "Value #B", "Value #C", "Value #D", "Value #E", "Value #F"])},
            "renameByName": {"node": "Node", "os_image": "OS", "kubelet_version": "Kubernetes",
                             "kernel_version": "Kernel", "internal_ip": "IP"}}},
    ],
})
y += 4

# ---- Capacity --------------------------------------------------------------
row("Capacity: used vs reserved", y)
y += 1

reserve_desc = ("Used is what the node really does. Reserved is the sum of the running pods' requests, "
                "which is what the scheduler counts: at 100% no new pod fits, however idle the node is.")
bargauge("CPU", per_node(("used", cpu_used), ("reserved", lambda node: reserved("cpu", node))),
         0, y, 8, 6, desc=reserve_desc)
bargauge("Memory", per_node(("used", mem_used), ("reserved", lambda node: reserved("memory", node))),
         8, y, 8, 6, desc=reserve_desc)
bargauge("Disk (/var)", per_node(("", disk)), 16, y, 8, 6,
         thresholds=steps((None, GOOD), (0.75, WARN), (0.9, CRIT)),
         desc="Used space on each node's /var, where Talos keeps images, logs and emptyDirs (/ is read-only).")
y += 6

# ---- Nodes over time -----------------------------------------------------------
row("Nodes", y)
y += 1
names = ["homelab", "homelab2"]
timeseries("CPU used", [target(cpu_used(), "{{nodename}}")], 0, y, 8, 8,
           "percentunit", names, minimum=0, maximum=1)
timeseries("Memory used", [target(mem_used(), "{{nodename}}")], 8, y, 8, 8, "percentunit", names,
           minimum=0, maximum=1)
temp_names = ["homelab CPU", "homelab2 CPU", "homelab2 NVMe"]
timeseries("Temperature", [
    target(f'max by (nodename) (node_thermal_zone_temp{{type="x86_pkg_temp",{NODES}}} {NODENAME})', "{{nodename}} CPU", "A"),
    target(f'max by (nodename) (node_hwmon_temp_celsius{{chip=~"nvme.*",sensor="temp1",{NODES}}} {NODENAME})', "{{nodename}} NVMe", "B"),
], 16, y, 8, 8, "celsius", temp_names, desc="CPU package and NVMe (dashed) temperature.")
y += 8
net_names = ["homelab in", "homelab out", "homelab2 in", "homelab2 out"]
net = 'sum by (nodename) (rate(node_network_{d}_bytes_total{{device=~"en.*|eth.*",{n}}}[5m]) {j})'
timeseries("Network", [
    target(net.format(d="receive", n=NODES, j=NODENAME), "{{nodename}} in", "A"),
    target(net.format(d="transmit", n=NODES, j=NODENAME), "{{nodename}} out", "B"),
], 0, y, 12, 8, "Bps", net_names,
    desc="Wired interface traffic per node; out is drawn below the axis.",
    extra_overrides=[{"matcher": {"id": "byRegexp", "options": ".* out"},
                      "properties": [{"id": "custom.transform", "value": "negative-Y"}]}])
timeseries("Load (1m) per core", [
    target(f'max by (nodename) (node_load1{{{NODES}}} {NODENAME}) / {cores()}', "{{nodename}}")], 12, y, 12, 8, "none", names,
    desc="1-minute load average divided by the core count; above 1 means work is queueing.")
y += 8

# ---- TrueNAS ---------------------------------------------------------------------
row("TrueNAS", y)
y += 1
nas_desc = "node-exporter on the NAS (truenas/apps/node-exporter)."
stat("NAS", f'up{{{NAS}}}', 0, y, w=6, thresholds=steps((None, CRIT), (1, GOOD)),
     mappings=[(0, "Down"), (1, "Up")], no_value="Down",
     desc="Whether Prometheus can scrape the NAS. " + nas_desc)
stat("Pools", f'count(node_zfs_zpool_state{{{NAS},state!="online"}} == 1) or vector(0)', 6, y, w=6,
     thresholds=steps((None, GOOD), (1, CRIT)), mappings=[(0, "Online")],
     desc="Pools whose ZFS state is not online (degraded, faulted, ...). TrueNAS mails its own alerts.")
stat("pool1 free", f'node_filesystem_avail_bytes{{{NAS},mountpoint="/mnt/pool1"}}', 12, y, w=6,
     unit="bytes", decimals=2, no_value="-",
     thresholds=steps((None, CRIT), (750e9, WARN), (1.5e12, GOOD)),
     desc="Space datasets can still use on pool1 (two mirrors), snapshots and the cluster's iSCSI zvols"
          " already taken off. Yellow below 1.5 TB, red below 750 GB.")
stat("Hottest disk", f'max(node_hwmon_temp_celsius{{{NAS},chip=~"target.*"}})', 18, y, w=6, unit="celsius",
     thresholds=steps((None, GOOD), (45, WARN), (50, SERIOUS), (55, CRIT)), no_value="-",
     desc="Highest SATA disk temperature (drivetemp): the four pool disks and the cache SSD."
          " All are rated to 70 °C; below 45 °C is comfortable.")
y += 4

# NAS disks by SATA port, since the sdX letters move between boots. The port is ata-N in
# node_disk_info's udev path and target<N-1> in drivetemp's chip. The second disk of a mirror is dashed.
NAS_DISKS = [  # name, SATA port, colour
    ("mirror-0 disk 1", 3, SLOT[0]), ("mirror-0 disk 2", 4, SLOT[0]),
    ("mirror-1 disk 1", 2, SLOT[3]), ("mirror-1 disk 2", 5, SLOT[3]),
    ("SSD cache", 6, SLOT[2]),  # pool1 L2ARC
]
BOOT, BOOT_COLOUR = "boot NVMe", "#5f5e5a"


def name_disks(expr, label, port_regex, nvme_regex):
    """Add a disk label naming the disk whose SATA port `label` matches."""
    for name, port, _ in NAS_DISKS:
        expr = f'label_replace({expr}, "disk", "{name}", "{label}", "{port_regex(port)}")'
    return f'label_replace({expr}, "disk", "{BOOT}", "{label}", "{nvme_regex}")'


def disk_overrides(suffix=""):
    out = []
    for name, colour in [(name, colour) for name, _, colour in NAS_DISKS] + [(BOOT, BOOT_COLOUR)]:
        props = [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}]
        if name.endswith("2"):
            props.append({"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 6]}})
        out.append({"matcher": {"id": "byName", "options": name + suffix}, "properties": props})
    return out


disk_desc = ("pool1's two mirrors, its SSD read cache (L2ARC) and the NVMe boot disk, named by the SATA"
             " port they sit on.")
timeseries("Disk temperature", [target(name_disks(
    f'node_hwmon_temp_celsius{{{NAS},chip=~"target.*|nvme.*",sensor="temp1"}}',
    "chip", lambda port: f"target{port - 1}:.*", "nvme.*"), "{{disk}}")],
    0, y, 8, 8, "celsius", desc=disk_desc, extra_overrides=disk_overrides())
nas_cores = f'count(node_cpu_seconds_total{{{NAS},mode="idle"}})'
timeseries("CPU", [
    target(f'1 - sum(rate(node_cpu_seconds_total{{{NAS},mode=~"idle|iowait"}}[5m])) / {nas_cores}', "busy", "A"),
    target(f'sum(rate(node_cpu_seconds_total{{{NAS},mode="iowait"}}[5m])) / {nas_cores}', "waiting on disk", "B"),
], 8, y, 8, 8, "percentunit", minimum=0, maximum=1, stack=True, legend_calcs=("mean", "max"),
    desc="All four cores. Waiting on disk (iowait) is idle time while the disks are busy, e.g. a Cloud Sync run.",
    extra_overrides=[
        {"matcher": {"id": "byName", "options": "busy"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": SLOT[0]}}]},
        {"matcher": {"id": "byName", "options": "waiting on disk"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": SLOT[1]}}]}])
timeseries("CPU temperature", [target(
    f'node_hwmon_temp_celsius{{{NAS},chip="platform_coretemp_0",sensor="temp1"}}', "package")],
    16, y, 8, 8, "celsius", desc="CPU package temperature (coretemp).")
y += 8
mem_total = f'node_memory_MemTotal_bytes{{{NAS}}}'
mem_avail = f'node_memory_MemAvailable_bytes{{{NAS}}}'
arc = f'node_zfs_arc_size{{{NAS}}}'
timeseries("Memory", [
    target(f'clamp_min({mem_total} - {mem_avail} - ignoring (__name__) {arc}, 0)', "system and apps", "A"),
    target(arc, "ZFS cache (ARC)", "B"),
    target(mem_avail, "free", "C"),
], 0, y, 8, 8, "bytes", stack=True, legend_calcs=("lastNotNull",),
    desc="32 GB in total. ZFS keeps its read cache (ARC) in memory and gives it back under pressure.",
    extra_overrides=[
        {"matcher": {"id": "byName", "options": "system and apps"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": SLOT[0]}}]},
        {"matcher": {"id": "byName", "options": "ZFS cache (ARC)"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": SLOT[1]}}]},
        {"matcher": {"id": "byName", "options": "free"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "#5f5e5a"}}]}])


def nas_disk_io(direction):
    rate = (f'rate(node_disk_{direction}_bytes_total{{{NAS}}}[5m])'
            f' * on (device) group_left (path) node_disk_info{{{NAS},path!=""}}')
    return f'sum by (disk) ({name_disks(rate, "path", lambda port: f".*-ata-{port}[.]0", ".*-nvme-.*")})'


timeseries("Disk throughput", [
    target(nas_disk_io("read"), "{{disk}} read", "A"),
    target(nas_disk_io("written"), "{{disk}} write", "B"),
], 8, y, 8, 8, "Bps", legend_calcs=("mean", "max"),
    desc="Bytes read and written per disk; write is drawn below the axis. " + disk_desc,
    extra_overrides=disk_overrides(" read") + disk_overrides(" write") + [
        {"matcher": {"id": "byRegexp", "options": ".* write"},
         "properties": [{"id": "custom.transform", "value": "negative-Y"}]}])
timeseries("Network", [
    target(f'rate(node_network_receive_bytes_total{{{NAS},device="eno1"}}[5m])', "in", "A"),
    target(f'rate(node_network_transmit_bytes_total{{{NAS},device="eno1"}}[5m])', "out", "B"),
], 16, y, 8, 8, "Bps", legend_calcs=("mean", "max"),
    desc="eno1 traffic: iSCSI and NFS for the cluster, backups, SMB. Out is drawn below the axis.",
    extra_overrides=[{"matcher": {"id": "byName", "options": "out"},
                      "properties": [{"id": "custom.transform", "value": "negative-Y"}]}])
y += 8


def fixed(colours):
    """Overrides that pin each named series to its colour."""
    return [{"matcher": {"id": "byName", "options": name},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}]}
            for name, colour in colours.items()]


# The SSD cache and the disks wear their Disk temperature colours, RAM the ARC colour of the Memory panel.
SSD, DISKS, RAM = SLOT[2], SLOT[0], SLOT[1]


def hit_rate(hits, misses):
    h, m = f'rate(node_zfs_arc_{hits}{{{NAS}}}[5m])', f'rate(node_zfs_arc_{misses}{{{NAS}}}[5m])'
    return f'{h} / ({h} + {m})'


timeseries("Reads RAM missed: SSD cache vs disks", [
    target(f'rate(node_zfs_arc_l2_read_bytes{{{NAS}}}[5m])', "from SSD cache", "A"),
    target(f'sum(rate(node_disk_read_bytes_total{{{NAS}}}[5m])'
           f' * on (device) group_left () node_disk_info{{{NAS},path!="",rotational="1"}})', "from disks", "B"),
], 0, y, 8, 8, "Bps", stack=True, legend_calcs=("mean", "max"),
    desc="Where pool1 found the reads its RAM cache (ARC) did not have: the SSD read cache (L2ARC)"
         " or the four disks. The more comes from the SSD, the fewer slow disk seeks. A scrub reads the disks"
         " directly and skips the cache.",
    extra_overrides=fixed({"from SSD cache": SSD, "from disks": DISKS}))
timeseries("Cache hit rate", [
    target(hit_rate("hits", "misses"), "RAM (ARC)", "A"),
    target(hit_rate("l2_hits", "l2_misses"), "SSD (L2ARC)", "B"),
], 8, y, 8, 8, "percentunit", minimum=0, maximum=1, legend_calcs=("mean", "lastNotNull"),
    desc="Share of lookups each cache answered. RAM sees every read; the SSD only sees what RAM missed,"
         " so its rate is lower and swings with the workload. Only frequently used blocks go to the SSD"
         " (TrueNAS tunable l2arc_mfuonly=1).",
    extra_overrides=fixed({"RAM (ARC)": RAM, "SSD (L2ARC)": SSD}))
timeseries("SSD cache size", [target(f'node_zfs_arc_l2_asize{{{NAS}}}', "on SSD")],
           16, y, 8, 8, "bytes", legend_calcs=("lastNotNull",), extra_overrides=fixed({"on SSD": SSD}),
           desc="Space the read cache uses on the SSD (about 236 GB usable). It fills from RAM over days and"
                " TrueNAS keeps it across reboots (persistent L2ARC).")
y += 8

# ---- Workloads -----------------------------------------------------------------
row("Workloads", y)
y += 1
# The pods the "Pods in trouble" tile counts, one row per pod (or per waiting container), with why:
# the phase plus the container waiting reason, the Unschedulable condition or the pod status reason
# (Evicted, NodeLost, ...). Running pods only show up for the waiting reasons the tile counts.
bad_phase = f'max by (namespace, pod, phase) (kube_pod_status_phase{{{BAD_PHASE}}} == 1)'
why = ('(max by (namespace, pod, container, reason) (kube_pod_container_status_waiting_reason == 1)'
       ' or max by (namespace, pod, reason) (label_replace(kube_pod_status_unschedulable == 1, "reason", "Unschedulable", "", ""))'
       ' or max by (namespace, pod, reason) (kube_pod_status_reason == 1))')
bad_wait = f'max by (namespace, pod, container, reason) (kube_pod_container_status_waiting_reason{{{BAD_WAIT}}} == 1)'
pod_age = 'max by (namespace, pod) (time() - kube_pod_created)'
trouble_table = table(
    "Pods in trouble: which and why",
    f'(({bad_phase} * on (namespace, pod) group_right (phase) {why})'
    f' or ({bad_phase} unless on (namespace, pod) {why})'
    f' or ({bad_wait} unless on (namespace, pod) {bad_phase}))'
    f' * on (namespace, pod) group_left () {pod_age}',
    0, y, 24, 7, no_value="All pods healthy", unit="s",
    desc="Every pod the 'Pods in trouble' tile counts: its phase, the container that is waiting and why"
         " (CrashLoopBackOff, ImagePullBackOff, Unschedulable, Evicted, ...), and how old the pod is."
         " Empty means nothing is stuck.",
    hide=["Time"], order=["namespace", "pod", "container", "phase", "reason", "Value"],
    rename={"Value": "pod age"})
trouble["fieldConfig"]["defaults"]["links"] = [panel_link("Which pods and why", trouble_table)]
y += 7
cpu_pod ='sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{container!="",image!=""}[5m]))'
bargauge("Top pods by CPU", [target(f"topk(10, {cpu_pod})", "{{namespace}}/{{pod}}", "A", True)],
         0, y, 8, 10, unit="suffix: cores", maximum=None, decimals=2, min_size=12,
         thresholds=steps((None, "#3987e5")), desc="CPU cores used now (5-minute rate).")
bargauge("Top pods by memory",
         [target('topk(10, sum by (namespace, pod) (container_memory_working_set_bytes{container!="",image!=""}))',
                 "{{namespace}}/{{pod}}", "A", True)],
         8, y, 8, 10, unit="bytes", maximum=None, decimals=1, min_size=12,
         thresholds=steps((None, "#3987e5")), desc="Working set memory now.")
idle = (f'topk(10, sum by (namespace, pod) (kube_pod_container_resource_requests{{resource="cpu"}} {RUNNING})'
        f' - {cpu_pod})')
bargauge("Reserved CPU left idle", [target(idle, "{{namespace}}/{{pod}}", "A", True)],
         16, y, 8, 10, unit="suffix: cores", maximum=None, decimals=2, min_size=12,
         thresholds=steps((None, GOOD), (0.25, WARN), (0.5, SERIOUS)),
         desc="CPU request minus CPU used: the first candidates for smaller requests.")
y += 10
ns_names = []
timeseries("CPU by namespace", [target(
    'sum by (namespace) (rate(container_cpu_usage_seconds_total{container!="",image!=""}[5m]))', "{{namespace}}")],
    0, y, 12, 9, "suffix: cores", stack=True, legend_calcs=("mean", "max"))
timeseries("Memory by namespace", [target(
    'sum by (namespace) (container_memory_working_set_bytes{container!="",image!=""})', "{{namespace}}")],
    12, y, 12, 9, "bytes", stack=True, legend_calcs=("lastNotNull", "max"))
y += 9

# ---- GitOps, ingress, storage -------------------------------------------------
row("Flux, ingress and storage", y)
y += 1
table("Flux: not ready", 'flux_resource_info{ready!="True"}', 0, y, 8, 8,
      desc="Flux resources whose Ready condition is not True. Empty means everything reconciled.",
      no_value="Everything reconciled",
      hide=["Time", "Value", "__name__", "container", "endpoint", "instance", "job", "namespace", "pod",
            "uid", "url", "path", "ref", "service", "source_name", "revision"],
      order=["kind", "exported_namespace", "name", "ready", "reason", "suspended"],
      rename={"exported_namespace": "namespace"})
svc = 'label_replace({e}, "svc", "$1", "service", "(.+)@kubernetes")'
timeseries("Ingress requests", [target(
    'topk(8, ' + svc.format(e='sum by (service) (rate(traefik_service_requests_total[5m]))') + ')', "{{svc}}")],
    8, y, 8, 8, "reqps", legend_calcs=("mean",),
    desc="Requests per second through Traefik, top 8 services.")
timeseries("Ingress errors (5xx)", [target(
    svc.format(e='sum by (service) (rate(traefik_service_requests_total{code=~"5.."}[5m]))'), "{{svc}}")],
    16, y, 8, 8, "reqps", legend_calcs=("mean", "max"), draw="bars", stack=True,
    desc="Server errors per second through Traefik.")
y += 8
bargauge("Volumes", [target(
    'sort_desc(max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes{job=\"kubelet\"} / kubelet_volume_stats_capacity_bytes{job=\"kubelet\"}))',
    "{{namespace}}/{{persistentvolumeclaim}}", "A", True)], 0, y, 24, 9,
    thresholds=steps((None, GOOD), (0.75, WARN), (0.9, CRIT)), min_size=10,
    desc="How full each mounted claim is (iSCSI, NFS and local).")
y += 9

dashboard = {
    "uid": "homelab-overview",
    "title": "Homelab",
    "description": "The homelab at a glance: cluster health and capacity, nodes, TrueNAS, workloads, Flux, ingress and storage.",
    "tags": ["homelab", "kubernetes", "truenas"],
    "editable": True,
    "graphTooltip": 1,
    "refresh": "30s",
    "schemaVersion": 39,
    "time": {"from": "now-6h", "to": "now"},
    "timepicker": {"refresh_intervals": ["15s", "30s", "1m", "5m", "15m"]},
    "timezone": "browser",
    "fiscalYearStartMonth": 0,
    "liveNow": False,
    "templating": {"list": []},
    "annotations": {"list": []},
    "links": [
        {"title": "Flux", "type": "link", "url": "https://flux.home.bariss.dev", "targetBlank": True, "icon": "external link"},
        {"title": "Kubernetes dashboards", "type": "dashboards", "tags": ["kubernetes-mixin"], "asDropdown": True},
    ],
    "panels": panels,
}
OUT.write_bytes((json.dumps(dashboard, indent=2) + "\n").encode())
print(f"{len(panels)} panels -> {OUT}")
