"""Keep autoscale RAM admission aligned across manual and webhook paths."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_ram_reserve_matches_in_manual_automatic_and_selector_defaults():
    workflow = (ROOT / ".github/workflows/03-autoscale-backend.yml").read_text()
    webhook = (ROOT / "scripts/webhook_listener.py").read_text()
    selector = (ROOT / "scripts/select_best_node.py").read_text()

    assert re.search(r'ram_reserve_mb:\s*\n(?:[^\n]*\n)*?\s*default: "1024"', workflow)
    assert '"ram_reserve_mb": "1024"' in webhook
    assert re.search(r'add_argument\("--ram-reserve-mb",\s*type=int,\s*default=1024\b', selector)


def test_backend_frontend_lxc_ram_remains_1024_mib():
    for file in ("04-backend.tf", "05-frontend.tf"):
        content = (ROOT / "terraform" / file).read_text()
        assert re.search(r'memory\s*\{\s*dedicated\s*=\s*1024\s*\}', content)
