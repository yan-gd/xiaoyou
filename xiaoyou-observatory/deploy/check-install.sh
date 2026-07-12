#!/usr/bin/env bash
set -u

failed=0
check() {
    if "$@" >/dev/null 2>&1; then
        printf '[ OK ] %s\n' "$*"
    else
        printf '[FAIL] %s\n' "$*"
        failed=1
    fi
}

check test -x /usr/local/sbin/xiaoyou-ctl
check test -r /etc/xiaoyou-observatory.env
check test -x /opt/xiaoyou-observatory/.venv/bin/uvicorn
check systemctl is-active xiaoyou-observatory
check sudo -u xiaoyou-observer sudo -n /usr/local/sbin/xiaoyou-ctl status
check curl -fsS http://127.0.0.1:8765/api/health
check nginx -t

exit "$failed"
