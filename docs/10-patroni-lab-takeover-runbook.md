> **HISTORICAL INCIDENT RECORD — NOT AN ACTIVE RUNBOOK (2026-11-14):** The historical DB06/DB07 sequence below is
> retained only for incident history. Both were consolidated into **one
> [Workflow 06](../.github/workflows/06-db-streaming-replication.yml)**.
> The current `00 → 01 → 06 bootstrap-ha → 05 → 03/04` sequence and
> safe `health` / `diagnose` / `adopt-vip` modes are documented in
> [Unified Database HA](11-db-ha-unified-workflow.md). Do **not** run the old
> DB07 takeover instructions on an already-adopted Patroni cluster. Current workflow 06 modes are `bootstrap-ha`, `health`, `diagnose`, `adopt-vip`, `gateway-repair`, and `resume`; older dispatch commands in this file are retained solely for incident history.

# DB07 — Lab-only Patroni takeover and writer VIP rollout

**Audience:** MeowCloud 3-node PVE/Ceph lab. This stage intentionally causes
PostgreSQL downtime on DB01 and DB02 during container replacement.
**The user has accepted disposable data and confirmed an existing backup.**
This is NOT production automation.

## Validated starting point (November 2, 2026)

Workflow 06 run [41442273794](https://github.com/TungLamisreal/proxmox_github_actions/actions/runs/41442273794):
PostgreSQL DB01 `.71` is primary, DB02 `.72` is streaming, etcd
mTLS voter quorum 3/3 (Observer `.21`), DB07 read-only job 10 passed.
Job 10 correctly reported blockers: standalone `meow-postgres` is active
with Docker `restart: always` on both DBs; PVE HA starts VM271; LB01 and
LB02 have no writer proxy at :5432. **Patroni is not yet running.**

## Workflow 07: exact sequence

The new workflow is [07-db-patroni-lab.yml](../.github/workflows/07-db-patroni-lab.yml).
Run from `main`, **only after merging a green PR**.

### 1. Controlled prep without downtime (optional but useful)

```
Mode: prepare
Confirmation: (blank)
```

Job 01 verifies live three-etcd-voter HTTPS quorum and PVE placement.
Job 02 builds `meowcloud-patroni:pg15-<12-character git sha>` on CT120,
streams `docker save` to the two DB VMs, and stages a private
`/opt/meowcloud-patroni` directory for UID/GID 70. It copies the existing
DB06 CA and Patroni client cert and key locally (mode 0600, directory 0700),
and renders config `patroni.yml` using existing Vault secrets. It runs a
non-networked config/secret readability smoke test without mounting PGDATA.
Legacy PostgreSQL continues running.

### 2. Lab takeover — intentional brief API interruption

```
Mode: takeover
Confirmation: TAKEOVER_PATRONI_LAB
```

This automatically reruns preparation from the **same Git SHA**, then:

1. On DB01 only, inspect the original SQL role and check the old
   `meow-postgres` is the sole running PGDATA owner. Persist a Day-0
   ownership marker `/opt/meowcloud-patroni/takeover.lock`, disable its
   Docker restart policy, stop it and confirm it is stopped.
2. Start `meow-patroni` as UID 70, network=host, exact existing PGDATA bind
   `/opt/pgdata/data:/var/lib/postgresql/data` and private client TLS files.
   Wait for Patroni's `/primary` endpoint to be HTTP 200 and SQL recovery
   state to be false. DCS is responsible for leader election.
3. Only after DB01 is confirmed leader, disable and stop DB02's legacy
   Postgres, start Patroni on **the existing standby PGDATA**, and require
   `/replica` HTTP 200 plus SQL recovery state true.
4. Assert one and only one SQL primary, one actively streaming replica, the
   same physical PostgreSQL system identifier and `wal_log_hints=on`
   on both sides.

**Partial failure:** The workflow intentionally does **not** automatically
restart a former writable database if Patroni fails after contacting DCS;
doing so can cause two writers. The marker disables Workflow01's old Docker
setup. Capture `docker logs --tail 100 meow-patroni` and check
`docker inspect --format '{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}' meow-postgres`
on the failing DB. After resolving config or service state, rerun the
**same commit** or a reviewed fix; never delete PGDATA, the etcd state or
CA merely to force success. A fresh `main` commit changes the Docker image
tag and may require operator image reconciliation on a partially adopted VM.

### 3. Verify real automatic failover readiness

```
Mode: health
Confirmation: (blank)
```

The read-only health playbook allows **either** DB01 or DB02 to be the
current leader. It will NOT use the old Workflow06 assumptions that DB01
must always be primary. It rejects a live old standalone container or a
Docker auto-restart policy for `meow-postgres`.

Proxmox HA for VM271 **can remain**: guest boot must start only
`meow-patroni` (Docker restart=always), not `meow-postgres`.
The Day-0 `05-setup-database.yml` fails intentionally when the
`takeover.lock` exists, to avoid resurrecting the standalone container.

### 4. Prepare PVE firewall for the DB writer proxy

Before proxy installation, add/confirm narrowly scoped inbound rules
to **both DB VMs'** `sg_db`:

```
IN ACCEPT -source +ipset_lb -p tcp -dport 5432,8008 -log nolog
```

`ipset_lb` includes LB01 `192.168.150.61`, LB02 `.62`, and the
Keepalived VIP `.100`. Limit inbound TCP8008 to LBs and necessary
management/monitoring only. Do NOT expose TCP8008 to the public internet.

To allow backend clients to connect to the **existing LB VIP** `.100:5432`,
also require on **both LBs'** `sg_lb`:

```
IN ACCEPT -source +ipset_backend -p tcp -dport 5432 -log nolog
```

The backend network must also allow outbound `.100:5432`; its current
output policy is assumed ACCEPT but must be verified. `pve-firewall compile`
is only a syntax check, not end-to-end connectivity. Check PVE IP filters
include the VIP on LB net0 as already configured.

### 5. Install only the database writer proxy

```
Mode: proxy
Confirmation: ENABLE_DB_WRITER_PROXY
```

Workflow07 first proves a healthy Patroni cluster; then from LB01 and LB02
it checks the two databases' `/primary` API on port 8008. It refuses to
deploy if there is not exactly one leader or the firewall blocks access.
Only then it installs a dedicated HAProxy TCP frontend on `0.0.0.0:5432`,
checking the Patroni API `/primary` on port 8008 and forwarding PostgreSQL
traffic to the single current leader. Nginx HTTP, Cloudflare Tunnel,
Keepalived VIP and backend DB_HOST **are deliberately unchanged**.

This stage sets up the infrastructure for end-to-end tests. Application
backend is currently hardcoded to DB01 `.71` in the release workflow and
runtime config. That cutover must be updated separately after verifying
TCP 5432 from the backend to `.100` and its firewall.


## Incident recovery: Patroni DB01 waits for etcd /v3 HTTP gateway (Nov 2)

After the first live takeover in run
[41445859845](https://github.com/TungLamisreal/proxmox_github_actions/actions/runs/41445859845),
DB01's `meow-postgres` was cleanly stopped with restart=no and
`meow-patroni` was **still running**. A user-provided DB01 Docker log
reported repeat HTTP `404 page not found` on
`https://192.168.150.{71,72,21}:2379/v3`, followed by
`waiting on etcd`. The 3-voter etcdctl native gRPC quorum previously
passed, but Patroni 4.0.6's etcd3 backend uses the **HTTP JSON gRPC gateway**:
`POST /v3/cluster/member/list`. Thus native gRPC tests alone did not
validate this crucial integration.

### Permanent repair for new and existing deployments

- `ansible/templates/meowcloud-etcd.yml.j2` explicitly sets
  `enable-grpc-gateway: true`. This is applied during every future
  DB06 bootstrap, including a clean 00→01→06 setup.
- `ansible/playbooks/16-verify-etcd-quorum.yml` now tests native etcdctl
  quorum **and** HTTP `/v3/cluster/member/list` on all three endpoints over
  TLS. A missing gateway fails the health job even if native gRPC is healthy.
- Existing voters can be fixed **in place**, one at a time, with
  `22-enable-etcd-gateway.yml`: require intact quorum first; change only
  `enable-grpc-gateway: true` in the owned etcd config; restart each voter
  only if config changed; require HTTP 200 and 3-member JSON before
  touching the next voter; then verify HTTP with the real Patroni client
  certificate from **both database hosts**. No etcd data or PostgreSQL
  data is deleted.

### Actual recovery run order (PR29 must be merged)

**First run** Workflow07 on updated `main`:
```
mode: gateway-repair
confirmation: ENABLE_ETCD_HTTP_GATEWAY
```

This intentionally restarts the etcd voter daemons **sequentially**, not
the DB containers. The voter cluster maintains a 2/3 majority during
normal single-member restarts. It fails closed if a voter cannot restart
or provide a valid JSON member list. Do not invoke `dcs-bootstrap` as a
workaround: it does extra package and PKI tasks.

DB01's already-running `meow-patroni` should stop logging `404`,
acquire the single DCS leader lease and start its **original PostgreSQL 15
data directory**. If it doesn't, read `docker logs --tail 100 meow-patroni`
on DB01, or Workflow07 `mode=diagnose`, **before continuing**.

**Then run** Workflow07 on the same updated `main`:
```
mode: resume
confirmation: RESUME_PATRONI_LAB
```

The resume playbook **does not rebuild/restart DB01 Patroni** or change its
PGDATA. It checks that DB01's old `meow-postgres` is stopped with
restart=no, the original Patroni image is running with restart=always,
and both REST `/primary` and SQL confirm the writer. It then reads the
original `meowcloud-patroni:pg15-<old-SHA>` image tag from DB01 and
uses that exact image on DB02, already imported in the initial run.
DB02 is confirmed standby before being transferred. Finally, the
existing failover-safe PostgreSQL health job tests the resulting cluster.

**Never rerun** the old failed GitHub Actions takeover job on the old
commit, and do **not** restart the old `meow-postgres` on DB01 while
Patroni is handling the DCS lease. DB02 should remain on its legacy
standby until resume passes every gate.

### Fail-closed follow-up

`mode=diagnose` (blank confirmation) records read-only Docker status,
Patroni logs and file ownership from DB01/DB02 without requiring a
healthy etcd quorum. New job03/06 failures auto-run this diagnostic.

If DB01 still has no HTTP 200 `/primary` after gateway repair, treat it
as a **new** startup blocker and inspect the actual Patroni log; don't
reboot DB01, bypass role checking or recreate PostgreSQL volumes to
make a workflow appear green.

## After a green gateway-repair but DB01 /primary returns 503

Workflow07 run
[41452992169](https://github.com/TungLamisreal/proxmox_github_actions/actions/runs/41452992169)
**successfully** rolled three etcd gateway configurations and verified every
`POST /v3/cluster/member/list` via mTLS using the real Patroni certificates
from both DB VMs, plus 3/3 native gRPC quorum. Therefore, re-running
`gateway-repair` or resetting etcd is not a sensible fix for a **later**
DB01 `GET http://127.0.0.1:8008/primary => 503`.

HTTP **503** here proves that Patroni REST is responding but **does not**
establish that PostgreSQL is down, that data are lost, or that DB01 has the
leader lease. It may be a replica, a node that cannot open PostgreSQL, a
node waiting for leadership or a node with another DCS/PG issue. The
previous HTTP 404 fault was solved in the gateway, but there is no
corresponding positive PostgreSQL leadership check yet.

### Resume after an orderly lab shutdown

1. Start the Proxmox nodes and verify physical PVE quorum/Ceph health,
   then DB01 `.71`, DB02 `.72` and Observer `.21`, and CT120 runner.
   If a VM was managed by PVE HA it may boot automatically; **check**
   before starting it manually.
2. Confirm only the DB07-managed `meow-patroni` container can use
   DB01's PGDATA. Its old `meow-postgres` must remain stopped,
   `restart=no`. Do not remove or reinitialize PGDATA.
3. From Workflow07 on updated `main`, run **`mode=diagnose`** with
   blank confirmation. It collects a *read-only*, allowlisted snapshot:
   DB01/DB02 Patroni `/primary`, `/replica`, `/` and `/cluster`
   (only role/state/member names), `pg_isready` from the Patroni
   container, Docker lifecycle, ownership of PGDATA and TLS, and
   bounded redacted Patroni logs. No API secrets or full cluster
   configuration are output. The interpretation distinguishes a
   `503` from a dead HTTP service.
4. Based on the **current** PostgreSQL startup error and cluster role,
   address the specific blocker. Never assume gateway repair
   promotes PostgreSQL by itself; do not force a promotion based
   solely on `GET /primary = 503`.
5. Only once DB01 `/primary` is HTTP 200 and SQL confirms
   `pg_is_in_recovery() = false` should Workflow07
   `mode=resume` transition DB02. The resume playbook already
   rejects a non-primary DB01. Afterward run `mode=health`.

Do not re-run Workflow 00, 01, 06 bootstrap, or the old destructive
`takeover` in a partially adopted DB01/legacy DB02 configuration.
An orderly shutdown preserves the data/configuration but does **not**
complete the Patroni transition. A fresh diagnostic after boot is needed.

## Final lab demonstration (NOT automated in this PR)

Once health, writer proxy and application VIP migration are confirmed:

1. Use a fresh test record and verify through the application API.
2. Power off VM271 and measure Patroni promotion time until DB02 becomes the
   sole leader. Observer + DB02 still form a 2/3 etcd quorum.
3. Write and read a new record via `.100:5432`, prove there is no second
   writer and no data regression. Async streaming can lose the last
   unreplicated transaction (nonzero RPO).
4. Boot VM271, verify it **rejoins as standby**; if divergence prevents
   `pg_rewind`, inspect Patroni logs and follow the documented lab reseed
   process rather than allowing an independent primary.

**No DB01 power-off drill is safe before application DB_HOST is switched
to the VIP and health shows a single Patroni leader.**

## Nov 3, 2026: adopted Patroni is now the live DB owner — non-destructive Workflow07

**Current observed topology:** DB01 VM271 (192.168.150.71) and DB02 VM272
(192.168.150.72) run Patroni on existing Ceph RBD PGDATA; the primary can
be **either node** after failover. etcd voters are DB01, DB02 and Observer
(192.168.150.21). LB01 (.61) and LB02 (.62) expose a writer-only HAProxy
on TCP 5432 behind the Keepalived VIP 192.168.150.100. The application
backend has already been switched to the VIP in the live environment.
The legacy `meow-postgres` is stopped with Docker restart=no. These
facts are observations of the lab, **not** a guarantee that every future
configuration/release artifact is synchronized.

**Never run `prepare` or `takeover` on this adopted cluster.** The
Workflow07 gate now inspects both database VMs and refuses either mode if
`takeover.lock` or `meow-patroni` exists — before it transfers any Docker
image or stages `patroni.yml`. This is deliberately stricter than the
historical one-time handover. Do not delete the marker to bypass the gate.

**Use one GitHub Actions workflow with separate sequential jobs:**

- `health` (now the default, empty confirmation): gate inventory and
  etcd native gRPC **plus** HTTPS JSON /v3 gateway (3 voters), Patroni
  SQL/REST single-writer and streaming verification (DB01/DB02 roles can
  reverse), live `pg_hba_file_rules` loopback-replication audit on both DBs,
  and end-to-end `/api/db-health` through the application VIP.
- `diagnose` (empty confirmation): **independent of a failing etcd gate**.
  Collects a bounded, redacted snapshot of both DB guests' Docker
  lifecycle, Patroni REST role, SQL recovery/streaming, TLS file ownership
  and recent errors. This is the manual diagnostic button and performs
  no restarts, promotions or writes.
- Other one-time modes are intentionally opt-in and remain guarded:
  `prepare`, `takeover`, `resume`, `gateway-repair`, `proxy`. These
  are not routine HA health checks. `gateway-repair` can restart etcd
  voters sequentially; `proxy` can reload HAProxy. Avoid both during a
  healthy demo unless a reviewed change specifically requires them.
- On any failed non-diagnostic job, `failure-diagnostic` gathers the same
  read-only failure context without requiring successful etcd health.
  A failed diagnostic is not permission to restart a former leader.

**Previous incidents permanently reflected in the source:**

1. Patroni 4.0.6 was stuck `waiting on etcd` with HTTP 404 on `/v3`:
   native `etcdctl` health alone is insufficient; validate the HTTPS
   JSON gateway and valid 3-member response as well.
2. PostgreSQL physical replication via loopback was rejected by HBA:
   `host all all 127.0.0.1/32` does *not* cover `replication`. The
   canonical template now explicitly includes
   `host replication meow_replication 127.0.0.1/32 scram-sha-256`
   and `host replication meow_replication ::1/128 scram-sha-256`.
   A **read-only live HBA check** catches drift without re-rendering the
   running Patroni config.
3. DB02 was promoted when DB01 was stopped. Health gates must accept
   DB02 as writer, require exactly one SQL writer and streaming standby,
   and never restart `meow-postgres` or write the existing PGDATA.
4. DB01 VM stop/cancel exposed Ceph quorum/RBD I/O loss. Proxmox HA
   quorum and Ceph MON quorum are independent; check `ceph -s` and guest
   health before another planned demo failover. Ceph HEALTH_OK does not
   repair an ext4 journal aborted during the storage outage.
5. Runner SSH host keys must be verified and enrolled in
   `/home/github-runner/.ssh/known_hosts` **against fingerprints seen on
   the actual DB consoles**. Do not disable SSH host-key checks to force
   `diagnose` or image staging to succeed.

**Important outstanding deployment regression:** the historical
`.github/workflows/05-app-deploy.yml`, `ansible/playbooks/09-migrate-crud.yml`
and bootstrap release helpers still contain assumptions about the old
fixed writer `192.168.150.71`. A future full app rollout must first
reconcile those paths with the live `192.168.150.100:5432` writer
endpoint. Workflow07 health does not deploy the application or rewrite
application runtime state.

Do not treat static PR CI or a green Docker image build as a live
failover test. Test Workflow07 `health` and `diagnose` on the running
lab, without intentionally shutting down DB VMs during CI.
