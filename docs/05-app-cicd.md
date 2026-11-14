# Workflow 05 — MeowCloud application CI/CD (current)

> **Current implementation (2026-11-14).** The source of truth is [05-app-deploy.yml](../.github/workflows/05-app-deploy.yml); this document describes its gates and operational boundaries. Do not confuse it with the removed manual 05R replay workflow.

## Triggers and deployment gates

| Event | What happens |
| --- | --- |
| Pull request touching the workflow's configured paths | Run offline/local build and PostgreSQL CRUD compatibility checks; do not deploy to MeowCluster |
| Push to `main` changing `apps/**` | Build, test and publish versioned Backend and Frontend images to GHCR; production migration and rollout run **only** when both repository variables are `true` |
| Manual `workflow_dispatch` on `main` | Same guarded build/migrate/deploy workflow |

The live migration and deployment jobs require **both** `MEOWCLOUD_APP_CICD_ENABLED=true` and `MEOWCLOUD_CRUD_DEMO_ENABLED=true`. These flags do **not** prevent the build/publish job from running. Changes to documentation alone do not trigger the `apps/**` push workflow.

## Required MeowCluster state

1. Workflow 01 has provisioned the BE/FE/LB pools and initial CRUD database state; for a fresh lab, workflow 06 has completed **one-time** `bootstrap-ha` before workflow 05. On an adopted cluster use workflow 06 `health`, not another `bootstrap-ha`.
2. CT120 contains `/opt/meowcloud-iac/ansible/inventory.ini`, `state/autoscale.tfvars.json` and the protected `state/meowcrud.json`. That state must point to database **writer VIP `192.168.150.100:5432`**, not to DB01 `192.168.150.71`.
3. Backend guests can reach the writer VIP and the images in `ghcr.io`. The workflow checks a healthy Patroni writer endpoint using `scripts/db_ha_vip.py --check` before altering the application.
4. Run no competing infrastructure operations; production jobs share the `meowcloud-iac-state` concurrency group.

See [Unified DB HA](11-db-ha-unified-workflow.md) for the exact workflow 06 modes, security prerequisites and risks. Do not print the `demo_token` or database password in a video or public issue.

## What workflow 05 changes

1. On a GitHub-hosted runner, validate code, build Backend and Frontend images, publish immutable GHCR digests and smoke-test CRUD against disposable PostgreSQL 15.
2. On CT120, run Ansible `09-migrate-crud.yml` through Backend01 against **`192.168.150.100:5432`**. Alembic schema changes are real database operations and must not be treated as image-only deployments.
3. Roll BE then FE images one LXC at a time using current runtime inventory, checking instance health and both versions through the HTTP VIP.
4. Verify `/api/health`, `/api/db-health`, `/api/version`, `/version.json` and authenticated `/api/stats`; publish `state/app-release.json` only after those checks pass.
5. Archive a verified CRUD release snapshot with `scripts/demo_release.py` when its edition/commit gate permits. The 05R replay workflow has been removed; **do not delete** this script because workflow 01 and 05 still use it.

## Read-only checks after a successful release

On CT120, without exposing secrets:

```sh
jq '{version,backend_image,frontend_image}' /opt/meowcloud-iac/state/app-release.json
curl -fsS http://192.168.150.100/api/health
curl -fsS http://192.168.150.100/api/db-health
curl -fsS http://192.168.150.100/api/version
curl -fsS http://192.168.150.100/version.json
```

Use the privately retrieved `demo_token` from `state/meowcrud.json` for CRUD UI/API tests. Browser access uses `X-Demo-Token`; do not store tokens in Git.

## Failure and rollback boundaries

The workflow attempts a **best-effort** rollback to the previously published BE/FE image selection if a rollout or VIP smoke check fails before publishing the new manifest. This does **not** roll back Alembic migrations or database contents and does not guarantee zero dropped in-flight requests. Inspect the actual guests and Patroni state before retrying a failed deploy. Do not use workflow 02 as an application rollback: it destroys lab workloads and may delete database disks.
