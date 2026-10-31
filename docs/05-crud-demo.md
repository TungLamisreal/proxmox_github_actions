# Workflow 05 — CRUD Demo with PostgreSQL

## Architecture and safety
- Frontend Nginx container: UI at http://192.168.150.100/
- Backend Flask container: HTTP API at VIP /api/
- PostgreSQL: existing DB primary VM .71, in a **new isolated database meowcloud_demo** (role meowcrud).
- Four tables: categories, products, customers, orders. FK constraints preserve references. Never drop existing databases.
- DB-02 (.72) is not shown as a configured streaming replica in the current playbooks; do NOT assume its data is synchronized.
- No template rebuild, no changes to Terraform, Ceph or PostgreSQL volumes.
- New app uses a demo access token in X-Demo-Token header. Do not store real customer data in this public lab.

IMPORTANT: Workflow 05 CD is gated by TWO repository Actions variables:
  MEOWCLOUD_APP_CICD_ENABLED=true
  MEOWCLOUD_CRUD_DEMO_ENABLED=true
Leave the second variable **unset or false** until the app DB has been bootstrapped.
Merging the PR can build Docker images but cannot deploy them while the second variable is false.

## 1. Verify existing system, PostgreSQL, and TCP access from Backend LXC

CT120 read-only commands:

~~~sh
jq '{backend_count,frontend_count}' /opt/meowcloud-iac/state/autoscale.tfvars.json
jq -r .version /opt/meowcloud-iac/state/app-release.json

runuser -u github-runner -- env HOME=/home/github-runner \
  ssh -o BatchMode=yes -o ConnectTimeout=10 root@192.168.150.71 \
  'docker exec meow-postgres pg_isready -U postgres'

runuser -u github-runner -- env HOME=/home/github-runner \
  ANSIBLE_CONFIG=/opt/meowcloud-iac/ansible/ansible.cfg \
  /home/github-runner/.venvs/ansible-2.21/bin/ansible \
  -i /opt/meowcloud-iac/ansible/inventory.ini backends -f 3 \
  -m ansible.builtin.wait_for -a 'host=192.168.150.71 port=5432 timeout=8'
~~~

If BE cannot connect to .71: STOP. Inspect DB firewall/security group and PostgreSQL listener. Do not weaken the firewall without inspecting rules.

## 2. Explicit one-time isolated DB bootstrap (after merge)

Use a **separate clean checkout** of merged main on CT120 (or the GitHub Runner workspace). Do not overwrite runtime Terraform state/inventory in /opt/meowcloud-iac. The script requires adjacent db/schema.sql. As github-runner:

~~~sh
env HOME=/home/github-runner python3 scripts/bootstrap_meowcrud.py \
  --confirm CREATE_MEOWCLOUD_DEMO_DB
~~~

This uses the existing SSH identity to DB-01, creates role meowcrud and database meowcloud_demo only if not already present, and applies additive schema. It writes sensitive credential state in /opt/meowcloud-iac/state/meowcrud.json with mode 0600. If interrupted, rerun from the same user/config; NEVER delete the saved state merely to retry.

~~~sh
jq '{initialized,db_host,db_name,db_user}' /opt/meowcloud-iac/state/meowcrud.json
~~~

Use a private terminal to display the browser login token; never screenshot or paste the DB password:

~~~sh
jq -r .demo_token /opt/meowcloud-iac/state/meowcrud.json
~~~

Preserve this secret file outside the Git repository. This app demo will later provide database contents for backup/restore testing.

## 3. Enable deploy and verify

After PR checks succeed and the database is ready, check no IaC workflow is running. Temporarily stop CT120 webhook to avoid an automatic competing scale event. Set GitHub Actions repository variable MEOWCLOUD_CRUD_DEMO_ENABLED=true. Manually run workflow 05 from main.

- Hosted runner: builds/tests Docker images against an ephemeral Postgres 15, checking create/read/update/delete, access-token enforcement and FK delete protection.
- CT120 runner: checks secret file and BE-to-DB TCP access, rolls out one BE/FE at a time, checks VIP /api/db-health and /api/stats, then publishes release digest.
- If deployment fails, workflow 05 attempts rollback to the previously published image version; DB data is NOT rolled back automatically.

CT120 checks after workflow Success:

~~~sh
curl -fsS http://192.168.150.100/api/db-health
curl -fsS http://192.168.150.100/version.json
curl -fsS -H "X-Demo-Token: $(jq -r .demo_token /opt/meowcloud-iac/state/meowcrud.json)" \
  http://192.168.150.100/api/stats
~~~

Browser: open your MeowCloud site, enter the token, perform Create/Read/Update/Delete. Make a category, product, customer and order; an order prevents deletion of referenced product/customer until the order is deleted.

After demonstration, restart CT120 webhook and check systemctl is-active meowcloud-webhook. A new Backend scale-out guest gets the same image digest and local DB credentials from CT120.

## Limitations
This is a lab console with one shared token, not a production multi-user application. Use fake test customers. DB KVM .72 replication is not proven. Schema migration and backup/restore require a separate, controlled workflow. No live data or PostgreSQL volumes are destroyed by this application PR.
