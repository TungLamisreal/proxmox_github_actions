# MeowCloud: Clean IaC rebuild and two-push CRUD CI/CD demo

## Execution boundaries

This document is a demo plan, **not evidence that a new 01/02 rebuild has passed**.
Workflow 02 destroys *both* database KVM guests and their PostgreSQL data disks, including any live CRUD records.
Do not run it until a tested off-VM database backup exists. CT120 files survive workflow 02, but a dump
stored on the same physical host is not a substitute for an independent copy.

Stop `meowcloud-webhook.service` before the recording. Check GitHub Actions has no active 00/01/02/03/04/05/05R run,
the 3-node PVE cluster and Ceph are healthy, and capacity is back to BE=3 / FE=3.
Do not run workflow 00 to rebuild Golden Images merely because the application code changed;
01 reuses the verified 9100/9101/9102 LXC and 9001/9002/9003 DB templates.

## How 01 works after this patch

Run workflow **01 - Provision MeowCloud Infrastructure** with:
- backend_count=3, frontend_count=3, db_count=2, tf_parallelism=1
- **initial_app_release=baseline** (new default).

`baseline` explicitly provisions pre-baked `meow-backend:latest` and `nginx:alpine`,
with Ansible's minimal `MeowCloud Elastic Infrastructure` page.
It **overrides stale `app-release.json` during deployment**, then publishes the validated
baseline to that manifest after successful VIP and service checks. It leaves
`app-release-v1.json` / `app-release-v2.json` replay snapshots untouched.

01 also installs the new Terraform-generated inventory into CT120's persistent
`/opt/meowcloud-iac/ansible/inventory.ini` and idempotently creates or reuses the
isolated `meowcrud` role / `meowcloud_demo` DB on `.71`, reusing secret state
`/opt/meowcloud-iac/state/meowcrud.json` (or generating it if missing).
It **does not create CRUD tables**: workflow 05 runs the Alembic migration before deploying v1/v2.

If intentionally resuming a published live app on existing infrastructure, select
`initial_app_release=pinned` instead; do not use it for clean baseline recordings.

After workflow 01, verify:
```bash
curl -fsS http://192.168.150.100/ | grep 'MeowCloud Elastic Infrastructure'
curl -fsS http://192.168.150.100/api/health | jq
jq '{backend_image,frontend_image,version}' /opt/meowcloud-iac/state/app-release.json
jq '{backend_count,frontend_count}' /opt/meowcloud-iac/state/autoscale.tfvars.json
test -s /opt/meowcloud-iac/ansible/inventory.ini
jq -e '.initialized == true' /opt/meowcloud-iac/state/meowcrud.json >/dev/null
```

## Record two real Git pushes (not 05R)

The repository's current `main` already contains v2 source. Each code push **must actually
change files under `apps/**` on `main`** to automatically trigger workflow 05.
Both deploy gates must still be enabled:
`MEOWCLOUD_APP_CICD_ENABLED=true` and `MEOWCLOUD_CRUD_DEMO_ENABLED=true`.

Prepare a local, clean clone on `main` after the patch PR is merged.
Do **not** use `git reset --hard` or force push to rewind Git history.
Use Git history's known-good source revisions to make *new* ordinary commits:

### Push 1 — deploy historical CRUD v1 code from a new commit

```bash
git switch main
git pull --ff-only origin main
git status --short
git restore --source=cd32cd5031cc37096fc64d0507d9328ae2329f32 -- apps/backend apps/frontend
git diff --stat
git diff -- apps/backend/app.py apps/frontend/index.html
git add apps/backend apps/frontend
git commit -m "demo: promote CRUD v1 application through CI/CD"
git push origin main
```

Open Actions > `05 - CI/CD Application Deployment`: the run must have `event=push`.
Wait for the build, database migration, rolling deployment, VIP health gates and published
manifest to all pass. Verify `/api/version`, `/version.json` report `crud-v1`.
Create sample products on the v1 UI; refresh and confirm they persist.

The historical v1 replay checkpoint is deliberately pinned to the original commit
`cd32cd...`. A v1 source **rebuild** will have a new SHA, so workflow 05
preserves the old trusted `app-release-v1.json` and reports a notice, instead of
overwriting it or reporting a false failure. The **active** `app-release.json`
still correctly records the new v1 commit.

### Push 2 — deploy CRUD v2 source

Only after the first run passes:
```bash
git restore --source=9acd393474ab20696bec663f3afb4bcc275ed52a -- apps/backend apps/frontend
git diff --stat
git add apps/backend apps/frontend
git commit -m "demo: upgrade CRUD v1 to inventory intelligence v2"
git push origin main
```

Wait for workflow 05 build / migrate / rolling / VIP / checkpoint. Verify both version
endpoints say `crud-v2`; existing v1 products still exist, and `/api/reports/low-stock`
returns the expected low-stock subset. The new v2 checkpoint is recorded by workflow 05.

## Scaling demonstration

With the webhook still paused:
1. Run workflow 03 manually: target=backend, add_backend_count=1,
   add_frontend_count=0, allow_manual_test_without_alert=true, ram_reserve_mb=2048.
2. Verify backend-04 via `http://192.168.150.104:5000/version` and `/db-health`;
   collect a 40-request distribution sample via VIP `/api/health`.
3. Run workflow 04 manually: target=backend, allow_manual_test_without_alert=true.
4. Verify capacity BE=3/FE=3, VIP distributes requests among 01/02/03, DB remains UP,
   and CRUD products are retained.

Do not rerun 03/04 blindly after a partial Terraform apply; reconcile PVE resources,
Terraform state and desired capacity first. Re-enable webhook only when ready for
unattended, alert-driven scaling.

## Non-goals / reproducibility caveats

- The app release is immutable by GHCR digest, but complete **byte-for-byte image
  reproducibility** is not guaranteed by upstream mutable tags (`nginx:alpine`,
  `postgres:15-alpine`, pre-baked `meow-backend:latest`) or external image sources.
- Golden Images, GitHub Actions secrets, CT120 runtime keys/Vault files, PVE/Ceph and the
  Cloudflare tunnel are external prerequisites. A Terraform destroy does not clean them.
- A clean rebuild destroys old DB contents; data persistence is demonstrated **between
  v1 and v2 / scale-out and scale-in on the newly provisioned DB**, not across
  workflow 02.
- Rebuilt VM SSH host identity is ephemeral; the bootstrap SSH behavior matches
  the existing internal-lab Ansible trust model.
