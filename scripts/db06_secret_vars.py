#!/usr/bin/env python3
"""Create temporary Ansible extra vars from a masked GitHub Actions secret."""
import json
import os
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("Expected path to a private pre-created temporary file")
secret = os.environ.get("REPL_SECRET", "")
if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", secret):
    raise SystemExit("Missing/invalid POSTGRES_REPLICATION_PASSWORD Actions secret")
target = Path(sys.argv[1])
if not target.is_file() or target.stat().st_mode & 0o077:
    raise SystemExit("Ansible vars file must already exist, readable only by owner")
with target.open("w", encoding="utf8") as output:
    json.dump({"vault_postgres_replication_password": secret}, output)
    output.flush()
    os.fsync(output.fileno())

