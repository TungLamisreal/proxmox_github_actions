# MeowCloud IaC — clean Golden Image v2

## Status and execution boundaries

This is a **source patch and static-tested candidate**, not a report of a successful Proxmox run. 00/01/02 were reported working before the changes; 03/04 have not been runtime tested. No commands have been executed on the user's actual PVE cluster.

### What this patch changes

- CephFS: `meowcloud-lxc-base-debian12-v2.tar.zst`, `meowcloud-lxc-lb-debian12-v2.tar.zst`, `meowcloud-lxc-observer-debian12-v2.tar.zst`, `meowcloud-lxc-tunnel-debian12-v2.tar.zst`.
- RBD `general_pool`: `9100` -> `9101`,`9102` Docker Golden Templates, using marker `meowcloud-docker-clean-v2`; Backend and Frontend still use linked clones on RBD.
- RBD: `9001`/`9002`/`9003` KVM DB Golden Templates cloned from clean seed 9000 (or cleaned inside the clone), backed by `general_pool` as actually configured on the seed. DB data disk remains on `db_pool`.
- All roles: fail-fast package/service checks at image-build and post-provision stages.
- Backends: `meow-backend:latest` pre-built once in Golden Image (NOT rebuilt at autoscale).
- DB: `postgres:15-alpine` pulled during KVM image build; frontends: `nginx:alpine` pulled during Docker image build.

## Deployment order — READ BEFORE RUNNING

1. On the **Auto-VM**, confirm this repository matches the latest `main`; the ZIP consists of replacement/additional source files and intentionally excludes `vault.yml`, `state/`, `tfplan*`, credentials and private keys. Overlay only these files on a clean Git worktree and review `git diff` before commit/push.
2. Rotate `PROXMOX_API_TOKEN_SECRET`: the earlier exported Terraform plan JSON contained a readable API secret. Keep all plan files and state out of Git.
3. Disable the `webhook_listener.py` systemd instance (or disable dispatching scale workflows), confirm there are no running GitHub Actions/Proxmox tasks, and check Ceph health. All workflow files now share `meowcloud-iac-state` concurrency group.
4. Run the previously working **workflow 02** with the explicit destroy phrase, after accepting DB data loss. Confirm Terraform state resources are removed, and check for any orphan FE/BE/DB workloads from earlier autoscaling. Do not blindly unlock tasks or purge Ceph RBD volumes.
5. **Retire stale Golden Templates** `9100`/`9101`/`9102` and `9001`/`9002`/`9003` only after verifying they have no linked-clone dependents. Do NOT remove seed VMID `9000`, Auto-VM `120`, live storage pools or Ceph metadata. Workflow 00 intentionally FAILS if these IDs remain rather than reusing or destroying them. On Proxmox, discover each template's actual node via `pvesh get /cluster/resources --type vm --output-format json`, inspect each with `pct config <id>` / `qm config <id>`, and then retire it manually from its owning PVE node. If uncertain about RBD children, stop and inspect them before deletion. Also check that build CTIDs `997` and `998` are free.
6. Before workflow **00**, verify VMID `9000` is a valid Debian KVM seed with Proxmox clone functionality, SSH access, and an OS disk on expected Ceph `general_pool`. Verify CephFS `shared-fs` and RBD pools mounted/healthy on all three nodes. Ensure Packer and its Proxmox plugin are available. Run workflow 00 **only after** the stale templates are retired. It must build all role archives and RBD templates and pass the gates.
7. Run workflow **01** with BE=3, FE=3, DB=2, Terraform parallelism=1. This workflow now ensures the actual MeowCloud UI appears, validates role package/service segregation, and initializes `/opt/meowcloud-iac/state/autoscale.tfvars.json` for scale workflows.
8. To manually test workflow **03** without an active Prometheus HighCPU alert, explicitly set `allow_manual_test_without_alert: true`, choose one additional Backend *or* Frontend, and ensure there is RAM capacity. This flag is FALSE by default for automatic Alertmanager dispatch.
9. To test workflow **04**, ensure the corresponding pool has >3 machines. Explicitly select `allow_manual_test_without_alert: true` for manual smoke testing, or trigger the original LowCPU alert. Confirm drain and post-scale-in health checks. **04 no longer automatically Terraform-applies a rollback after partial apply**, to avoid accidentally recreating workloads.
10. Re-enable the autoscale webhook only when the full 00/01/03/04 run passes. Record measured deployment/scale timings and real service inventories.

## Fail-closed behaviour

- Any unexpected package/service on a guest => image build fails or Ansible provisioning fails.
- If a template with a stale marker exists => selector rejects it; workflow 00 refuses to overwrite it.
- If FE returns the default Nginx welcome page with HTTP 200 => Frontend health check fails.
- If Docker pre-baked image is absent => Ansible fails; it does not pull/build dynamically.
- If a 04 apply starts and then fails => avoid automatic apply rollback; reconcile real resources and state before retry.

## Limitations (important)

- Runtime Proxmox API, Ceph RBD, Docker-inside-LXC and cloudflared behavior **cannot** be verified offline.
- This patch intentionally retains upstream application tags `nginx:alpine`, `postgres:15-alpine`, and `meow-backend:latest` for compatibility. For reproducible production builds, pin digests plus the Cloudflared Debian artifact hash. In particular, the tunnel builder currently uses upstream `latest` (same artifact source as the previous Ansible provisioning) and is not guaranteed byte-for-byte reproducible.
- Packer HCL/Terraform CLI and Ansible collections must be validated on the user's Auto-VM; the local static checker does not replace `packer validate`, `terraform validate`, `ansible-playbook --syntax-check`, or a clone runtime smoke test.
- This package deliberately excludes the encrypted `ansible/group_vars/databases/vault.yml` from distribution. Existing Vault must remain available, and any DB credential configured in it should be re-created if the original vault is absent.

## Mandatory on-Auto-VM checks, before 00

```bash
cd /opt/meowcloud-iac
for file in packer/lxc-base.pkr.hcl packer/lxc-roles.pkr.hcl packer/lxc-docker.pkr.hcl packer/db-kvm.pkr.hcl; do
  (cd packer && packer init "${file#packer/}" && packer validate "${file#packer/}") || exit 1
done
(cd terraform && terraform fmt -check && terraform init -backend=false -input=false && terraform validate) || exit 1
(cd ansible && for p in playbooks/*.yml; do ansible-playbook -i inventory.ini --syntax-check "$p" || exit 1; done) || exit 1
for f in packer/scripts/*.sh; do bash -n "$f" || exit 1; done
python3 -m compileall -q scripts
```

## Caveat about overlaying source on Auto-VM

GitHub Actions `actions/checkout` gets code from GitHub `main` on the runner. If source is only patched under `/opt/meowcloud-iac` but not committed/pushed to `main`, 00/01/03 may execute old code again. Conversely 04 copies portions of checkout to `/opt/meowcloud-iac`. Verify the actual commit SHA for each run.
