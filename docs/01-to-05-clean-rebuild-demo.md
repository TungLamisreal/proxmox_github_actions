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


## Guest placement, HA Manager, and safe staged startup

- **PVE node startup staggering:** Day-0 workflow 01 executes
  `python3 scripts/pve_cluster_policy.py --mode set-startup-delay` **before Terraform creates guests**.
  It writes `startall-onboot-delay=60` via the PVE node configuration API to
  node1, node2, node3 and verifies the value on each. The setting is stored on
  PVE nodes, **not on the guest**, so workflow 02 does not delete it; 01
  reasserts the desired value on every replay.
- The LXC guest definitions include `start_on_boot=true` for regular host
  startup. Terraform 01 still uses `tf_parallelism=1` to serialize provisioning.
- **PVE HA Manager:** Only **Observer CT121** and **DB Primary VM271** are
  registered after all 01 configuration and service checks pass:
  `python3 scripts/pve_cluster_policy.py --mode apply-ha`. The controller uses
  idempotent HA groups with `nofailback=1`, `restricted=0`, home-node priority:
  - `meow-observer`: node1 preferred (2), node2/node3 allowed (1)
  - `meow-db-primary`: node2 preferred (2), node1/node3 allowed (1)
- CT120 Auto-VM is **already user-managed** and is never changed by this script.
  Tunnel CT131-133 and Nginx LB CT141-142 retain distinct initial placements by
  Terraform (no HA registration). BE CT151+ and FE CT171+ remain replaceable
  stateless cattle and are **not** Proxmox HA services. DB2 VM272 is **not**
  configured for PostgreSQL streaming replication/promotion; do not claim DB HA.
- VM/CT HA recovery is **restart after safe fencing/quorum**, not uninterrupted
  live migration. `ignore_changes = [node_name]` protects Observer placement,
  and `ignore_changes = [node_name, clone]` protects DB placement when HA
  relocates them. Database IP filter follows the VM's current node.
- **IMPORTANT: PVE `startall-onboot-delay=60` governs ordinary PVE host
  auto-start; HA-managed guests are scheduled by the HA Manager separately.**
  It does **not** guarantee a cluster-wide one-minute gap, nor that ct:120,
  ct:121 and vm:271 (all HA-managed) start sequentially. Do a controlled
  power-on/recovery test with Ceph and RAM monitoring; do not rely on this
  setting alone to eliminate host memory spikes.
- Existing PVE/HA groups are read first. Unexpected HA resources, policy
  mismatch, a node offline, or lost quorum => the policy script **fails closed**.
  PVE versions lacking the requested HA group options require an explicit
  compatibility review; do not bypass safety guards.
- Before Terraform destroy, workflow 02 runs
  `python3 scripts/pve_cluster_policy.py --mode remove-ha`, which unregisters
  only CT121 and VM271 and removes only its two owned groups.
  **Guest data and VM disks are not deleted by the HA policy script**;
  workflow 02's subsequent Terraform operation is destructive and
  still requires independently tested PostgreSQL backups.
- Inspect the runtime without changing it:
  ```bash
  python3 scripts/pve_cluster_policy.py --mode audit
  ssh root@192.168.150.49 'ha-manager status'
  ssh root@192.168.150.49 'pvesh get /cluster/ha/resources --output-format json'
  ```

## Save live data before workflow 02

The pre-v2 PostgreSQL dump from an earlier checkpoint is at
`/opt/meowcloud-iac/backups/meowcloud_demo_pre_v2_20261031_225608.dump` on CT120.
Connect WinSCP SFTP to `192.168.150.20:22` as root or an authorized account,
browse `/opt/meowcloud-iac/backups/`, and copy the dump to a separate Windows disk.
It is only a *pre-v2* backup; copy a new final dump if later records matter.
Use `pg_restore -l` to verify the archive is readable, and separately test
restoring into an isolated scratch database before deleting the real DB KVM disks.
Never treat a backup inside the same PC as independent disaster recovery.

## How 01 works after this patch

Run workflow **01 - Provision MeowCloud Infrastructure** with:
- backend_count=3, frontend_count=3, db_count=2, tf_parallelism=1
- **initial_app_release=baseline** (new default).

`baseline` explicitly provisions pre-baked `meow-backend:latest` and `nginx:alpine`,
with the **official `Welcome to nginx!` page** from the preloaded Nginx image; there is no custom HTML bind mount at baseline.
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
curl -fsS http://192.168.150.100/ | grep 'Welcome to nginx!'
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

- **IaC repeatability != byte-for-byte reproducibility.** Golden Templates and runtime configuration define the same desired topology when the same code, inputs, provider versions and Golden Images are used. The application is pinned by GHCR digest after deployment, but baseline upstream tags (`nginx:alpine`, `postgres:15-alpine`, pre-baked `meow-backend:latest`) are not globally immutable and rebuilt Golden Images may differ over time.
- Terraform provider versions are constrained to the versions verified in a passing workflow (bpg/proxmox 0.119.0, local 2.5.3, external 2.4.2). To pin provider **checksums** too, generate `terraform/.terraform.lock.hcl` from a trusted runner via `terraform providers lock -platform=linux_amd64` and commit the lockfile; do not hand-write its hashes.
- For stronger repeatability, version and checksum all Golden Image archives, Docker image digests and Packer/OS package inputs, test `terraform plan` after re-apply, and keep secrets/Vault/SSH keys outside Git. No guarantee can be made against upstream outages or failed hardware.
- Golden Images, GitHub Actions secrets, CT120 runtime keys/Vault files, PVE/Ceph and the
  Cloudflare tunnel are external prerequisites. A Terraform destroy does not clean them.
- A clean rebuild destroys old DB contents; data persistence is demonstrated **between
  v1 and v2 / scale-out and scale-in on the newly provisioned DB**, not across
  workflow 02.
- Rebuilt VM SSH host identity is ephemeral; the bootstrap SSH behavior matches
  the existing internal-lab Ansible trust model.
