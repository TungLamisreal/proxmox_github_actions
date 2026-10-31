#!/usr/bin/env python3
"""One-time opt-in PostgreSQL app DB bootstrap on CT120.

NEVER formats, drops, truncates or replaces an existing PostgreSQL database.
Targets existing DB primary KVM (192.168.150.71) via SSH / docker exec psql.
"""
import argparse
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys

STATE = pathlib.Path('/opt/meowcloud-iac/state/meowcrud.json')
SCHEMA = pathlib.Path(__file__).resolve().parents[1] / 'db' / 'schema.sql'
HOST = '192.168.150.71'
DB = 'meowcloud_demo'
ROLE = 'meowcrud'


def psql(sql, database='postgres', user='postgres'):
    # SSH only sends a fixed command; SQL travels on stdin, not command-line args.
    ssh = [
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'StrictHostKeyChecking=accept-new', f'root@{HOST}',
        'docker', 'exec', '-i', 'meow-postgres', 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
        '-A', '-t', '-U', user, '-d', database,
    ]
    result = subprocess.run(ssh, input=sql, text=True, capture_output=True, timeout=60)
    if result.returncode:
        # Avoid echoing potentially sensitive SQL and output.
        raise RuntimeError(f'PostgreSQL command failed (rc={result.returncode}). '
                           'Check SSH access and meow-postgres on DB primary.')
    return result.stdout.strip()


def persist(data):
    # No credentials in repo, GitHub logs or shell command-line arguments.
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_name('.meowcrud.json.pending')
    fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, STATE)
        STATE.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()


def main():
    parser = argparse.ArgumentParser(description='Initialize isolated MeowCloud demo PostgreSQL database')
    parser.add_argument('--confirm', required=True, help='Must be CREATE_MEOWCLOUD_DEMO_DB')
    args = parser.parse_args()
    if args.confirm != 'CREATE_MEOWCLOUD_DEMO_DB':
        parser.error('Explicit --confirm CREATE_MEOWCLOUD_DEMO_DB required')
    if STATE.exists():
        cfg = json.loads(STATE.read_text())
        if not all(cfg.get(x) for x in ('db_host', 'db_name', 'db_user', 'db_password', 'demo_token')):
            raise RuntimeError('Existing meowcrud.json has missing secrets; inspect manually')
        if cfg['db_host'] != HOST or cfg['db_name'] != DB or cfg['db_user'] != ROLE:
            raise RuntimeError('Existing configuration belongs to unexpected DB/role; aborting')
    else:
        role_exists = psql(f"SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}';")
        db_exists = psql(f"SELECT 1 FROM pg_database WHERE datname = '{DB}';")
        if role_exists or db_exists:
            raise RuntimeError('Role or DB already exists without local secret state. '
                               'Refusing to take ownership or rotate unknown passwords.')
        cfg = dict(db_host=HOST, db_port=5432, db_name=DB, db_user=ROLE,
                   db_password=secrets.token_hex(24), demo_token=secrets.token_urlsafe(32),
                   initialized=False)
        persist(cfg)  # Persist before any SQL to allow safe retry after partial failure.

    # All SQL identifiers here are constants, and password is checked hex.
    password = cfg['db_password']
    if not re.fullmatch('[0-9a-f]{48}', password):
        raise RuntimeError('Unexpected database password format, refusing SQL interpolation')
    if not psql(f"SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}';"):
        psql(f"CREATE ROLE {ROLE} WITH LOGIN PASSWORD '{password}';")
    else:
        psql(f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{password}';")
    if not psql(f"SELECT 1 FROM pg_database WHERE datname = '{DB}';"):
        psql(f'CREATE DATABASE {DB} OWNER {ROLE};')
    if not SCHEMA.is_file():
        raise RuntimeError(f'Missing schema: {SCHEMA}')
    psql(SCHEMA.read_text(), database=DB, user=ROLE)
    count = psql('SELECT count(*) FROM information_schema.tables '
                 "WHERE table_schema='public' AND table_name IN "
                 "('categories','products','customers','orders');", database=DB, user=ROLE)
    if count != '4':
        raise RuntimeError(f'Expected 4 app tables, found {count}')
    cfg['initialized'] = True
    persist(cfg)
    print(f'PASS: {DB} on {HOST} contains 4 tables. State: {STATE}')
    print('Demo login token is intentionally not printed. '
          "Retrieve on CT120 with: jq -r .demo_token /opt/meowcloud-iac/state/meowcrud.json")


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
