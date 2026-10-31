# Workflow 05R: controlled baseline / CRUD v1 / CRUD v2 replay

## Purpose and boundaries

Workflow `05-replay-demo.yml` is a manual **release replayer**. It uses current golden-image containers for `baseline` and immutable, previously deployed GHCR digests for `v1` and `v2`. It never runs Terraform, Docker builds, PostgreSQL migrations, database restores or workflow 02. The regular workflow 05 remains responsible for **new Git commits → build/test → GHCR → Alembic → rolling deployment**.

All IaC workflows share `meowcloud-iac-state` concurrency so they cannot mutate the same live pool simultaneously.

| Choice | Backend image | Frontend image | Expected version |
| --- | --- | --- | --- |
| baseline | pre-baked local `meow-backend:latest` | local `nginx:alpine`, serves legacy Nginx web UI | no `/version` contract |
| v1 | `state/app-release-v1.json` pinned digest | same checkpoint FE digest | exactly `cd32cd5031cc37096fc64d0507d9328ae2329f32` |
| v2 | `state/app-release-v2.json` pinned digest | same checkpoint FE digest | first successful v2 deploy SHA |

The current `state/app-release.json` is not replaced until every selected guest has passed Ansible health checks and the VIP responds with the selected release. A timestamped pre-replay copy of that manifest is taken first. A failed rollout triggers best-effort Ansible restoration of the **previously published** release; this is not a substitute for checking the VIP after failure.

The replay workflow requires:
- GitHub `main`, self-hosted CT120 runner, existing runtime inventory.
- A complete snapshot for the desired version, with both images pinned by `@sha256:`.
- `meowcrud.json` initialized and reachable DB1 for v1/v2.
- Local Docker baseline images on **all** current FE/BE guests before selecting baseline.

## First use

1. Merge the release-replay PR containing this workflow (no changes under `apps/**`, so the normal workflow 05 push trigger does not deploy).
2. Go to **Actions → 05R - Replay MeowCloud App Release**.
3. Select `v1`, keep `dry_run=true`, click **Run workflow**. It checks release manifest and prerequisites but does not touch containers or release state.
4. If dry-run is green and you intentionally want to switch, select the desired release, set `dry_run=false` and enter exactly `REPLAY-MEOWCLOUD`. This actively changes **all** BE and FE containers using Ansible, one host at a time.
5. Confirm the target release via `curl -fsS http://192.168.150.100/version.json | jq` for v1/v2 and `/api/db-health` plus data persistence.
6. Do **not** use `02` as an application rollback. Workflow 02 may delete DB VM storage.

## Version snapshots

Already taken on CT120:

```sh
cp -p /opt/meowcloud-iac/state/app-release.json /opt/meowcloud-iac/state/app-release-v1.json
```

After a successful normal workflow 05 deployment, its new final step verifies FE and BE edition and saves `app-release-v1.json` or `app-release-v2.json` automatically. The initial v2 GHCR image does **not** exist before first v2 merge/deploy; replaying `v2` without a v2 snapshot must fail closed.

## Full video rehearsal plan

1. Prepare a known-good lab. Workflow 01 provisions/configures infrastructure **and can select the last published application release**, so don't assume 01 alone always displays legacy baseline.
2. Replay `baseline` using 05R if the desired initial shot is the legacy page.
3. Replay `v1` using 05R. This is a **manual pinned-release deployment**, not a new Git push; show how v1 appears and create sample products, customer and order.
4. Merge/push v2 changes to `main` (after CI approval) to trigger regular **05 automatic build, migration and rolling update**, record the GitHub Actions run and new `crud-v2` UI.
5. Verify that data created on v1 exists in v2 and exercise the new `/api/reports/low-stock?threshold=5` endpoint.
6. Run 03 scale-out then 04 scale-in, validating health/version, traffic and persistent PostgreSQL records.
7. Need another take? 05R can replay baseline/v1/v2 after the v2 snapshot exists, without rewriting Git history or resetting the database.

**Safety:** A replay intentionally changes the running site when `dry_run=false`. Use only synthetic demo data. The PostgreSQL custom-format backup is separate from application-image snapshots and must be kept safely. Never paste `demo_token` or DB password into actions inputs or recorded video.
