> **HISTORICAL DESIGN / INCIDENT NOTES (2026-11-14) — NOT CURRENT OPERATIONS.** This document preserves the standalone etcd milestone and the 2026-11-02 recovery incident. Its references to old DB06/DB07 jobs and modes (such as `ha-readiness`, `dcs-bootstrap`, `dcs-health` or `patroni-stage`) are **not valid dispatch instructions** for the current pipeline. Use [Unified DB HA — workflow 06](11-db-ha-unified-workflow.md); current modes are `bootstrap-ha`, `health`, `diagnose`, `adopt-vip`, `gateway-repair` and `resume`. Never re-bootstrap an already adopted Patroni cluster.
# MeowCloud DB06: three-member etcd DCS bootstrap (not PostgreSQL failover)

This change implements the **etcd DCS milestone only**. It does **not**
install or start Patroni, promote a standby, stop PostgreSQL, alter PGDATA,
switch the backend database host, or change the Proxmox HA ownership policy.
Do not run the DB01 power-off drill yet.

## Cluster layout

| Voter | Guest | IP | Preferred Proxmox node |
| --- | --- | --- | --- |
| db-01 | VM271 | 192.168.150.71 | node2 |
| db-02 | VM272 | 192.168.150.72 | node3 |
| observer | CT121 | 192.168.150.21 | node1 |

The `scripts/db06_pve_placement.py` preflight inspects **live** PVE placement and refuses bootstrap unless all three voters are running on three distinct PVE nodes. Proxmox HA is permitted to relocate DB01 and Observer, so do not blindly trust their Terraform initial placement.

Debian packages `etcd-server` and `etcd-client` are installed directly on the guests; CT121 does not have Docker. Ansible disables the vendor `etcd` service and uses a dedicated `meowcloud-etcd.service`. All etcd data stays under `/var/lib/meowcloud-etcd` on the guest OS disk and is **not** PostgreSQL `/opt/pgdata`.

## Network precondition — Proxmox firewall

The Proxmox guest firewall definitions currently have `input_policy=DROP` and reference existing `sg_db` and `sg_observer` security groups. The current repository does **not** own those security-group rule bodies.

**Before invoking `dcs-bootstrap`**, update the existing firewall source-of-truth (and apply/review its changes) so that **only** these three guest source IPs can connect **TCP 2379** (etcd HTTPS client endpoint) and **TCP 2380** (etcd peer endpoint) on each of the three voter guests:

- Source `192.168.150.71/32` (DB01)
- Source `192.168.150.72/32` (DB02)
- Source `192.168.150.21/32` (Observer)

All other ingress to those DCS ports must stay blocked. In particular, do not change the guest input policy to ACCEPT. Do not configure plain HTTP etcd listeners. Clients must present certificates signed by the dedicated CA.

The DCS run verifies TLS quorum after installing services. Without the firewall changes, service configuration may be present and `etcd-verify` may fail; **this must not trigger Patroni takeover**. Treat the missing firewall permission as a prerequisite, not a reason to disable mTLS.

## Persistent PKI

The Actions job creates a dedicated PKI at `/home/github-runner/.local/state/meowcloud/db-ha-etcd-pki` **on CT120**, not inside the Git checkout. The directory and private keys have restrictive permissions. The CA private key is **never copied to guests or GitHub**. Each etcd voter has a unique, IP-SAN server/peer certificate; the separate Patroni client certificate is staged for future controlled takeover.

On reruns, the tool validates certificate signatures, expiration and key matching and **never silently rotates** existing certificates. Back up CT120's PKI to a separate protected location: losing the CA key complicates future certificates and recovery.

If a previous run was interrupted between publishing a certificate and its key, generation fails closed. Do **not** delete an existing CA or active etcd data directory to force a rerun.

## Exact workflow usage after PR review + merge

1. **Actions → 06 - Database HA Pipeline → Run workflow → `verify`** (confirmation blank). Requires healthy PostgreSQL replication.
2. Ensure the three guests are on distinct PVE nodes and firewall rules above are applied from their authoritative source.
3. Run the same workflow with:
   - Mode: `dcs-bootstrap`
   - Confirmation: `BOOTSTRAP_ETCD_QUORUM`
   - Branch: `main`
4. Review the independent jobs: `preflight` → `verify` (existing PostgreSQL) → **`etcd-bootstrap`** → **`etcd-verify`**. The last stage requires three etcd members and all three HTTPS endpoints healthy.
5. For subsequent read-only checks select `dcs-health` (confirmation blank): the etcd bootstrap job is skipped and the health job is rerun.

If etcd-verify fails because of firewall or networking, correct the underlying issue and use GitHub's **Re-run failed jobs** for that workflow attempt. Do not delete `/var/lib/meowcloud-etcd`, regenerate the CA, or reprovision PostgreSQL.


## Recovery from partial etcd bootstrap (2026-11-02, run 41435755796)

The first DCS bootstrap run reached DB02 but did not reach service setup on DB01
or Observer. DB01 and Observer failed a stale `apt-cache policy etcd-server`
availability check: no `Candidate:` field was returned. DB02 successfully
installed packages, placed mTLS material and created the DB06 ownership marker,
but a `Type=notify` systemd service waited for readiness without any peers
available and timed out. A service timeout alone cannot exclude a separate
configuration or TLS error; the next run will capture journal evidence.

**The repair does not require deleting any etcd data, regenerating the CA,
reinitializing the DB, or disabling Proxmox firewall.** Fixes:

1. Refresh APT indexes and verify both `etcd-server` and `etcd-client` on
   **all three voters** before touching a single service. If package retrieval
   still fails, the run stops at an explicit APT prerequisite error with no
   cluster installation changes; inspect the failing host's Debian package
   source configuration.
2. Install the distribution packages with `policy_rc_d: 101`, preventing
   Debian's unconfigured vendor etcd daemon from autostarting. Then disable the
   vendor `etcd` unit and manage only `meowcloud-etcd`.
3. The DB06 service uses `Type=exec`. This indicates only that the daemon
   process was launched; **not that quorum exists**. Startup and restart occur
   one voter at a time (`serial: 1`) so an established quorum isn't
   interrupted by a simultaneous restart. All client listeners are tested
   after the last voter is configured.
4. Job 09 independently confirms all three member identities and mTLS endpoint
   health. Bootstrap job 08 runs `17-diagnose-etcd.yml` on failure and records
   package installation, systemd status and a bounded journal tail on all voters.

The failed run from the **old** commit must **not** be rerun as-is. After
reviewing and merging the fix PR, create a **new** run on the updated `main`
with `mode=dcs-bootstrap` and confirmation `BOOTSTRAP_ETCD_QUORUM`.
The existing DB02 marker and mTLS files are deliberately reused. If an
APT source or systemd error remains, do not force-delete any state: use the
diagnostic task output to resolve the specific guest-level dependency.

## What remains to make DB01 power-off safe

- Verify that the PVE HA policy for VM271 cannot start an old writable PostgreSQL while DB02 is leader.
- Build and validate a PostgreSQL-15-compatible Patroni process/container image that can take control of the existing `/opt/pgdata/data` without reinitializing it. Disable standalone Docker `restart: always` for Postgres during a reviewed, rollback-capable handover.
- Prove one elected writer, fencing, rejoin/pg_rewind, and DCS quorum under network partitions.
- Add HAProxy DB writer routing to the existing Keepalived VIP `192.168.150.100:5432`, with Patroni `/primary` health checks; switch `meowcrud.json` and workflow 05 from the hard-coded `.71` to this endpoint after validation.
- Perform a controlled test: CRUD before failure, stop VM271, DB02 takes leadership, verify old data + new writes, restart VM271, verify it rejoins as a standby.

**Do not use the `patroni-stage` input as if it activates HA.** It writes config files only, remains separately gated, and requires a proven etcd quorum.
