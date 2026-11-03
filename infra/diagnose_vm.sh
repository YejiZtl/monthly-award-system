#!/usr/bin/env bash
# Read-only diagnostics; never print the environment file or credentials.
set -u
date --iso-8601=seconds
systemctl --no-pager --full status monthly-award monthly-award-worker || true
df -h /opt/monthly-award /var/lib/monthly-award /tmp
free -m
journalctl -u monthly-award -u monthly-award-worker --since '1 hour ago' --no-pager -n 200
/opt/monthly-award/.venv/bin/python - <<'PY'
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as response:
        print(response.read().decode())
except Exception as exc:
    print('Health check failed:', type(exc).__name__)
PY
