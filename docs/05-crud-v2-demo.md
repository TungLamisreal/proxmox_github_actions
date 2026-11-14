# MeowCloud CRUD v2 — Inventory Intelligence

> **Implemented in current `main` (2026-11-14).** The earlier Draft-PR and "DB replica later" language was a historical plan, not the present state. This page is a concise record of the v1-to-v2 application demonstration; use [workflow 05](05-app-cicd.md) and [workflow 06 DB HA](11-db-ha-unified-workflow.md) for current operations.

Original CRUD v1 reference commit: `cd32cd5031cc37096fc64d0507d9328ae2329f32` (edition marker `crud-v1`). The current repository includes the `crud-v2` Frontend and Backend functionality.

## Features delivered

- Authenticated, read-only Backend endpoint: `GET /api/reports/low-stock?threshold=5`.
- Allowed threshold: an integer from 0 through 1000; invalid threshold returns HTTP 400, missing or invalid `X-Demo-Token` returns HTTP 401.
- Response includes `threshold`, matching `total`, and up to 100 items with SKU, name, stock, price and category.
- Frontend includes the inventory analysis panel and retains CRUD operations for categories, products, customers and orders.
- Inventory report uses the existing schema; Alembic revision `0001_initial_crud` remains the initial CRUD migration.

## Controlled demo scenario

1. In a disposable/rehearsal lab, follow the guarded infrastructure order `01 → 06 bootstrap-ha → 05`. Do not run `bootstrap-ha` again on an already adopted Patroni cluster.
2. If demonstrating the historical v1-to-v2 Git rollout, deploy a reviewed v1 source commit through workflow 05; create sample records and note the version.
3. Deploy v2 through workflow 05; verify `/api/version`, `/version.json` and the low-stock report using a private demo token.
4. Confirm data from v1 persists after the image change, then test workflows 03 and 04 to demonstrate scale-out and scale-in.
5. Use workflow 06 `health` to verify PostgreSQL/Patroni HA; replication and automatic failover are no longer "future phases" of this application demo.

Workflow 02 is destructive; preserve an independently verified database backup before any rebuild. Removing the retired 05R workflow does not remove the ability to demonstrate CI/CD through new, ordinary Git commits.
