# DB07: Patroni preflight and build-only PostgreSQL 15 candidate

**State:** read-only audit and container-image compatibility candidate. **NOT** a
Patroni deployment, leadership takeover, or automatic failover.

## Verified baseline before this PR

GitHub Actions run [41437469264](https://github.com/TungLamisreal/proxmox_github_actions/actions/runs/41437469264)
reported success for PostgreSQL streaming audit, DB06 etcd bootstrap and 3/3
mTLS quorum. Placement: CT121 Observer node1, VM271 DB01 node2, VM272 DB02
node3 at verification time. Both PGDATA volumes remain owned by
`meow-postgres` Docker containers; DB01 primary .71, DB02 streaming replica
.72. The workload has NOT failed over.

## Motivation: one and only one PostgreSQL lifecycle authority

Current `ansible/playbooks/05-setup-database.yml` creates
`meow-postgres` with `restart_policy: always`; also
`scripts/pve_cluster_policy.py` automatically manages `vm:271` in PVE HA.
Starting a separate Patroni-managed PostgreSQL before coordinating those
controllers risks data-directory corruption or two writable primaries.

Even with a 3/3 etcd quorum, there is NO safe DB01 power-off drill until
PostgreSQL ownership, client-side writer routing, and peer fencing are tested.

## New read-only workflow

After PR merge, GitHub → Actions → `06 - Database HA Pipeline` →
`Run workflow` on `main`:

- Mode: `patroni-preflight`
- Confirmation: blank

The job sequence is: 01 read-only DB topology → 05 strict streaming verification
→ 06 existing lifecycle/DCS/LB audit → 09 independent mTLS quorum verification
→ **10 read-only ownership report**.

Job 10's `db07_patroni_preflight.py` reads:
- Both Docker containers' **running state**, restart policies, image name and
  exact PGDATA bind mounts (no environment variables or password output).
- The mounted filesystem path `/opt/pgdata` on both DB VMs.
- Live Proxmox HA state for VM271 (via CT120 SSH to PVE).
- Whether an LB proxy is listening on port 5432 on LB01 and LB02.

It prints `READINESS: BLOCKED` and specific `BLOCKER` entries. Its job can
be **green** if the diagnostic audit itself completed successfully; green does
**NOT** authorize a takeover. The CLI has `--require-ready` for a future
fail-closed enforcement gate. The code is strictly read-only.

The existing DB06 `patroni-stage` mode remains config-only. This PR does not
automatically run it and does not change the current secrets permissions.

## Build-only Patroni image

The candidate `docker/db-patroni/Dockerfile` extends the same official
`postgres:15-alpine` image lineage used by the two existing DB containers,
adds a private Python environment with **pinned Patroni and psycopg2**, and
runs as UID 70 (`postgres`). PR CI builds this image and smoke-tests its
PostgreSQL major version, `pg_rewind`, Patroni Python import and non-root UID.

**It is not published or deployed**. Do NOT place it over live PGDATA yet.
Before activation, pin its reviewed base image and resulting build digest, and
verify the runtime bind mounts and PostgreSQL 15 minor-level compatibility.
The existing `patroni.yml.j2` refers to container paths
`/var/lib/postgresql/data` and `/usr/local/bin`, so the runtime must mount
the corresponding host data disk and PostgreSQL binaries unchanged.

**Credential ownership:** Stage currently stores Patroni config and TLS
client key as root-only mode 0600. A UID 70 Patroni process cannot read these
files unless a separately reviewed, least-privilege bind-mount/UID/GID plan
is implemented. Do not loosen keys globally. Also ensure the Patroni REST API
port 8008 is restricted to the two LBs and the monitoring/automation endpoints
as necessary, never exposed publicly.

## Required handover design milestones

1. **Resilience backup:** Prove a fresh, restorable backup independent from
   the shared Ceph storage and record the recovery procedure. Current
   replication is asynchronous, so zero data loss is not guaranteed.
2. **Lifecycle fencing:** Write a cutover policy that prevents the old
   `meow-postgres` container from restarting on either VM, even after PVE HA
   restarts VM271. Reconcile the `05-setup-database.yml` provisioning path
   and automatic recovery paths before changing restart policy.
3. **Patroni runtime:** Deploy a pinned, tested image with the original
   container data mounts, UID/GID, protected secrets and PostgreSQL 15 tooling,
   but no concurrent old PostgreSQL process. Start DB01 first, verify sole
   DCS leader and `/primary`; only then start DB02, verify follower.
   Preserve physical identity; never `initdb` / `pg_basebackup` live PGDATA.
4. **Writer routing:** Stage HAProxy TCP 5432 on LB01/LB02. Use Patroni
   `/primary` port 8008 for exclusive writer selection; verify failover
   through Keepalived VIP `192.168.150.100:5432`. Add exact scoped firewall
   rules, then switch backend DB_HOST and Workflow 05 guardrails atomically.
5. **Failover rehearsal:** Confirm old data plus new writes, stop DB01
   in a controlled test, observe DB02 promotion and RTO/RPO, restart DB01,
   and confirm it rejoins as standby with `pg_rewind`. Explicitly test
   loss of quorum and verify no second writable primary.

**Do not shutdown DB01 or call `patroni-stage` a takeover.**
