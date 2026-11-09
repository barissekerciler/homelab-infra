# node-exporter on TrueNAS

Native TrueNAS Custom App `node-exporter` for the Homelab dashboard in
Grafana. TrueNAS Apps owns the workload; Flux does not
manage it. The cluster's Prometheus scrapes `http://10.0.0.203:9100/metrics`
(`infrastructure/monitoring/scrape/truenas.yaml`, job `truenas`).

- Host network and host PID, the root filesystem read-only at `/host`, no
  capabilities, at most a quarter core and 64 MiB.
- It listens on the LAN address only.
- Disk temperatures come from the `drivetemp` hwmon driver, which TrueNAS
  loads for the SATA disks; the NVMe and the CPU (`coretemp`) have their own
  hwmon sensors.
- Pool state comes from the ZFS collector and free space from `/mnt/<pool>`.
  TrueNAS mails its own alerts (SMART, pools, updates), so they are not
  scraped.

## Install or update

Paste `compose.yaml` in TrueNAS Apps > Discover Apps > More Options >
Install via YAML with the name `node-exporter`, or from an admin shell:

```sh
python3 -c 'import json, subprocess, sys; print(subprocess.run(["midclt", "call", "-j", "app.create", json.dumps({"app_name": "node-exporter", "custom_app": True, "custom_compose_config_string": sys.stdin.read()})], capture_output=True, text=True))' < compose.yaml
```

For a new image tag use `app.update` with the same `custom_compose_config_string`.

## Check

```sh
curl -s http://10.0.0.203:9100/metrics | grep -E '^node_hwmon_temp_celsius|^node_zfs_zpool_state'
```
