# MeowCloud: controlled PostgreSQL 15 streaming replication (lab)

## Observed before implementation — 2026-11-02

- DB01 VM271 (192.168.150.71) is an independent primary with database `meowcloud_demo`, `wal_level=replica`, zero WAL senders.
- DB02 VM272 (192.168.150.72) is also an independent primary, with only default `postgres` database, no WAL receiver.
- DB02 data disk: ext4 mounted at `/opt/pgdata`, 32 GiB total, ~30 GiB free.
- The backend is currently configured using `meowcrud.json` with DB host 192.168.150.71; **no automatic failover** or stable DB write endpoint exists.
- Proxmox HA for VM271 can restart a DB VM; it cannot arbitrate PostgreSQL leadership.

## Git/Ansible rollout — manually triggered GitHub workflow 06

A new **workflow_dispatch-only** `.github/workflows/06-db-streaming-replication.yml` runs reviewed code from the GitHub Actions checkout on CT120 using its generated runtime inventory. It does **not** pull/reset the dirty `/opt/meowcloud-iac` checkout. All configuration intent lives in Ansible; DB data lives on persistent Ceph storage.

1. Merge the reviewed PR only after the CI YAML checks pass. Create a repository **Actions secret** named `POSTGRES_REPLICATION_PASSWORD`: strong randomly generated alphanumeric/underscore/dash value, at least 16 chars. It is not checked into Git and must not be shared in chat.
2. Start **06 - DB Streaming Replication (Gated)** with phase `audit`. This is read-only and shows real PostgreSQL roles, data mount, database names, and PostgreSQL physical cluster identifiers.
3. Select `prepare-primary` with `confirmation=APPLY_DB_REPLICATION` and a unique backup reference (6–64 chars, e.g. `lab-20261102-db01`). First, playbook 09 performs a complete, non-destructive SQL `pg_dumpall` into `/opt/pgdata/backups/pre-replication-<ref>.sql` on DB01; then playbook 11 configures a replication role via secret, permits only DB02/32 in `pg_hba.conf` and caps physical slot WAL retention to 1 GiB. It reloads PostgreSQL config, **does not restart or stop DB01**, and does not touch DB02.
4. Start `bootstrap-standby` with the same secret and a new valid backup reference (or the existing one if known to be complete). The workflow backs up DB01 again if needed, makes sure DB01 is ready, then playbook 12 stages physical `pg_basebackup -R -C -S meow_db02` under `/opt/pgdata/replica-stage` on DB02. It uses an encrypted-at-rest GitHub Actions secret, transported to Ansible via a private temporary extra-vars file. A protected local `.pgpass` in PGDATA is readable only by PostgreSQL.
5. Once staging completes, playbook 12 stops **only DB02's** container, renames `/opt/pgdata/data` to `/opt/pgdata/data.before-replica` (no delete), moves staged data into place, restarts DB02's container, then checks recovery, WAL receiving and identical PostgreSQL system identifier to DB01. On a failed cutover it attempts to restore the original DB02 directory and restart its prior container. If any staging or rollback directory remains, the next bootstrap fails closed for manual inspection.
6. The workflow automatically runs the strict audit after bootstrap. A separate `verify` phase reruns it later. DB01 must remain primary, DB02 recovery/streaming, physical identifiers equal.

**Lab caveats:**
- The SQL backup is on the same Ceph-backed DB01 disk, not an independent offsite/PBS backup. For real availability and disaster recovery also test an external PBS restore. A nonempty `pg_dumpall` file is evidence that a dump completed, not evidence of a tested restore.
- Only DB02's existing independent database is replaced, after preserving the old data directory. DB01 `meowcloud_demo` is never rewritten during bootstrap.
- The slot limits WAL retention to 1 GiB; prolonged disconnection can still cause a standby to need rebuilding. Monitor `pg_replication_slots`, disk and lag.
- If connection from DB02 to DB01 TCP/5432 is blocked by `sg_db`, the playbook stops *before the cutover*. Fix firewall in the policy-as-code source (not by disabling firewall).
- A partial basebackup may leave `replica-stage` and the `meow_db02` slot on primary. Do not blindly remove live directories or slots. Diagnose both first. The workflow is deliberately not an unattended retry mechanism.
- Run `05-setup-database.yml` only after checking how it interacts with the existing standby; it still starts PostgreSQL using the same persistent volume, but does not manage replication secrets or leadership.

## Separate next milestone — automatic failover, not yet delivered

Streaming replication by itself **does not** promote DB02 or move the backend DB endpoint. To meet "DB01 power off -> DB02 becomes writable and apps reconnect" safely, next implement a single PostgreSQL leader controller using e.g. Patroni with an odd-numbered DCS quorum (etcd/Consul) and a TCP write endpoint (HAProxy/Keepalived or a tested equivalent), plus fencing and coordination with Proxmox HA VM271. Two DB VMs alone cannot rule out split-brain under a network partition. Design, test and review this as a separate PR, only after streaming replica and restore have passed.

## Do not

- Do not run Terraform destroy, workflows 00/01/02 or manually wipe PGDATA.
- Do not run `git reset --hard` or `git clean -fd` on the dirty CT120 runtime checkout.
- Do not present PostgreSQL streaming replication as equivalent to automatic failover or backup.

