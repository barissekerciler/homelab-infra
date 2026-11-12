"""The Claude Code box runs on the subscription, dials out only and keeps its home on iSCSI."""

from functools import lru_cache
from pathlib import Path
import subprocess

import yaml

from adguard_rules import CLUSTER, resolve
from private_values import home, need, oauth2_proxy_url


ROOT = Path(__file__).resolve().parents[2]
APP = "apps/production/claude-code"


@lru_cache
def rendered():
    result = subprocess.run(["kustomize", "build", APP], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def one(kind, name):
    rows = [row for row in rendered() if row["kind"] == kind and row["metadata"]["name"] == name]
    assert len(rows) == 1, f"expected one {kind}/{name}"
    return rows[0]


def pod():
    return one("Deployment", "claude-code")["spec"]["template"]["spec"]


def test_home_is_an_iscsi_volume_and_the_scheduler_picks_the_node():
    claim = one("PersistentVolumeClaim", "claude-code-home")
    assert claim["spec"]["storageClassName"] == "freenas-iscsi-csi"
    volume = one("PersistentVolume", claim["spec"]["volumeName"])
    assert volume["spec"]["csi"]["driver"] == "org.democratic-csi.iscsi"
    spec = pod()
    assert not spec.get("nodeSelector")
    assert not spec.get("affinity")
    assert one("Deployment", "claude-code")["spec"]["strategy"] == {"type": "Recreate"}


def test_the_box_preempts_rather_than_waits():
    # Remote Control reconnects its sessions only when it restarts within about four hours.
    priority = one("PriorityClass", pod()["priorityClassName"])
    assert priority["preemptionPolicy"] == "PreemptLowerPriority"
    assert priority["globalDefault"] is False
    assert priority["value"] > 0


def test_nothing_listens():
    assert not [row for row in rendered() if row["kind"] in {"Service", "Ingress", "IngressRoute"}]
    policy = one("NetworkPolicy", "claude-code")
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in policy["spec"]


def test_the_box_reads_the_cluster_but_no_secrets():
    assert pod()["serviceAccountName"] == "claude-code"
    assert "automountServiceAccountToken" not in pod()
    box = [{"kind": "ServiceAccount", "name": "claude-code", "namespace": "claude-code"}]
    bindings = {row["metadata"]["name"]: row for row in rendered() if row["kind"] == "ClusterRoleBinding"}
    assert {name: row["roleRef"]["name"] for name, row in bindings.items()} == {
        "claude-code-view": "view", "claude-code-read": "claude-code-read"}
    assert all(row["subjects"] == box for row in bindings.values())
    for rule in one("ClusterRole", "claude-code-read")["rules"]:
        assert rule["verbs"] == ["get", "list", "watch"]
        assert "secrets" not in rule["resources"]
        assert "" not in rule["apiGroups"] or rule["resources"] == ["nodes", "persistentvolumes"]
    assert not [row for row in rendered() if row["kind"] in {"Role", "RoleBinding", "ClusterRole"}
                and row["metadata"]["name"] != "claude-code-read"]


def test_private_data_is_out_of_network_reach():
    egress = one("NetworkPolicy", "claude-code")["spec"]["egress"]
    blocks = [peer["ipBlock"] for rule in egress for peer in rule["to"] if "ipBlock" in peer]
    wide = next(block for block in blocks if block["cidr"] == "0.0.0.0/0")
    # NAS, camera, router DNAT, IoT network; pods and services only by namespace.
    dnat = need("ROUTER_DNAT_IP") + "/32"
    assert {need("NAS_IP") + "/32", need("CAMERA_IP") + "/32", dnat, need("IOT_SUBNET"),
            "10.42.0.0/16", "10.43.0.0/16"} <= set(wide["except"])
    cudy = next(rule for rule in egress if rule["to"][0].get("ipBlock", {}).get("cidr") == dnat)
    assert sorted(port["port"] for port in cudy["ports"]) == [22, 80, 443]
    namespaces = next(peer["namespaceSelector"] for rule in egress for peer in rule["to"]
                      if "namespaceSelector" in peer)
    [expression] = namespaces["matchExpressions"]
    assert expression["operator"] == "NotIn"
    assert {"frigate", "immich", "nextcloud", "mail", "vaultwarden", "openbao"} <= set(expression["values"])
    assert all("podSelector" not in peer for rule in egress for peer in rule["to"])


def test_claude_runs_as_the_non_root_user():
    container = pod()["containers"][0]
    assert container["securityContext"]["runAsUser"] == 1000
    env = {row["name"]: row.get("value") for row in container["env"]}
    # Remote Control refuses API keys and setup tokens.
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"} & set(env)


@lru_cache
def agents():
    result = subprocess.run(["kustomize", "build", "apps/production/claude-agents"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return [row for row in yaml.safe_load_all(result.stdout) if row]


def agents_one(kind, name):
    rows = [row for row in agents() if row["kind"] == kind and row["metadata"]["name"] == name]
    assert len(rows) == 1, f"expected one {kind}/{name}"
    return rows[0]


def test_the_box_is_admin_in_claude_agents():
    binding = agents_one("RoleBinding", "claude-code-admin")
    assert binding["metadata"]["namespace"] == "claude-agents"
    assert binding["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "admin"}
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "claude-code", "namespace": "claude-code"}]


def test_claude_agents_memory_is_requested_in_full_and_capped():
    limits = agents_one("LimitRange", "claude-agents")["spec"]["limits"]
    assert len(limits) == 1 and limits[0]["type"] == "Container"
    assert limits[0]["maxLimitRequestRatio"] == {"memory": "1"}
    assert limits[0]["default"]["memory"] == limits[0]["defaultRequest"]["memory"]
    # Each container and the namespace as a whole stay capped, so research pods cannot fill the node.
    assert "memory" in limits[0]["max"]
    assert "requests.memory" in agents_one("ResourceQuota", "claude-agents")["spec"]["hard"]
    browser = agents_one("Deployment", "claude-browser")["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert browser["requests"]["memory"] == browser["limits"]["memory"]


def test_claude_agents_pods_stay_off_the_host_and_the_nas():
    namespace = agents_one("Namespace", "claude-agents")
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "baseline"
    assert namespace["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
    # Same egress as the box itself.
    policy = agents_one("NetworkPolicy", "claude-agents-egress")
    assert policy["spec"]["podSelector"] == {}
    assert policy["spec"]["egress"] == one("NetworkPolicy", "claude-code")["spec"]["egress"]
    volumes = agents_one("ValidatingAdmissionPolicy", "claude-agents-volumes")
    expression = volumes["spec"]["validations"][0]["expression"]
    for kind in ("nfs", "iscsi", "hostPath", "csi"):
        assert f"has(v.{kind})" not in expression
    assert "has(v.persistentVolumeClaim)" in expression


def test_claude_service_accounts_cannot_touch_the_network_policy():
    policy = agents_one("ValidatingAdmissionPolicy", "claude-agents-network-policy")
    rule = policy["spec"]["matchConstraints"]["resourceRules"][0]
    assert rule["resources"] == ["networkpolicies", "ingresses"]
    assert set(rule["operations"]) == {"CREATE", "UPDATE", "DELETE"}
    condition = policy["spec"]["matchConditions"][0]["expression"]
    assert "system:serviceaccount:claude-code:" in condition
    assert "system:serviceaccount:claude-agents:" in condition
    assert policy["spec"]["validations"] == [{"expression": "false", "message": policy["spec"]["validations"][0]["message"]}]
    for name in ("claude-agents-volumes", "claude-agents-network-policy",
                 "claude-agents-controller-kinds", "claude-agents-services"):
        binding = agents_one("ValidatingAdmissionPolicyBinding", name)
        assert binding["spec"]["policyName"] == name
        assert binding["spec"]["validationActions"] == ["Deny"]
        assert binding["spec"]["matchResources"]["namespaceSelector"] == {
            "matchLabels": {"kubernetes.io/metadata.name": "claude-agents"}}


def test_claude_service_accounts_cannot_go_through_controllers_or_publish_ports():
    # Flux applies as cluster-admin, ESO reads every OpenBao path and
    # cert-manager issues public certificates; admin in claude-agents would
    # otherwise reach all three.
    policy = agents_one("ValidatingAdmissionPolicy", "claude-agents-controller-kinds")
    [rule] = policy["spec"]["matchConstraints"]["resourceRules"]
    assert {"source.toolkit.fluxcd.io", "kustomize.toolkit.fluxcd.io", "helm.toolkit.fluxcd.io",
            "image.toolkit.fluxcd.io", "notification.toolkit.fluxcd.io", "fluxcd.controlplane.io",
            "external-secrets.io", "generators.external-secrets.io", "cert-manager.io"} <= set(rule["apiGroups"])
    assert rule["resources"] == ["*"] and set(rule["operations"]) == {"CREATE", "UPDATE", "DELETE"}
    assert policy["spec"]["validations"][0]["expression"] == "false"
    assert "system:serviceaccount:claude-agents:" in policy["spec"]["matchConditions"][0]["expression"]
    # NodePorts and LoadBalancers answer on the nodes' LAN and Tailscale addresses.
    services = agents_one("ValidatingAdmissionPolicy", "claude-agents-services")
    expression = services["spec"]["validations"][0]["expression"]
    assert '"ClusterIP"' in expression and "externalIPs" in expression
    assert "system:serviceaccount:claude-code:" in services["spec"]["matchConditions"][0]["expression"]
    # The store itself refuses both claude namespaces.
    store = yaml.safe_load((ROOT / "infrastructure/external-secrets/store/cluster-secret-store.yaml")
                           .read_text(encoding="utf-8"))
    [condition] = store["spec"]["conditions"]
    [expression] = condition["namespaceSelector"]["matchExpressions"]
    assert expression["operator"] == "NotIn"
    assert {"claude-agents", "claude-code"} <= set(expression["values"])


def test_the_box_reads_the_platform_kinds_and_no_leftover_groups():
    groups = {group for rule in one("ClusterRole", "claude-code-read")["rules"] for group in rule["apiGroups"]}
    assert {"cilium.io", "tuppr.home-operations.com", "postgresql.cnpg.io"} <= groups
    assert not {"helm.cattle.io", "k3s.cattle.io", "clickhouse.com"} & groups


def test_the_browser_pod_holds_no_tokens():
    deployment = agents_one("Deployment", "claude-browser")
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    spec = deployment["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert not spec.get("serviceAccountName") and not spec.get("nodeSelector")
    [container] = spec["containers"]
    assert "envFrom" not in container
    assert {"persistentVolumeClaim", "emptyDir", "configMap"} >= {
        next(key for key in volume if key != "name") for volume in spec["volumes"]}


def test_only_the_box_and_traefik_reach_the_browser():
    rules = agents_one("NetworkPolicy", "claude-browser")["spec"]["ingress"]
    assert [rule["ports"] for rule in rules] == [[{"protocol": "TCP", "port": 8931}],
                                                 [{"protocol": "TCP", "port": 6080}]]
    box, traefik = (rule["from"] for rule in rules)
    assert box == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "claude-code"}},
                    "podSelector": {"matchLabels": {"app": "claude-code"}}}]
    assert traefik == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}]


def test_the_browser_screen_sits_behind_the_github_sign_in():
    host = home("agent-browser")
    ingress = agents_one("Ingress", "claude-browser")
    assert [rule["host"] for rule in ingress["spec"]["rules"]] == [host]
    assert ingress["metadata"]["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"] == \
        "claude-agents-claude-browser-auth@kubernetescrd"
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": "claude-browser", "port": {"number": 6080}}
    auth = agents_one("Middleware", "claude-browser-auth")["spec"]["forwardAuth"]
    assert auth["address"] == oauth2_proxy_url()
    for client in ("local", "tailscale"):
        assert resolve(host, client) == CLUSTER[client], client


def test_the_box_has_no_claude_ai_connectors():
    env = {row["name"]: row.get("value") for row in pod()["containers"][0]["env"]}
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
