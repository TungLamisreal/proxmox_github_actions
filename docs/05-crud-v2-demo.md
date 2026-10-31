# MeowCloud CRUD v2 — Inventory Intelligence

CRUD v1 release commit: `cd32cd5031cc37096fc64d0507d9328ae2329f32`. Frontend v1 version marker: `crud-v1`.

V2 is a separate unmerged Draft PR. The current production app remains v1. When the v2 PR is merged to main, the existing CI/CD variables may automatically deploy it: do not merge before the planned demonstration or controlled rehearsal.

Backend v2 adds one authenticated read-only endpoint:
`GET /api/reports/low-stock?threshold=5`.
Valid thresholds: integers 0–1000. Response: threshold, total, items with stock, SKU, name, category and price. Invalid threshold gives 400; missing token gives 401.

Frontend v2 has a new violet/coral theme and a prominently displayed stock analysis card. It retains all four CRUD sections and reads the same persistent PostgreSQL database. Alembic revision `0001_initial_crud` remains unchanged.

Demo:
1. Provision a known-good lab through workflow 01.
2. Deploy v1 through workflow 05; create two products, one with stock 2, one with stock 40.
3. Deploy v2 through workflow 05; verify edition marker and new stock report (threshold 1 vs 5).
4. Confirm records created on v1 persist on v2.
5. Scale out using 03 and in using 04, testing CRUD availability and persistent data after each.
6. Configure PostgreSQL replica and evaluate automatic failover as a later independent phase.

When recreating lab using workflow 02, back up data first: 02 is destructive and is not a simple app rollback.
