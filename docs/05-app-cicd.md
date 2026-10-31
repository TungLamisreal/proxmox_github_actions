# Workflow 05 — MeowCloud Application CI/CD (GHCR)

**Scope:** BE/FE app images only. Terraform, Golden Templates, PostgreSQL containers and volumes remain unchanged.

## Before enabling

1. Review Draft PR and its changes. Workflow 05 has an automatic `push` trigger for changes under `apps/**` on `main`. **Merging this PR will build and attempt to deploy immediately**, because it introduces those files. Keep IaC workflows idle before merge. Consider pausing the webhook during the first rollout; restart it only after checking the current `scripts/webhook_listener.py` deployed at CT120.
2. Ensure the GitHub repository has Actions enabled and GHCR package permissions. A GitHub-hosted Ubuntu runner builds images; CT120 self-hosted runner deploys. The workflow uses repo-scoped `GITHUB_TOKEN` and permissions `packages:write` (build) / `packages:read` (other IaC workflows).
3. Ensure **each BE/FE LXC** can resolve and reach `ghcr.io` over HTTPS; image pulls happen *inside* guests. The containers need available disk space. If outbound access is blocked, deploy safely fails before publishing release state.
4. Current runtime inventory and capacity state must exist at `/opt/meowcloud-iac/ansible/inventory.ini` and `/opt/meowcloud-iac/state/autoscale.tfvars.json`. Keep `main` code consistent with the CT120 runtime, and ensure no other Terraform/Ansible IaC workflow is active.
5. Backend and Frontend images keep their existing API/UI health contracts; no DB changes or migrations occur.

## Flow

`apps/**` push to `main` (or workflow_dispatch) → build two Docker images on GitHub-hosted runner → push to GHCR with commit-SHA tags → retrieve immutable SHA256 digests → test containers locally → self-hosted CT120 Ansible deployment to all **currently inventoried** BE and FE, **serial: 1** with per-node healthcheck → VIP health/version checks → atomically publish `/opt/meowcloud-iac/state/app-release.json` (mode 0600).

The manifest is *not* a Terraform variable or part of the Git repository. A successful app release makes workflows 01/03/04 select the same pinned images on freshly cloned or subsequently configured BE/FE. No Golden Template rebuild is needed. If there is no manifest, existing pre-baked `meow-backend:latest` and `nginx:alpine` remain the default.

Secrets are not written to release state. When a missing GHCR image needs pulling, Ansible performs a temporary Docker login using `GITHUB_TOKEN` and logs out. If the image is cached, no login is needed.

## Rollback and operational limitations

If rolling rollout or VIP checks fail, workflow 05 attempts rollback by running BE/FE playbooks with the **last successful release manifest** (or original Golden Template images if no manifest existed). This is a best-effort rollback; inspect individual LXC state if rollback fails. The desired release manifest is written only after healthchecks pass.

Rolling replacement restarts one application container at a time; the Nginx HA load balancer can retry failed GET requests, but this workflow does **not** implement a dedicated request-drain gate and does not guarantee zero dropped in-flight requests.

`/api/health` verifies the BE is running; it does **not** verify DB connectivity. `/api/version` and `/version.json` prove BE/FE application version through VIP. Database schema migration/rollback must be planned separately with backups.

## Verifying after a successful rollout

On CT120:

```sh
cat /opt/meowcloud-iac/state/app-release.json
curl -fsS http://192.168.150.100/api/version
curl -fsS http://192.168.150.100/version.json
```

Verify the commit-SHA version matches both responses; view full GitHub Actions logs for each BE/FE healthcheck. Then exercise one controlled scale-out (workflow 03) to verify the new LXC pulls the exact released digest. Monitor webhook notifications after updating the runtime webhook file to the new revision, which includes workflow 05 in its busy-check allowlist.

To change the UI, edit `apps/frontend/index.html`; to change the API, edit `apps/backend/app.py`, then push to `main`. The CI/CD workflow builds both images at that commit and rolls them out; DB is untouched.
