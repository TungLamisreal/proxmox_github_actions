# MeowCloud PostgreSQL replication rollout (gated)

## Verified on 2026-11-02

- DB01 (VM271, 192.168.150.71): PostgreSQL 15-alpine, recovery=false, wal_level=replica; `meowcloud_demo` exists, no WAL sender.
- DB02 (VM272, 192.168.150.72): PostgreSQL 15-alpine, recovery=false, no WAL receiver.
- Both are **independent primaries**. `db_role=standby` is only an inventory label.
- `05-setup-database.yml` currently starts two independent PostgreSQL Docker containers; do not use it to initialize or promote a standby.
- The CRUD configuration still directs backend traffic to DB01 (.71). A database promotion by itself will not reroute backend connections.
- Proxmox HA VM271 does NOT arbitrate PostgreSQL leadership.

## Rollout sequence

1. **Read-only audit now:** `ansible-playbook -i inventory.ini playbooks/10-audit-db-replication.yml`. The optional `-e pg_expect_replication_ready=true` adds strict verification after replication is implemented.
2. **Data protection:** verify a consistent DB01 backup (logical and/or app-consistent PBS restore point) and independently inspect/backup DB02 before changing either. Preserve the existing `/opt/pgdata/data`. Save the verified backup identifier.
3. **DB01-only setup when approved:** put a high-entropy `vault_postgres_replication_password` in the existing encrypted `ansible/group_vars/databases/vault.yml`, never plaintext in Git or chat. From a *clean reviewed checkout*, run: `ansible-playbook -i inventory.ini playbooks/11-configure-db-primary-replication.yml -e pg_apply_primary_replication=true -e pg_backup_reference=<verified-backup-id>`. This only creates a replication login (if missing), permits DB02/32 in PG HBA, reloads configuration, and refuses incompatible WAL settings. No restart, no standby cutover.
4. **DB02 bootstrap is a separate future reviewed PR:** physical `pg_basebackup -R` needs EMPTY target PGDATA; DB02 currently has populated independent data. Back up and compare DB02, verify volume space, design staged data-dir cutover and rollback, and test physical lineage and read-only recovery. Never overwrite current DB02 data blindly.
5. **Automatic failover is not enabled:** decide on Patroni+DCS odd quorum or a compatible repmgr+witness plan, plus fencing, coordinated Proxmox HA policy and a stable DB write endpoint for all backends. Two database VMs alone cannot safely promote in a network partition. Replication alone cannot provide automatic failover.
6. **Acceptance tests:** run strict audit for shared PostgreSQL system ID, one primary and one streaming standby; verify CRUD read/write, replica read-only, RTO/RPO, reconnect, rejoin old primary without split brain, backup/restore.

## Immutable deployment boundaries

- Ansible and Git own configuration; PostgreSQL PGDATA is durable state, not a disposable golden-image layer.
- No changes are applied automatically by this PR to workflow 01/03/04 or database deployment playbook 05.
- Never run destructive workflows 00/01/02 to configure replication.
- CT120's working checkout currently has local changes that block `git pull`. Do NOT use `git reset --hard` or `git clean -fd` on that runtime checkout. Reconcile separately before deployment.
