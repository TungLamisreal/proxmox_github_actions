# MeowCloud Security Monitoring - optional Ansible

Run this playbook by itself; never import into day-0 site.yml. CT121 is an etcd voter with 2 vCPU, 2 GiB RAM and 15 GiB disk. The preflight requires 768 MiB spare cgroup memory and 3 GiB free disk or FAILS CLOSED. There is no automatic resize. Loki and Alloy systemd limits are 512 MiB and 192 MiB; Loki retention is 72 hours but this is not a disk quota.

On first install, the playbook temporarily masks absent Loki/Alloy services to avoid package post-install auto-start. It validates their configuration before unmasking. A validation or package failure may leave new services masked; inspect the failed task and runbook before retrying, rather than removing masks blindly. Config validation is not a guarantee of operational health.

## Automatic rebuild behavior (requires the workflow integration PR to be merged)

Workflow 01 now runs a **separate, non-blocking** `security-monitoring` job after the original `provision-and-configure` job succeeds. By default it calls playbook 29 on **Observer only** using the freshly published canonical inventory; the main provision job and HA policy steps remain unchanged. For emergency exclusion, enable the `skip_security_monitoring` workflow-dispatch input.

After Workflow 02 destroys CT121, a subsequent Workflow 01 can therefore reinstall Loki/Alloy and re-provision the Security datasource/dashboard. **Installation is not guaranteed**: a package/config/network failure produces a warning and job summary, while the core workflow can still succeed. Check the `Optional Security Monitoring (CT121)` job and `systemctl is-active loki alloy` after every clean rebuild. Security logs stored only on CT121 are lost when that CT is destroyed; old logs require independent backup/remote storage.

PVE forwarding is a separate opt-in on the PVE hosts, pfSense forwarding uses its GUI, and the CT121 firewall still requires the narrow UDP allow rules described below. These are NOT silently enabled by Workflow 01.

On CT120 after updating the repo:
    cd /opt/meowcloud-iac/ansible
    ansible-playbook -i inventory.ini playbooks/29-setup-security-monitoring.yml --syntax-check
    ansible -i inventory.ini monitoring -m ping
    ansible-playbook -i inventory.ini playbooks/29-setup-security-monitoring.yml

This installs Loki bound to localhost TCP 3100 and Alloy on CT121 UDP 5514 (Proxmox) and UDP 5515 (pfSense), and separately provisions datasource UID meow-security-loki and dashboard UID meow-security. Existing Grafana datasource meow-prom, Autoscaling dashboard and Workflow 06 remain untouched. Grafana briefly restarts only once to pick up the NEW datasource.

SECURITY: current Grafana allows anonymous Viewer. Log dashboard may expose internal IPs to any client that can reach 3000. Keep it behind a trusted management network.

FIREWALL SOURCE OF TRUTH: Proxmox sg_observer is referenced in Terraform but its rule bodies are not owned by this repo. Add only narrow inbound UDP allow to CT121:
- from 192.168.150.10/.11/.12 to UDP 5514
- from 192.168.150.1 to UDP 5515
Never remove its etcd, Grafana, Prometheus and exporter allow rules.

Optional Proxmox forwarding ONLY when you have verified that /var/log/pve-firewall.log exists on all 3 node hosts (some PVE firewall backends log differently):
    ansible-playbook -i inventory.ini playbooks/29-setup-security-monitoring.yml -e security_install_pve_forwarders=true

This installs rsyslog on opted-in Proxmox hosts only; forwards new pve-firewall logfile lines to CT121 UDP 5514; validates rsyslogd -N1. No changes to PVE firewall rules, HA/Corosync, Ceph, Patroni or guests.

pfSense: Status > System Logs > Settings > Remote Logging Options: send only Firewall Events as RFC3164 UDP syslog to 192.168.150.21:5515. Enable logging on demo BLOCK rules. pfSense policy is NOT modified by Ansible.

Verify CT121:
    systemctl is-active meowcloud-etcd loki alloy meowcloud-grafana
    ss -lun | grep -E ':5514|:5515'
    curl -fsS http://127.0.0.1:3100/ready
    curl -fsS http://127.0.0.1:3000/api/dashboards/uid/meow-security >/dev/null

Grafana Explore LogQL:
    {job="firewall",firewall="proxmox"}
    {job="firewall",firewall="pfsense"}

Rollback on CT121 (does not touch original dashboard):
    systemctl disable --now alloy loki
    rm -f /etc/meowcloud-grafana/datasources/meowcloud-security.yaml /etc/meowcloud-grafana/boards/security.json
    systemctl restart meowcloud-grafana

If optional PVE forwarding was enabled, on each PVE node:
    rm -f /etc/rsyslog.d/35-meowcloud-pve-firewall.conf
    rsyslogd -N1 && systemctl restart rsyslog

Turn off pfSense remote syslog and remove only added UDP allow rules. No automatic deletion of collected logs.
