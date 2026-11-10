# Umami

Web analytics for the blog: Umami (`ghcr.io/umami-software/umami`, no
official Helm chart), no cookies, data in the database `umami` on the main
PostgreSQL cluster (`apps/production/postgresql/databases/umami.yaml`, backed
up with it). Image automation follows its 3.x releases; Umami migrates its
database at start.

## Where it answers

- **Dashboard**: `https://analytics.home.bariss.dev` (LAN and Tailscale),
  behind the GitHub sign-in (`middleware.yaml`), then Umami's own sign-in as
  `admin`.
- **Tracker**: two paths on the blog's own host, through the Cloudflare tunnel
  (`tracker-route.yaml`): `/p.js` (Umami's `/script.js`) and `/api/e` (the
  tracker posts there; Traefik maps it to `/api/send`). A blocker that stops
  them stops the blog, and the paths avoid the names block lists know
  (`umami`, `script.js`, `/api/send`). Nothing else of Umami is public.
  These routes take the fixed priority 200; a route for the blog itself on
  `blog.bariss.dev` takes 100, so the order never depends on the rules'
  length.
- The NetworkPolicy admits only Traefik and the admin password Job.

The country comes from `CF-Connecting-IP`, the visitor's address that
Cloudflare sets (`CLIENT_IP_HEADER`); Umami does not store addresses.

## Adding a site

Sign in, Settings, Websites, Add website; then, in the site's HTML head:

```html
<script defer src="/p.js" data-website-id="<website id>"></script>
```

The script takes the host from its own URL, so it posts to the blog's
`/api/e`.

## Secrets

All generated, nothing typed (`generated-secrets.yaml`): PushSecrets write a
fresh value into OpenBao once, through the role `app-secrets`
([OpenBao](../openbao/README.md#generated-app-secrets)).

| OpenBao key | Secret | Used for |
| --- | --- | --- |
| `secret/postgresql/umami-db` | `umami-db` (`DATABASE_URL`) | role `umami` |
| `secret/umami/umami-app` | `umami-app` (`APP_SECRET`) | sign-in tokens, visitor hashes |
| `secret/umami/umami-admin` | `umami-admin` (`password`) | the dashboard user `admin` |

Umami's first migration creates `admin` with the password `umami`. The Job
`umami-admin-password` signs in with it once and sets the generated one; when
`admin` no longer signs in with `umami`, it does nothing. Copy the admin
password from OpenBao (`secret/umami/umami-admin`) to Bitwarden.
