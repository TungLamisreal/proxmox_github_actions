"""Regression guard for scale-out frontend health checks under bash pipefail."""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/03-autoscale-backend.yml"


def test_frontend_and_vip_healthchecks_do_not_pipe_curl_to_quiet_grep():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'set -euo pipefail' in source

    # grep -q closes a pipeline as soon as it sees the marker.
    # curl can then fail with exit 23 despite an otherwise healthy frontend.
    assert '| grep -Fq "$FRONTEND_MARKER"' not in source

    assert '--output "$FE_HTML" "http://${FE_IP}/"' in source
    assert 'grep -Fq "$FRONTEND_MARKER" "$FE_HTML"' in source

    assert '--output "$VIP_HTML" http://192.168.150.100/' in source
    assert 'grep -Fq "$FRONTEND_MARKER" "$VIP_HTML"' in source
