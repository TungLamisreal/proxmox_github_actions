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
