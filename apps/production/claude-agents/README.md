# claude-agents

The Claude Code box's sandbox (apps/production/claude-code). The box is admin
here, within the memory rules in `limits.yaml`. Guards:
- Baseline Pod Security: no privileged pods, hostPath or host network.
- `admission.yaml`, for the claude-code and claude-agents ServiceAccounts:
  - no NFS or iSCSI volumes;
  - no changes to NetworkPolicies or Ingresses;
  - no Flux, External Secrets or cert-manager objects: their controllers
    would apply them with cluster-wide rights;
  - Services only as ClusterIP, without externalIPs, so nothing here opens a
    port on the nodes.
- The ClusterSecretStore `openbao` does not serve this namespace or
  claude-code.
- The egress NetworkPolicy: no NAS, camera, IoT network or apps that hold
  private data. Both nodes' addresses are listed explicitly (API server,
  AdGuard), because Cilium's `0.0.0.0/0` does not cover a pod's own node.
  Cilium enforces it (`talos/README.md`);
  `scripts/check-network-policies.sh` proves it with a throwaway pod here on
  each node.

## claude-browser

The box's browser, in its own pod so a page that breaks Chromium lands
somewhere without the box's Claude, GitHub and cluster tokens.

- Xvfb screen `:99` with headed Chromium, driven by `@playwright/mcp` on 8931
  (streamable HTTP at `/mcp`). The box adds it as the `playwright` MCP server.
  The `vision` capability adds coordinate clicks on screenshots: computer use
  for the browser. Claude Code's own computer use is macOS only.
- Chromium runs as root without its sandbox. The node blocks the unprivileged
  user namespaces the sandbox needs, so the pod (no ServiceAccount token, no
  Secrets, claude-agents egress limits) is the sandbox.
- `/data` is the iSCSI claim `claude-browser-data`: the profile with site
  logins, the Playwright Chromium build, apt downloads and screenshots.
- Only the box reaches 8931. noVNC on 6080 is reachable only from Traefik:
  https://agent-browser.home.bariss.dev/vnc.html (LAN and Tailscale), behind
  the GitHub sign-in. Use it to watch or to sign in to sites by hand.
