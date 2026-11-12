"""Cilium is the CNI: Talos's required values, hostPorts on LAN and Tailscale, ipBlocks that match nodes."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess

import yaml
from private_values import need


ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def rendered(path):
    result = subprocess.run(["kustomize", "build", path], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def release():
    rows = [row for row in rendered("infrastructure")
            if row["kind"] == "HelmRelease" and row["metadata"]["name"] == "cilium"]
    assert len(rows) == 1
    return rows[0]


def values():
    return release()["spec"]["values"]


def test_release_is_pinned_and_survives_prune():
    hr = release()
    assert hr["metadata"]["namespace"] == "kube-system"
    assert hr["spec"]["releaseName"] == "cilium"
    assert "targetNamespace" not in hr["spec"] and "storageNamespace" not in hr["spec"]
    chart = hr["spec"]["chart"]["spec"]
    assert chart["chart"] == "cilium"
    assert re.fullmatch(r"\d+\.\d+\.\d+", chart["version"]), "pin an exact chart version"
    assert chart["sourceRef"] == {"kind": "HelmRepository", "name": "cilium", "namespace": "flux-system"}
    # Deleting the HelmRelease would uninstall the CNI.
    assert hr["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
    # Flux must never own (and so never prune) kube-system itself.
    assert not [row for row in rendered("infrastructure")
                if row["kind"] == "Namespace" and row["metadata"]["name"] == "kube-system"]
    sources = {row["metadata"]["name"]: row for row in rendered("infrastructure/sources")}
    assert sources["cilium"]["spec"] == {"interval": "1h", "url": "oci://quay.io/cilium/charts",
                                         "type": "oci"}


def test_talos_required_values():
    # Talos docs, "Deploying Cilium CNI": pod ranges from Kubernetes, the API
    # through KubePrism, no kernel module loading, cgroup v2 already mounted.
    v = values()
    assert v["ipam"]["mode"] == "kubernetes"
    assert v["kubeProxyReplacement"] is True
    assert (v["k8sServiceHost"], v["k8sServicePort"]) == ("localhost", 7445)
    for container in ("ciliumAgent", "cleanCiliumState"):
        capabilities = v["securityContext"]["capabilities"][container]
        assert "SYS_MODULE" not in capabilities, container
        assert {"NET_ADMIN", "SYS_ADMIN", "SYS_RESOURCE"} <= set(capabilities), container
    assert v["cgroup"] == {"autoMount": {"enabled": False}, "hostRoot": "/sys/fs/cgroup"}


def test_hostports_answer_on_the_lan_and_tailscale():
    # Traefik takes 80/443/465/993 as hostPorts, and every node address must
    # answer: the LAN NIC (eno1, enp0s31f6) and tailscale0. Without kube-proxy,
    # Cilium serves hostPorts only on its devices.
    traefik = [row for row in rendered("infrastructure")
               if row["kind"] == "HelmRelease" and row["metadata"]["name"] == "traefik"][0]
    assert traefik["spec"]["values"]["ports"]["websecure"]["hostPort"] == 443
    devices = values()["devices"]
    # Cilium's device names take "+" as a wildcard.
    patterns = [re.escape(device).replace(r"\+", ".*") for device in devices]
    for nic in ("eno1", "enp0s31f6", "tailscale0"):
        assert any(re.fullmatch(pattern, nic) for pattern in patterns), (nic, devices)
    # tailscale0's 1280 must not set the pod MTU; pods keep 1450 under VXLAN.
    assert values()["MTU"] == 1500
    assert (values()["routingMode"], values()["tunnelProtocol"]) == ("tunnel", "vxlan")


def test_ipblocks_also_match_node_addresses():
    # Cilium matches node addresses in an ipBlock only in this mode, and even
    # then 0.0.0.0/0 (with except) does not select the pod's own node (seen on
    # 1.20.4). claude-code and claude-agents list every node explicitly, so
    # the API servers and host-network pods stay reachable from any node.
    assert "nodes" in values()["policyCIDRMatchMode"]
    nodes = {need(key) + "/32" for key in ("HOMELAB_IP", "HOMELAB2_IP", "HOMELAB3_IP")}
    for path in ("apps/production/claude-code/networkpolicy.yaml",
                 "apps/production/claude-agents/networkpolicy.yaml"):
        policy = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
        rules = [{peer["ipBlock"]["cidr"] for peer in rule["to"] if "ipBlock" in peer}
                 for rule in policy["spec"]["egress"]]
        assert any("0.0.0.0/0" in blocks for blocks in rules), path
        node_rule = next(rule for rule, blocks in zip(policy["spec"]["egress"], rules) if blocks == nodes)
        assert "ports" not in node_rule, path


def test_pod_traffic_is_encrypted_and_no_open_status_ports():
    v = values()
    # OpenBao and several apps speak plain HTTP inside the cluster.
    assert v["encryption"] == {"enabled": True, "type": "wireguard"}
    # The health API (4240) and operator metrics (9963) would answer on the LAN.
    assert v["healthChecking"] is False
    assert v["endpointHealthChecking"] == {"enabled": False}
    assert v["operator"]["prometheus"] == {"enabled": False}


def test_only_kubernetes_network_policies_no_envoy_or_hubble_ui():
    v = values()
    assert v["l7Proxy"] is False
    assert v["hubble"]["enabled"] is True
    assert v["hubble"]["relay"]["enabled"] is False and v["hubble"]["ui"]["enabled"] is False
