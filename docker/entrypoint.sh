#!/bin/bash
set -e

# 환경변수를 cron 환경에서도 사용할 수 있도록 /etc/environment에 기록
printenv | grep -v "^_=" >> /etc/environment

echo "[entrypoint] Starting cron daemon..."
cron -f
