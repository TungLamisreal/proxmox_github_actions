# MeowCloud documentation index

> **Updated 2026-11-14.** This index distinguishes active workflows from historical engineering notes. Actual source-of-truth behavior is defined by GitHub Actions YAML and IaC source files in `main`, not by an old example command in a runbook.

## Current operation and demos

| Document | Scope |
| --- | --- |
| [Unified database HA — workflow 06](11-db-ha-unified-workflow.md) | Current one-time `bootstrap-ha`, safe `health`/`diagnose`, Patroni and writer VIP `192.168.150.100:5432` |
| [Application CI/CD — workflow 05](05-app-cicd.md) | Current GHCR build, explicit deploy gates, Alembic migration and rolling releases |
| [CRUD v2 inventory demo](05-crud-v2-demo.md) | Implemented low-stock feature and controlled application version demonstration |
| [Autoscaling Grafana demo](12-grafana-autoscale-demo.md) | Current dashboard and journal-panel notes; verify runtime state before recording |
| [Security monitoring](15-security-monitoring-runbook.md) | Loki/Alloy, PVE firewall logs and optional workflow 01 post-provision job |
| [Clean rebuild / two-push demo rehearsal](01-to-05-clean-rebuild-demo.md) | Historical rehearsal plan, with current workflow order stated at the top; workflow 02 is destructive |

Workflow sequence for a **fresh, deliberately rebuilt** lab: optional `00` Golden Images → `01` provisioning → one-time `06 bootstrap-ha` → `05` application deployment → `03/04` autoscaling. Use `06 health` on an existing adopted cluster; do not rerun `bootstrap-ha`. Workflow `02` destroys lab workloads and requires proven backups and a separate deliberate decision.

## Historical development and incident records — retained

| Document | What it records |
| --- | --- |
| [Initial CRUD v1 rollout](05-crud-demo.md) | Original pre-Patroni CRUD and Alembic rollout; direct `.71` commands are obsolete |
| [Early PostgreSQL streaming replication](06-db-replication-rollout.md) | Before Patroni, earlier DB06 rollout |
| [Original Patroni design](07-patroni-ha-design.md) | Proposed architecture, former DB06/DB07 modes |
| [etcd DCS bootstrap and incident](08-etcd-dcs-runbook.md) | Early etcd-only milestone and recovery observations |
| [DB07 preflight](09-patroni-handover-preflight.md) | Historical read-only Patroni candidate checks |
| [DB07 takeover and incident](10-patroni-lab-takeover-runbook.md) | Historical controlled takeover, gateway and recovery notes |

Historical documents contain old options and staged deployment assumptions. **Do not execute their commands on the currently adopted Patroni environment** without reconciling with the current source.

## Retired material

- `00R - Resume DB Golden Images` and `05R - Replay App Release` workflow files were removed on 2026-11-14. Their historical commits remain available in Git history, but they are not runnable Actions workflows.
- The retired 05R click-by-click document was removed to avoid directing operators to a nonexistent GitHub Actions workflow. `scripts/demo_release.py` must remain: workflows 01 and 05 still call it.

## Safety boundaries

Never expose `state/meowcrud.json` passwords or tokens, Ansible Vault secrets, GitHub tokens or private keys in public documentation/screenshots. CI PASS is not proof of a successful live Proxmox/Ceph/Patroni test; validate the actual lab before claiming HA, recovery or RTO/RPO results.
