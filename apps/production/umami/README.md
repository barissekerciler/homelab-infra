# Umami

Web analytics for the blog: Umami (`ghcr.io/umami-software/umami`, no
official Helm chart), no cookies, data in the database `umami` on the main
PostgreSQL cluster (`apps/production/postgresql/databases/umami.yaml`, backed
up with it). Image automation follows its 3.x releases; Umami migrates its
database at start.

## Where it answers

- **Dashboard**: `https://analytics.home.bariss.dev` (LAN and Tailscale),
  behind the GitHub sign-in (`middleware.yaml`), then Umami's own sign-in.
- **Tracker**: the blog runs on Cloudflare Pages. A Pages Function in the
  blog's repository (`functions/`) proxies `blog.bariss.dev/p` and
  `blog.bariss.dev/api/e` to `umami.bariss.dev`, as Umami's
  [bypass ad blockers](https://umami.is/docs/bypass-ad-blockers) guide
  describes; visitors only ever see the blog's host, so a blocker that stops
  the tracker stops the blog. The names come from Umami's own settings:
  `/p` (`TRACKER_SCRIPT_NAME`) and `/api/e` (`COLLECT_API_ENDPOINT`).
- `umami.bariss.dev` comes through the Cloudflare tunnel and answers only
  those two paths, in the tunnel's config and in `tracker-route.yaml`;
  everything else there is a 404.
- The NetworkPolicy admits only Traefik.

The Function forwards the visitor's address as `X-Real-IP`. Cloudflare turns
that into `CF-Connecting-IP` for a request to the same zone, and Umami reads
it (`CLIENT_IP_HEADER`) for the country; Umami does not store addresses.

## First sign-in

Umami creates the user `admin` with the password `umami`. Sign in once,
then Settings, Profile, Change password, and keep the new one in Bitwarden.
The database holds it, so a restore keeps it.

## Adding a site

Settings, Websites, Add website; then, in the site's HTML head:

```html
<script defer src="/p" data-website-id="<website id>"></script>
```

The script takes the host from its own URL, so it posts to the site's own
`/api/e`; the site needs the same proxy as the blog.

## Secrets

| OpenBao key | Secret | Written by |
| --- | --- | --- |
| `secret/postgresql/umami-db` | `umami-db` (`DATABASE_URL`) | PushSecret `umami-db` ([PostgreSQL](../postgresql/README.md)) |
| `secret/umami/umami-app` | `umami-app` (`APP_SECRET`) | PushSecret `umami-app` (`generated-secrets.yaml`), through the OpenBao role `umami-secrets` |

Both are generated once and never typed.
