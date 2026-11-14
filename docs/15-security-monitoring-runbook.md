# MeowCloud Security Monitoring - optional Ansible

Playbook 29 can run standalone. Workflow 01 also runs it from a **separate, optional post-provision job**; it is not imported into day-0 `site.yml`. CT121 is an etcd voter with 2 vCPU, 2 GiB RAM and 15 GiB disk. The preflight requires 768 MiB spare cgroup memory and 3 GiB free disk or FAILS CLOSED. There is no automatic resize. Loki and Alloy systemd limits are 512 MiB and 192 MiB; Loki retention is 72 hours but this is not a disk quota.

On first install, the playbook temporarily masks absent Loki/Alloy services to avoid package post-install auto-start. It validates their configuration before unmasking. A validation or package failure may leave new services masked; inspect the failed task and runbook before retrying, rather than removing masks blindly. Config validation is not a guarantee of operational health.

## Automatic rebuild behavior (integrated in current workflow 01)

Workflow 01 now runs a **separate, non-blocking** `security-monitoring` job after the original `provision-and-configure` job succeeds. By default it calls playbook 29 on **Observer plus the 3 PVE firewall syslog forwarders** (`-e security_install_pve_forwarders=true`) using the freshly published canonical inventory; the main provision job and HA policy steps remain unchanged. For emergency exclusion, enable the `skip_security_monitoring` workflow-dispatch input. These optional PVE steps install/validate rsyslog and can restart rsyslog on configuration change, but do not change any Proxmox firewall rules.

After Workflow 02 destroys CT121, a subsequent Workflow 01 can therefore reinstall Loki/Alloy and re-provision the Security datasource/dashboard. **Installation is not guaranteed**: a package/config/network failure produces a warning and job summary, while the core workflow can still succeed. Check the `Optional Security Monitoring (CT121)` job and `systemctl is-active loki alloy` after every clean rebuild. Security logs stored only on CT121 are lost when that CT is destroyed; old logs require independent backup/remote storage.

IMPORTANT: The 4 narrowly scoped UDP allow rules inside the pre-existing Proxmox Datacenter security group `sg_observer` are **external persistent cluster configuration**, not managed by Workflow 01/02 or Terraform in this repository. The user has already applied them to the existing cluster: 192.168.150.10/.11/.12 -> CT121:5514/udp, and 192.168.150.1 -> CT121:5515/udp. They should survive Workflow 02 guest destroy; if the entire PVE cluster is rebuilt or policy manually reset, restore the rules from a backup before relying on syslog. The job MUST NOT silently rewrite `cluster.fw`/HA rules.

pfSense Remote Syslog is configured in the pfSense GUI and persists only while that firewall configuration persists; its setup is not automated by Workflow 01. If pfSense is rebuilt, restore its config backup or reconfigure remote logging. Firewall-rule logging on individual guest DROP rules must also remain enabled. Therefore 'automatic rebuild' of app components does not promise automatic reconstruction of external firewalls.

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

## Unified Proxmox + pfSense Firewall Dashboard (dashboard v4)

The dashboard is renamed to **MeowCloud · Firewall Monitoring** (same UID `meow-security` and URL). Existing Proxmox DROP count, trend, single short log panel, source-IP and destination-port statistics remain unchanged. Below all existing Proxmox panels is a dedicated **pfSense** section with BLOCK count (last 5 min), BLOCK trend, and ONE firewall log panel. This allows the thesis demo to scroll from Proxmox to pfSense in one dashboard, without duplicated log panels or modifying the Autoscaling dashboard.

The pfSense statistics count native `filterlog` CSV actions containing `,block,`. The pfSense log panel intentionally shows the actual received events rather than guessing a parser without a captured real pfSense log sample; a future parser change should be verified against live pfSense `filterlog`. These panels show no data until pfSense forwards **Firewall Events** to Alloy on CT121 UDP 5515. Proxmox + pfSense Loki labels and listeners were already configured; **dashboard JSON alone does not enable pfSense forwarding**.

To update only the dashboard JSON from the current `main` revision:
```bash
cd /opt/meowcloud-iac
git pull --ff-only origin main
ansible -i ansible/inventory.ini monitoring -b -m copy \
  -a 'src=/opt/meowcloud-iac/monitoring/security/security-dashboard.json dest=/etc/meowcloud-grafana/boards/security.json owner=root group=root mode=0644'
```
Grafana should reload the file-provider dashboard without restarting services. Dashboard UID remains `meow-security`, original Autoscaling dashboard remains untouched.

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
