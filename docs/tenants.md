# Tenants

Apps that should not be public live in a separate, private Git repository and
reach the cluster as a [Flux tenant](https://fluxcd.io/flux/installation/configuration/multitenancy/).
This repository is the platform: it runs Flux, the shared infrastructure and
the public apps, and it hands each tenant a namespace, a ServiceAccount, a Git
source and the Kustomizations that build the tenant's repository.

The one tenant, `homelab-private`, holds my own apps and prototype projects
that I don't want public, with their databases and image automation. The split
between platform and apps is deliberate: this repository shows how the cluster
is built, and the private one decides what runs on it besides the public apps.

```
homelab-infra (public)                     homelab-private (private)
tenants/homelab-private/
  namespace.yaml       namespace           base/         shared app bases
  rbac.yaml            ServiceAccount      production/   built by private-prod
  git-ssh-secret.yaml  deploy key          dev/          built by private-dev
  sync.yaml            GitRepository  ───► components/   kustomize components
                       + Kustomizations
```

## What a tenant gets

`tenants/<name>/` holds, in the tenant's namespace:

- `GitRepository` `<name>`: the private repository's `main` branch over SSH,
  with a deploy key from OpenBao (`git-ssh-secret.yaml`, key
  `secret/<name>/git-ssh` with `identity`, `identity.pub` and `known_hosts`).
  The key has write access when the tenant runs image automation.
- `Kustomization`s that build directories of that repository (`./production`,
  `./dev`). Each sets `serviceAccountName`, so kustomize-controller applies
  the tenant's objects as the tenant's ServiceAccount, not as itself, and
  depends on the platform Kustomization whose services it uses (`prod`, `dev`).
- The ServiceAccount and its RBAC (`rbac.yaml`).

The Flux Kustomization `tenants` (`clusters/homelab/tenants.yaml`) applies
`tenants/` after `infrastructure`, so the External Secrets Operator and its
ClusterSecretStore exist.

## What a tenant may use

Tenants build on platform services the same way the public apps do:

- PostgreSQL: `DatabaseRole`, `Database`, `PushSecret` and `ExternalSecret`
  objects in the `postgresql` namespace, against the main cluster `postgres` or
  the pantry `postgres-pantry`, with the shared password store
  (`apps/production/postgresql/README.md`).
- Secrets: ExternalSecrets against the ClusterSecretStore `openbao`.
- Storage: democratic-csi iSCSI and NFS volumes, pinned as described in
  `AGENTS.md`.
- Ingress: Traefik and the `*.home.bariss.dev` wildcard DNS.
- The VPN proxy (`apps/production/gluetun`): its NetworkPolicy admits
  Traefik and every namespace labelled `home.bariss.dev/vpn-proxy-client:
  "true"`, so a tenant namespace that browses through it sets that label in
  the tenant's repository.
- Image automation: ImageRepositories, ImagePolicies and an
  ImageUpdateAutomation in the tenant's namespace that push to the tenant's
  repository; `$imagepolicy` markers name `<tenant namespace>:<policy>`.

## RBAC

The `homelab-private` tenant is trusted and bound to `cluster-admin`. It
creates its own Namespaces and pinned PersistentVolumes, and its database
objects live in the shared `postgresql` namespace; rules narrow enough to mean
something would have to list those namespaces here, in public. The split is
about what the public repository shows, not about isolating a hostile tenant.

The Flux controllers run without the multi-tenancy lockdown flags
(`--no-cross-namespace-refs`, `--default-service-account`): platform
HelmReleases reference HelmRepositories in `flux-system`, and tenant
Kustomizations depend on platform Kustomizations there. Tenant Kustomizations
therefore name their ServiceAccount explicitly.

## Adding a tenant

1. Create the private repository with a `production/` (and optionally `dev/`)
   directory that `kustomize build` accepts.
2. Generate a deploy key (`ssh-keygen -t ed25519 -N '' -f <name>`), add its
   public half to the repository's deploy keys (write access if the tenant runs
   image automation) and store it in OpenBao:

   ```sh
   ssh-keyscan github.com > known_hosts
   bao kv put secret/<name>/git-ssh identity=@<name> identity.pub=@<name>.pub known_hosts=@known_hosts
   ```

3. Copy `tenants/homelab-private/` to `tenants/<name>/`, rename, and list it
   in `tenants/kustomization.yaml`.

## Moving apps into a tenant

An app that moves from this repository into a tenant keeps running if its
objects keep their names and namespaces. Flux deletes what a Kustomization no
longer builds, so protect the objects first: annotate them with
`kustomize.toolkit.fluxcd.io/prune: disabled` (or suspend the old
Kustomization), let the tenant's Kustomization apply them (it takes them over),
then remove the annotation or resume.
