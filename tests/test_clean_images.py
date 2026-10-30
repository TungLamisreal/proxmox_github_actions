"""Offline invariants; these are NOT Proxmox or Ansible runtime tests."""
import os
import pathlib
import subprocess
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE_SCRIPT = ROOT / 'packer/scripts/40-image-role-gate.sh'

@pytest.mark.parametrize('role,required,forbidden',[
    ('base', {'openssh-server','prometheus-node-exporter'}, {'nginx','prometheus','keepalived','docker-ce'}),
    ('docker', {'openssh-server','prometheus-node-exporter','docker-ce','python3-docker'}, {'nginx','prometheus','keepalived'}),
    ('lb', {'openssh-server','prometheus-node-exporter','nginx','keepalived'}, {'prometheus','docker-ce'}),
    ('observer', {'openssh-server','prometheus-node-exporter','prometheus','prometheus-alertmanager'}, {'nginx','keepalived','docker-ce'}),
    ('tunnel', {'openssh-server','prometheus-node-exporter','cloudflared'}, {'nginx','prometheus','docker-ce'}),
    ('db', {'openssh-server','prometheus-node-exporter','docker-ce','qemu-guest-agent'}, {'nginx','prometheus','keepalived'}),
])
def test_role_gate(role, required, forbidden):
    # Replace system binaries to exercise package/service policy deterministically.
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for name,source in {
            'dpkg-query': '''#!/bin/sh
pkg=""; for x do pkg="$x"; done
case ",$MOCK_PACKAGES," in *",$pkg,"*) printf 'ii '; exit 0;; esac
exit 1
''',
            'systemctl': '''#!/bin/sh
case "$1" in is-active|is-enabled) exit 1;; esac
exit 0
''',
            'docker': '#!/bin/sh\nexit 0\n',
        }.items():
            path=d/name;path.write_text(source);path.chmod(0o755)
        env={**os.environ,'PATH':f'{d}:{os.environ["PATH"]}', 'MOCK_PACKAGES':','.join(sorted(required))}
        assert subprocess.run(['bash',str(ROLE_SCRIPT),role],env=env,capture_output=True).returncode==0
        env['MOCK_PACKAGES']+=','+sorted(forbidden)[0]
        assert subprocess.run(['bash',str(ROLE_SCRIPT),role],env=env,capture_output=True).returncode!=0

@pytest.mark.parametrize('file',list(ROOT.glob('.github/workflows/*.yml'))+list(ROOT.glob('ansible/playbooks/*.yml')))
def test_yaml_syntax(file):
    assert yaml.safe_load(file.read_text()) is not None

@pytest.mark.parametrize('file',list(ROOT.glob('packer/scripts/*.sh')))
def test_bash_syntax(file):
    assert subprocess.run(['bash','-n',str(file)]).returncode==0

@pytest.mark.parametrize('file',list(ROOT.glob('.github/workflows/*.yml')))
def test_workflow_shell_syntax(file):
    data=yaml.safe_load(file.read_text())
    for job in data['jobs'].values():
        for step in job.get('steps',[]):
            if 'run' in step:
                assert subprocess.run(['bash','-n'],input=step['run'],text=True,capture_output=True).returncode==0

def test_main_legacy_service_contamination_removed():
    base=(ROOT/'packer/scripts/01-base-packages.sh').read_text()
    for package in ['nginx','keepalived','prometheus-alertmanager','docker-ce']:
        assert package not in base
    assert '40-image-role-gate.sh base' in (ROOT/'packer/lxc-base.pkr.hcl').read_text()

def test_markers_consistent():
    marker='meowcloud-docker-clean-v2'
    assert marker in (ROOT/'packer/lxc-docker.pkr.hcl').read_text()
    assert marker in (ROOT/'scripts/select_lxc_template.py').read_text()
    assert marker in (ROOT/'.github/workflows/00-build-golden-images.yml').read_text()

def test_workflow00_never_destroys_templates_directly():
    y=(ROOT/'.github/workflows/00-build-golden-images.yml').read_text()
    assert 'qm destroy' not in y
    assert 'pct destroy' not in y

@pytest.mark.parametrize('file',list(ROOT.glob('packer/*.pkr.hcl')))
def test_packer_hcl_inline_strings_have_balanced_quotes(file):
    # Lightweight sanity check, NOT a replacement for packer validate.
    for line in file.read_text().splitlines():
        count=0;escape=False
        for ch in line:
            if ch=='"' and not escape:count+=1
            if ch=='\\':escape=not escape
            else:escape=False
        assert count%2==0, line


def test_no_sensitive_files_in_patch_manifest():
    manifest = (ROOT / 'PATCH-MANIFEST.txt').read_text().splitlines()
    shipped = [
        line[2:].strip()
        for line in manifest
        if line.startswith(('M ', 'A '))
    ]
    assert not any(
        path.startswith(('terraform/tfplan', 'state/'))
        or path.endswith(('.tfstate', '.tfstate.backup'))
        or path == 'ansible/group_vars/databases/vault.yml'
        for path in shipped
    )
