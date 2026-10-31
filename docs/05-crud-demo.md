# Workflow 05 — MeowCloud CRUD v1: SQLAlchemy + Alembic + PostgreSQL

## Versioned application demo plan

- CRUD app **v1**: four related PostgreSQL tables (categories, products, customers, orders), Create/Read/Update/Delete. BE Flask and FE web dashboard have identical commit SHA metadata, Docker digest pinned in CT120 release manifest.
- CRUD app **v2**: a SECOND independent Git release planned only *after v1 is running and verified*. BE v2 gains a stock-report endpoint; FE v2 changes its layout/colors and displays that new report. Both versions will be identifiable by immutable images and a release label. Do not mix app v1/v2 with the previously running status-page release.
- PostgreSQL schema migrations are independent of Docker image rollbacks. Future additive migration 0002 is appropriate only when v2 needs extra DB fields. Initial app v1 is revision `0001_initial_crud`.

## Current architecture and limitations

Frontend is an Nginx Docker image served via VIP 192.168.150.100; Backend is Flask/Gunicorn behind VIP /api/ on three or more LXC guests. Data lives only on existing PostgreSQL KVM `192.168.150.71` in separate DB `meowcloud_demo` with role `meowcrud`. Proxmox Terraform, KVM persistent data disk and Golden Images are untouched.

The second KVM .72 has no verified PostgreSQL streaming replication configuration. Do not assume automatic failover or equal data until separately configured and tested.

The browser uses a shared demo token sent in `X-Demo-Token`; use synthetic demo data only.

## Safety gates: NEVER merge and auto-deploy blindly

Workflow 05 application CD requires BOTH GitHub Actions repository variables:
- `MEOWCLOUD_APP_CICD_ENABLED=true`
- `MEOWCLOUD_CRUD_DEMO_ENABLED=true`

Keep the SECOND variable absent/false until you deliberately initialize the separate demo database and approve rollout. PR validation on GitHub-hosted runner builds Docker images locally and tests SQLAlchemy/Alembic migrations against **throwaway PostgreSQL 15**. PR CI never alters the real CT120 or PostgreSQL VM.

## Preflight from CT120 (already passed in lab)

SSH from github-runner to DB1 via trusted host key, and PostgreSQL port 5432 reachable from all BE LXC guests:

~~~sh
runuser -u github-runner -- env HOME=/home/github-runner \
  ssh -o BatchMode=yes root@192.168.150.71 \
  'docker exec meow-postgres pg_isready -U postgres'
runuser -u github-runner -- env HOME=/home/github-runner \
  ANSIBLE_CONFIG=/opt/meowcloud-iac/ansible/ansible.cfg \
  /home/github-runner/.venvs/ansible-2.21/bin/ansible \
  -i /opt/meowcloud-iac/ansible/inventory.ini backends -f 3 \
  -m ansible.builtin.wait_for -a 'host=192.168.150.71 port=5432 timeout=8'
~~~

## Step 1: create a SEPARATE database and owner only (after merging PR)

Use a clean checkout on CT120, as github-runner. Do not overwrite the Terraform runtime or inventory:

~~~sh
git clone --depth 1 --branch main \
  https://github.com/TungLamisreal/proxmox_github_actions.git \
  /home/github-runner/meowcrud-bootstrap
python3 /home/github-runner/meowcrud-bootstrap/scripts/bootstrap_meowcrud.py \
  --confirm CREATE_MEOWCLOUD_DEMO_DB
~~~

The bootstrap makes ONLY the PostgreSQL role `meowcrud`, database `meowcloud_demo`, and an atomic 0600 credential state file at `/opt/meowcloud-iac/state/meowcrud.json`. It **does not create tables**. If a conflicting database or role exists without known state, it fails closed; never remove state to force a retry. `initialized=true` means DB and credentials are ready, not that migrations have run.

Inspect without printing passwords or login tokens:

~~~sh
jq '{initialized,db_host,db_name,db_user}' /opt/meowcloud-iac/state/meowcrud.json
~~~

## Step 2: apply schema via Alembic BEFORE rolling out the new app

Only after Step 1 succeeds, set `MEOWCLOUD_CRUD_DEMO_ENABLED=true` in GitHub Actions variables, and manually run workflow 05 on `main`. Keep other IaC jobs idle; pause the webhook if needed, resume it afterward.

The workflow now has three sequential jobs:

1. **build (CI)**: builds/pushes BE+FE immutable GHCR images and validates CRUD on ephemeral PostgreSQL. The same Alembic revision is executed twice to prove it is idempotent.
2. **migrate (CD)**: on CT120, uses Ansible once on `backend-01` to pull the immutable BE image (contains SQLAlchemy models and Alembic migrations), makes a temporary mode 0600 DB env file, and executes `alembic upgrade head` via a one-shot Docker container on that BE host. It checks `alembic current` and deletes the temporary env file. PostgreSQL records the applied version in `alembic_version`. This job fails closed before rolling out the app if migration fails.
3. **deploy (CD)**: rolling BE1, BE2, BE3 then FE1, FE2, FE3, with per-LXC and VIP version checks plus real DB connectivity. Publish new release digest only after success.

Rollback of Docker images does **not** undo DB schema/data; every migration must remain backward compatible with previously deployed app versions. Never do automatic destructive downgrades. Preserve PostgreSQL backups before potentially destructive migrations.

## Step 3: verify and demo

~~~sh
curl -fsS http://192.168.150.100/api/db-health
curl -fsS http://192.168.150.100/version.json
curl -fsS -H "X-Demo-Token: $(jq -r .demo_token /opt/meowcloud-iac/state/meowcrud.json)" \
  http://192.168.150.100/api/stats
~~~

Via the browser open the MeowCloud URL and enter your demo token (retrieve privately with `jq -r .demo_token ...`). Create a product, customer and order; edit the price/status, reload, prove records persist, then delete order before deleting its referenced product/customer. FK checks intentionally block invalid deletes.

For later scale-out, workflows 03/04 consume the published pinned BE/FE digests and pass the separate DB credentials to new BE hosts. They do NOT run schema migrations per-guest.

## Future phases (not performed by this PR)

- CRUD application v2 via separate PR/commit, new BE feature and distinct FE visual theme; prove app v1 ↔ v2 release behavior with preserved data.
- PostgreSQL replica .72 streaming replication, start with manual controlled promotion and verified read-only standby; Patroni only if automated HA is a stated requirement, and a proper DCS/quorum/fencing mechanism is available.
- PostgreSQL backup/restore tests and RPO/RTO measurement. Replication does not replace backup.
