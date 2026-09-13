#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/logs/chant_agent.pid"

if [ ! -f "$PID_FILE" ]; then
  printf '服务未运行（没有 PID 文件）。\n'
  exit 0
fi

pid="$(<"$PID_FILE")"
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  printf '服务未运行，已清理过期 PID 文件。\n'
  exit 0
fi

if ! ps -p "$pid" -o command= | grep -Fq 'uvicorn app.main:app'; then
  printf 'PID %s 不是 chant_agent 服务，为避免误杀已保留该进程。\n' "$pid" >&2
  exit 1
fi

kill "$pid"
for _ in {1..20}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    printf '服务已停止。\n'
    exit 0
  fi
  sleep 0.25
done

kill -KILL "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
printf '服务已强制停止。\n'
