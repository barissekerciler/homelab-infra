"""tuppr upgrades Talos and Kubernetes; Flux image automation picks the patches."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_versions_follow_their_image_policies():
    talos = (ROOT / "infrastructure/tuppr/upgrades/talos.yaml").read_text(encoding="utf-8")
    assert '# {"$imagepolicy": "flux-system:talos:tag"}' in talos
    kubernetes = (ROOT / "infrastructure/tuppr/upgrades/kubernetes.yaml").read_text(encoding="utf-8")
    assert '# {"$imagepolicy": "flux-system:kubelet:tag"}' in kubernetes
    # Patches only: a new minor is a manual change, one minor at a time.
    assert load("infrastructure/image-update-automation/policies/talos.yaml")["spec"]["policy"] == \
        {"semver": {"range": "~1.14"}}
    assert load("infrastructure/image-update-automation/policies/kubelet.yaml")["spec"]["policy"] == \
        {"semver": {"range": "~1.36"}}
    automation = load("infrastructure/image-update-automation/tuppr-update-automation.yaml")
    assert automation["spec"]["update"]["path"] == "./infrastructure/tuppr/upgrades"


def test_upgrades_are_health_gated_at_night():
    for name in ("talos", "kubernetes"):
        spec = load(f"infrastructure/tuppr/upgrades/{name}.yaml")["spec"]
        assert any(check["kind"] == "Node" for check in spec["healthChecks"])
        [window] = spec["maintenance"]["windows"]
        assert window["timezone"] == "Europe/Berlin"


def test_tuppr_reads_only_nodes_and_reaches_the_talos_api():
    rules = load("infrastructure/tuppr/release.yaml")["spec"]["values"]["rbac"]["healthCheckRules"]
    assert rules == [{"apiGroups": [""], "resources": ["nodes"], "verbs": ["get", "list", "watch"]}]
    first = next(yaml.safe_load_all((ROOT / "talos/patches/controlplane.yaml").read_text(encoding="utf-8")))
    access = first["machine"]["features"]["kubernetesTalosAPIAccess"]
    assert access == {"enabled": True, "allowedRoles": ["os:admin"],
                      "allowedKubernetesNamespaces": ["system-upgrade"]}
