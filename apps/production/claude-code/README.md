# Claude Code box

Claude Code runs here 24/7 on the Claude Max subscription. `claude remote-control`
(server mode) dials out to Anthropic. Its sessions appear in the Claude app and
at claude.ai/code under the `homelab` prefix, where new ones can be started too.
Nothing listens, so there is no Service or Ingress.

- Image: `mcr.microsoft.com/devcontainers/python` (Debian trixie with git, ssh,
  python, pipx, jq and passwordless sudo for `vscode`).
- `start.sh` installs `packages.txt` with apt on every start (the root
  filesystem resets) and, once, Claude Code and uv into `~/.local/bin` and
  Node LTS into `~/.local/node`.
  Claude Code updates itself there. mise installs the CLIs in `mise-tools.txt`
  (kubectl, flux, helm, kustomize, kubeconform, sops, yq).
- Browser: the `playwright` MCP server (user scope, re-added at every start)
  is the claude-browser pod in claude-agents, over HTTP. Chromium runs there,
  away from the box's tokens; see apps/production/claude-agents/README.md.
- `box.md` is imported by `~/.claude/CLAUDE.md` and tells every session the
  rules of the box: changes through Git only, no SOPS private key, and which
  places are blocked.
- `job.sh` runs heavy work (duckdb, pandas, model fits, bulk downloads) in a
  pod of its own in claude-agents instead of in the box. The box has 4 GiB and
  may run on homelab2, which has 8 GB in all; one large job there would
  push the node into swap and stall every Remote Control session. The script copies the working
  directory in, runs the command with a memory limit, streams the log, copies
  the results back and deletes the pod.
- The NetworkPolicy keeps the box away from private data: the NAS
  (10.0.0.203), the camera and its DNAT on the Cudy, the IoT network, and the
  frigate, immich, nextcloud, mail, vaultwarden and openbao namespaces.
  Internet, the rest of the LAN and the other namespaces stay reachable.
  Cilium enforces it (`talos/README.md`);
  `scripts/check-network-policies.sh` checks it from claude-agents, which has
  the same egress rules.
- Rights: `view` plus read access to the cluster-scoped and platform kinds
  (`rbac.yaml`), no Secrets; admin only in claude-agents, within the guards in
  its README (`admission.yaml`). OpenBao's store does not serve either
  namespace.
- Home (`/home/vscode`) is the iSCSI claim `claude-code-home`: login,
  `~/homelab-claude-agent`, the SSH key `~/.ssh/id_ed25519` and anything installed under home.
  To keep an extra apt package after restarts, add it to `~/.apt-packages`.
- Sessions start in `~/homelab-claude-agent` in the `auto` mode (`CLAUDE_PERMISSION_MODE`).
- After a restart Remote Control reconnects the sessions it was serving, but
  only within about four hours; after a longer outage they drop out of the
  Claude app, and their transcripts stay in `~/.claude/projects`. The box runs
  in the `claude-code` PriorityClass, so after a drain the scheduler evicts a
  lower-priority pod on another node rather than leave the box Pending.

## Sign in (once, and again when the login expires)

Remote Control needs the full claude.ai login. API keys and `claude setup-token`
tokens do not work.

```bash
kubectl -n claude-code exec -it deploy/claude-code -- tmux attach -t claude
```

1. In the `claude` screen, pick the Claude subscription login, open the URL,
   approve and paste the code back.
2. Type `/exit`; the box trusts its own folder.
3. Remote Control starts and asks `Enable Remote Control? (y/n)`: answer `y`.
4. Detach with `Ctrl-b d`.

`claude auth status` shows the login state.

## GitHub (once)

The box has its own GitHub account, a collaborator on the repositories it
works on; it may push to their main branches. Sign in to GitHub in a
browser as that account, then:

```bash
kubectl -n claude-code exec -it deploy/claude-code -- /opt/box/git-login.sh
```

Open the printed URL, enter the code and authorize. The script makes git push
over HTTPS with that account. Commits are authored as
the repository owner, so they show on the owner's GitHub profile and
contribution graph.
