"""The Talos schematic and patches keep what the cluster depends on."""

from pathlib import Path

import yaml
from private_values import need

ROOT = Path(__file__).resolve().parents[2]
TALOS = ROOT / "talos"


def load(name):
    return yaml.safe_load((TALOS / name).read_text(encoding="utf-8"))


def documents(name):
    """The v1alpha1 part and the typed documents (Talos 1.14 multi-doc) of a patch."""
    docs = list(yaml.safe_load_all((TALOS / name).read_text(encoding="utf-8")))
    legacy = next((d for d in docs if "kind" not in d), {})
    typed = {}
    for doc in docs:
        if "kind" in doc and doc.get("$patch") != "delete":
            typed[doc["kind"]] = doc
    return legacy, typed


def deleted(name):
    """The kinds of the documents a patch removes from the generated config."""
    docs = yaml.safe_load_all((TALOS / name).read_text(encoding="utf-8"))
    return {doc["kind"] for doc in docs if doc.get("$patch") == "delete"}


def test_schematic_has_the_required_extensions():
    extensions = load("schematic.yaml")["customization"]["systemExtensions"]["officialExtensions"]
    assert set(extensions) == {"siderolabs/i915", "siderolabs/intel-ucode", "siderolabs/iscsi-tools",
                               "siderolabs/util-linux-tools", "siderolabs/tailscale"}


def test_node_patches_are_not_in_this_repository():
    # Hostnames, NIC MACs, install disks and Tailscale addresses live in the
    # private tenant repository's talos/nodes/.
    assert sorted(path.name for path in (TALOS / "patches").glob("*.yaml")) == [
        "common.yaml", "controlplane.yaml", "tailscale.yaml"]


def test_cluster_keeps_pod_service_ranges_and_privileged_default():
    _, common = documents("patches/common.yaml")
    assert common["KubeNetworkConfig"]["podSubnets"] == ["10.42.0.0/16"]
    assert common["KubeNetworkConfig"]["serviceSubnets"] == ["10.43.0.0/16"]
    assert [n["address"] for n in common["ResolverConfig"]["nameservers"]] == [need("PI_IP"), "1.1.1.1", "8.8.8.8"]
    # democratic-csi enters PID 1's namespaces to run iscsiadm.
    assert common["SecurityProfileConfig"]["workloadIsolation"] is False
    # Talos sets 0; Jenkins needs user namespaces to deploy to dev.
    legacy, _ = documents("patches/common.yaml")
    assert int(legacy["machine"]["sysctls"]["user.max_user_namespaces"]) > 0
    _, controlplane = documents("patches/controlplane.yaml")
    assert controlplane["KubeAdmissionControlConfig"]["configuration"]["defaults"]["enforce"] == "privileged"
    # Workloads run on the control plane too: its node config has no taints.
    assert "taints" not in controlplane["KubeNodeConfig"]
    assert need("HOMELAB_IP") in controlplane["KubeAPIServerConfig"]["certExtraSANs"]


def test_cilium_replaces_talos_cni_and_kube_proxy():
    # Cilium (infrastructure/cilium) is the CNI, enforces the NetworkPolicies
    # and replaces kube-proxy. Talos 1.14 drops its default CNI when that
    # document is deleted and kube-proxy when its document says enabled: false;
    # both exist on the control plane only.
    _, controlplane = documents("patches/controlplane.yaml")
    assert deleted("patches/controlplane.yaml") >= {"KubeFlannelCNIConfig"}
    assert "KubeFlannelCNIConfig" not in controlplane
    assert controlplane["KubeProxyConfig"]["enabled"] is False
    # Cilium reaches the API through KubePrism on its default port
    # (k8sServicePort in the Cilium release); no patch moves or disables it.
    for name in ("patches/common.yaml", "patches/controlplane.yaml", "patches/tailscale.yaml"):
        legacy, typed = documents(name)
        assert "KubePrismConfig" not in typed, name
        # The v1alpha1 fields these documents replace may not be set as well.
        assert "network" not in legacy.get("cluster", {}), name
        assert "proxy" not in legacy.get("cluster", {}), name


def test_tailscale_needs_no_key_and_no_secret_is_in_git():
    _, typed = documents("patches/tailscale.yaml")
    env = typed["ExtensionServiceConfig"]["environment"]
    assert "TS_AUTH_ONCE=true" in env and not any(e.startswith("TS_AUTHKEY") for e in env)
    # The secrets bundle lives in Bitwarden (README.md), never in the repository.
    for path in TALOS.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        for marker in ("tskey-auth-", "PRIVATE KEY", "secretboxencryptionsecret", "bootstraptoken", "sops:"):
            assert marker not in text, (path, marker)
