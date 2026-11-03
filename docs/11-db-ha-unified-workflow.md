# MeowCloud Unified Database HA — Workflow 06

**Current source of truth:** `.github/workflows/06-db-streaming-replication.yml`.
The former `07-db-patroni-lab.yml` has been **removed**. All PostgreSQL
replication, etcd quorum, Patroni takeover, writer HAProxy, health and manual
diagnosis are now jobs in one manually dispatched Workflow 06.

## Fresh deployment / live demo sequence

1. **00** — build golden images when needed.
2. **01** — provision exactly two KVM databases, three separate etcd voters,
   existing backend/frontend/LB/Observer and initial isolated `meowcrud`
   database. Day-0 SQL bootstrap uses DB01 `192.168.150.71` *only to create*
   the role and database before Patroni exists. The application's
   `/opt/meowcloud-iac/state/meowcrud.json` now contains `db_host:
   192.168.150.100` and port `5432`; a Day-0 baseline app may report
   DB unavailable until the following workflow is complete.
3. **06** — select `bootstrap-ha`, enter exact confirmation
   `BOOTSTRAP_MEOWCLOUD_DB_HA`. This is a **one-time operation**:
   DB-only snapshot → streaming replication if missing → verify identical
   physical lineage → etcd three voters and mTLS → HTTP /v3 gateway check
   → stage Patroni PG15 image/config/TLS → exclusive PGDATA takeover
   DB01 then DB02 → single-Primary/streaming-Replica verification
   → validate localhost replication HBA → install writer HAProxy
   on LB01 (.61) and LB02 (.62), port 5432 → check from Backend01 to
   `192.168.150.100:5432` → atomically publish VIP in credential state
   → rolling reconcile Backend Docker environments → verify all backends.
4. **05** — build/deploy CRUD frontend/backend and apply Alembic migrations
   through `192.168.150.100:5432`. The migration and rollout preflights
   refuse a direct `.71` source state.
5. **03 / 04** — autoscale out/in only after the configured writer VIP and
   live Patroni single-writer streaming checks pass. New backends inherit
   `DB_HOST=192.168.150.100`.

**Do not run workflow 06 `bootstrap-ha` again after the first Patroni
takeover**. The preflight explicitly refuses a `takeover.lock` or any
existing `meow-patroni` container on either DB guest *before* legacy
Postgres or DCS are changed.

## Already-running cluster: migration without reinstall

Run Workflow 06 `health` (blank confirmation). This checks
3/3 etcd gRPC plus Patroni-compatible HTTPS JSON gateway, actual SQL and REST
roles on DB01/02 (either can be Primary), replication stream and data
lineage, effective HBA and Backend01 TCP access to writer VIP.

If `meowcrud.json` still has `db_host=192.168.150.71`, run Workflow 06
`adopt-vip` with confirmation `ACTIVATE_DB_WRITER_VIP`. That operation
checks Patroni and writer proxy first, updates only CT120's protected
credential state, rolls out existing Backend Docker env one at a time,
and verifies that every Backend points to `.100`. It **does not stop,
reinstall, rebuild, reinitialize or promote either database**.

Run Workflow 06 `diagnose` (empty confirmation) whenever a failure occurs.
It is independently callable even if the etcd readiness check fails.
It logs bounded, redacted Docker/SQL/Patroni REST status with **no write**.

Maintenance modes `gateway-repair` and `resume` require distinct explicit
confirmations. They are *not* regular health checks and should not run on
the recovered live demo without a specific incident and reviewed runbook.
`gateway-repair` may restart etcd voters one at a time. `resume` is only
for an interrupted first-time takeover and assumes the historical DB01
first-Primary handover stage, not a DB01 that rejoined as Replica.

## Pre-existing firewall security-group prerequisites

Terraform Workflow 01 attaches `sg_db`, `sg_lb` and
`sg_observer` but currently **does not own their rule bodies**.
Therefore Workflow 06 does not silently overwrite PVE firewall policies.
Before the first `bootstrap-ha`, check the existing rules permit:
- DB01/DB02/Observer etcd mTLS client/peer traffic on TCP 2379 and 2380,
  restricted to the three voters; Patroni 2379 HTTPS gateway must be reachable.
- Both LB IPs `.61`/`.62` reaching both DBs' Patroni REST `8008`
  and PostgreSQL `5432`; keep `8008` private.
- Backend IPs reaching both LB services through VIP `.100:5432`.

Existing firewall-denied traffic fails **closed** in the DCS,
HAProxy and Backend01 TCP readiness jobs. See
`docs/08-etcd-dcs-runbook.md` and the existing PVE security-group
configuration for source-scoped rule details. A completely fresh PVE
installation requires this firewall groundwork; the repo does not yet
automate the security-group rule definitions.

## Proxmox HA (Observer, both DB VMs and LB pair)

Workflow 01 manages HA services `ct:121`, `vm:271`, `vm:272`,
`ct:141` (LB01) and `ct:142` (LB02). It creates soft positive
node-affinity preferences:

- Observer `ct:121` prefers node1.
- DB01 `vm:271` prefers node2; DB02 `vm:272` prefers node3.
- LB01 `ct:141` prefers node1; LB02 `ct:142` prefers node2.

It additionally creates non-strict **negative resource-affinity** rules
`meow-etcd-voter-spread` for `ct:121,vm:271,vm:272` and
`meow-lb-spread` for `ct:141,ct:142`. These are soft spread policies,
**not enforced anti-affinity guarantees**: a node outage must not prevent
HA recovery onto a surviving node. Workflow 06's live three-distinct-node
placement gate remains required before its initial etcd bootstrap.

All five managed HA services use `failback=0` and
`auto-rebalance=0`; a host recovery does not automatically relocate
them back. Terraform ignores subsequent HA-managed node relocation for
the DB, Observer, and LB guests.

Workflow 02 removes these HA registrations and rules **before** Terraform
destroy. For legacy guests with missing owned node-affinity rules,
`--allow-owned-missing-rule` is limited to destroy and requires exact
matching Terraform state ownership plus live PVE guest type/name checks.
Any unknown HA rule, conflicting policy or identity mismatch still blocks
destroy. `ct:120` is never managed by the script. The Proxmox HA manager restarts/migrates
VMs; **Patroni** elects database Primary/Replica and protects the
writer lease. Do not manually promote a VM due solely to its HA name.

For an existing lab where VM272 is not yet in PVE HA, updating this PR
alone makes **no live HA change**. After merging, apply
`python3 scripts/pve_cluster_policy.py --mode apply-ha` from CT120's
repository checkout (requires existing PVE access) and verify
`ha-manager status` lists `vm:272` as started. The policy refuses
ambiguous or externally-owned HA entries rather than replacing them.

## Critical regression guardrails

- Patroni 4.0.6 initially failed `waiting on etcd` with HTTP 404
  `/v3`. Native `etcdctl endpoint health` alone was insufficient; the
  workflow verifies the TLS JSON gateway with 3-member responses.
- PostgreSQL `host all all 127.0.0.1/32` does **not** grant the special
  replication database. Canonical Patroni config includes physical
  replication HBA for `127.0.0.1/32` and `::1/128`. The health job
  audits live `pg_hba_file_rules` and fails on drift; it does **not**
  overwrite an already-running Patroni config.
- Existing `meow-postgres` containers must stay stopped with restart=no;
  `meow-patroni` owns PGDATA and Docker restart=always.
- Proxmox HA and Ceph MON quorum are different: a stopped/fenced VM
  may trigger failover, while a concurrent Ceph outage can cause RBD
  errors/aborted ext4 journals. Confirm `ceph -s`, PGs
  `active+clean`, and no recent storage I/O errors before demonstrating
  node failure.
- HAProxy chooses a writer with Patroni `/primary` HTTP checks, not a
  fixed DB01 hostname. The application never targets the current leader
  directly.
- **No assumed zero-RPO:** asynchronous physical streaming may lose recent
  unreplicated transactions. Measure RTO/RPO in live tests.

## Note on CI vs live validation

Pull request CI checks YAML, Ansible syntax, image compatibility, safe DAG
and state transitions using disposable test inputs. These checks **do not**
reach live Proxmox, stop VM271, test genuine Patroni election under outage,
or prove the current DB guest HBA is fixed. After merging, run Workflow 06
`health` and `diagnose` against the working lab first. Existing live
Patroni must remain installed throughout.

## Workflow 02: partial RBD destroy recovery (2026-11-04)

The destroy workflow defaults to Terraform `tf_parallelism=2` and **rejects
values above 2** (parallelism 1 remains available). It first checks RBD
storage listings from both DB hosts, verifies the cluster, unregisters only
owned HA services, then executes Terraform destroy.

If the first destroy fails, it does not blindly retry. The read-only scan
of `general_pool` and `db_pool` must prove an exact VM271/VM272 RBD
reference is missing. Before detaching that missing reference, the script
also verifies the canonical local Terraform state, matching live guest
type/name/tags, non-HA registration, stopped status, no lock, and no
unexpected disks. Existing disks are **never detached** or deleted by
the recovery helper. Only when these conditions pass is Terraform
destroy retried once. Ceph or permission errors, mismatched identity,
busy guest and unrelated Terraform failures remain red.

After Terraform returns successfully, the workflow requires Terraform
state to be empty and checks for remaining MeowCloud VM/CT IDs and RBD
images (including orphan `general_pool:vm-171-disk-0`). Unexpected
survivors keep workflow 02 red and need deliberate inspection; it never
automatically removes orphan RBD images. DB golden templates remain
outside the deletion scope and are verified afterward.

This is a guarded recovery path, **not proof** that a new live destroy
has succeeded. Do not run 01 until 02 finishes green and orphan
investigation is complete.
