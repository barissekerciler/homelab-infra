#!/usr/bin/env bash
# Proves that the cluster enforces NetworkPolicies (talos/README.md).
#
# Cilium enforces them. The script first checks that its agent runs on every
# node, then starts a throwaway pod in claude-agents, which has the Claude Code
# box's egress policy, on each node in turn and tries every target below from
# it: Cilium treats the pod's own node (host) and the other node (remote-node)
# differently, so both must pass. Blocked means the TCP connect timed out (the
# policy drops the SYN); a refused connect or any answer means it got through.
# Exits 1 when the agent is missing, a forbidden target is reachable or an
# allowed one is not.
#
#   ./scripts/check-network-policies.sh
set -euo pipefail

NS=claude-agents
RUN="netpol-check-$$"
IMAGE=curlimages/curl:8.17.0

cleanup() { kubectl -n "$NS" delete pod -l "netpol-check=$RUN" --ignore-not-found --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Without its agent a node starts no new pods, and policy changes stop
# reaching it.
status=$(kubectl -n kube-system get daemonset cilium \
  -o jsonpath='{.status.desiredNumberScheduled} {.status.numberReady}') || {
  echo "FAIL no cilium DaemonSet in kube-system"; exit 1; }
read -r desired ready <<<"$status"
if [ -z "$desired" ] || [ "$desired" = 0 ] || [ "$desired" != "$ready" ]; then
  echo "FAIL cilium agents ready: $ready/$desired"; exit 1
fi
echo "ok   cilium agents ready: $ready/$desired"

fail=0
for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
  pod="$RUN-$node"
  # A small request, so the pod fits even on a node whose requests are full
  # (nodeName skips the scheduler; the kubelet would refuse the 512Mi default).
  kubectl -n "$NS" run "$pod" --image="$IMAGE" --restart=Never \
    --labels="app=netpol-check,netpol-check=$RUN" --override-type=strategic \
    --overrides="{\"apiVersion\":\"v1\",\"spec\":{\"nodeName\":\"$node\",\"containers\":[{\"name\":\"$pod\",\"resources\":{\"requests\":{\"cpu\":\"10m\",\"memory\":\"64Mi\"},\"limits\":{\"memory\":\"64Mi\"}}}]}}" \
    --command -- sleep 300 >/dev/null
  if ! kubectl -n "$NS" wait --for=condition=Ready "pod/$pod" --timeout=120s >/dev/null; then
    echo "FAIL no test pod on $node"; fail=1; continue
  fi
  echo "== from a pod on $node"
  # expected name url
  kubectl -n "$NS" exec -i "$pod" -- sh -s <<'EOF' || fail=1
fail=0
check() {
  expected=$1 name=$2 url=$3
  # time_connect stays 0 until the TCP handshake completes; PostgreSQL and
  # other non-HTTP ports connect and then time out on the HTTP request.
  connect=$(curl -ks -o /dev/null -w '%{time_connect}' --connect-timeout 4 -m 6 "$url" 2>/dev/null)
  rc=$?
  if [ "$rc" = 6 ]; then got=no-dns
  elif [ "$rc" = 28 ] && [ "${connect:-0}" = "0.000000" ]; then got=blocked
  else got=open
  fi
  if [ "$got" = "$expected" ]; then mark=ok; else mark=FAIL; fail=1; fi
  printf '%-4s %-8s %-38s (curl %s)\n' "$mark" "$got" "$name" "$rc"
}
if nslookup -timeout=3 kubernetes.default.svc.cluster.local >/dev/null 2>&1; then
  echo "ok   open     DNS (CoreDNS)"
else
  echo "FAIL blocked  DNS (CoreDNS)"; fail=1
fi
check open    "internet (github.com)"               https://github.com
check open    "Kubernetes API"                      https://kubernetes.default.svc
check open    "Traefik on homelab (hostPort 443)"   https://10.0.0.150
check open    "AdGuard on homelab (host network)"   http://10.0.0.150:53
check open    "PostgreSQL (postgres-rw)"            http://postgres-rw.postgresql.svc:5432
check blocked "openbao"                             http://openbao-active.openbao.svc:8200/v1/sys/health
check blocked "immich"                              http://immich-server.immich.svc:2283
check blocked "nextcloud"                           http://nextcloud.nextcloud.svc:8080
check blocked "mail (stalwart)"                     http://stalwart.mail.svc:8080
check blocked "frigate"                             http://frigate.frigate.svc:8971
check blocked "SeaweedFS S3 on TrueNAS"             http://10.0.0.203:8333
check blocked "TrueNAS 10.0.0.203"                  https://10.0.0.203
check blocked "camera DNAT on the Cudy :8554"       http://10.0.0.224:8554
check blocked "claude-browser MCP (box only)"       http://claude-browser.claude-agents.svc:8931
exit $fail
EOF
  kubectl -n "$NS" delete pod "$pod" --wait=false >/dev/null
done
exit $fail
