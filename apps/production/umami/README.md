# Umami

Web analytics for the blog: Umami (`ghcr.io/umami-software/umami`, no
official Helm chart), no cookies, data in the database `umami` on the main
PostgreSQL cluster (`apps/production/postgresql/databases/umami.yaml`, backed
up with it). Image automation follows its 3.x releases; Umami migrates its
database at start.

## Where it answers

- **Dashboard**: `https://analytics.home.bariss.dev` (LAN and Tailscale),
  behind the GitHub sign-in (`middleware.yaml`), then Umami's own sign-in.
- **Tracker**: two paths on the blog's own host, through the Cloudflare tunnel
  (`tracker-route.yaml`), renamed with Umami's own settings
  ([bypass ad blockers](https://umami.is/docs/bypass-ad-blockers)):
  `/p.js` (`TRACKER_SCRIPT_NAME`) and `/api/e` (`COLLECT_API_ENDPOINT`). A
  blocker that stops them stops the blog. Nothing else of Umami is public.
  The route takes the fixed priority 200; a route for the blog itself on
  `blog.bariss.dev` takes 100, so the order never depends on the rules'
  length.
- The NetworkPolicy admits only Traefik.

The country comes from `CF-Connecting-IP`, the visitor's address that
Cloudflare sets (`CLIENT_IP_HEADER`); Umami does not store addresses.

## First sign-in

Umami creates the user `admin` with the password `umami`. Sign in once,
then Settings, Profile, Change password, and keep the new one in Bitwarden.
The database holds it, so a restore keeps it.

## Adding a site

Settings, Websites, Add website; then, in the site's HTML head:

```html
<script defer src="/p.js" data-website-id="<website id>"></script>
```

The script takes the host from its own URL, so it posts to the blog's
`/api/e`.

## Secrets

| OpenBao key | Secret | Written by |
| --- | --- | --- |
| `secret/postgresql/umami-db` | `umami-db` (`DATABASE_URL`) | PushSecret `umami-db` ([PostgreSQL](../postgresql/README.md)) |
| `secret/umami/umami-app` | `umami-app` (`APP_SECRET`) | PushSecret `umami-app` (`generated-secrets.yaml`), through the OpenBao role `umami-secrets` |

Both are generated once and never typed.
