#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_FILE="$ROOT_DIR/logs/chant_agent.pid"
LOG_FILE="$ROOT_DIR/logs/chant_agent.log"
HOST="${CHANT_AGENT_HOST:-127.0.0.1}"
PORT="${CHANT_AGENT_PORT:-8765}"
PYTHON="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  printf '未找到 Python 虚拟环境，请先执行：bash install.sh\n' >&2
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  pid="$(<"$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= | grep -Fq 'uvicorn app.main:app'; then
    printf '服务已经在运行：PID %s，地址 http://%s:%s/\n' "$pid" "$HOST" "$PORT"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$ROOT_DIR/logs"
# Detach from the invoking terminal when the platform provides `setsid`.
# macOS commonly has no setsid, so retain nohup as the portable fallback.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup "$PYTHON" -m uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    </dev/null >>"$LOG_FILE" 2>&1 &
else
  nohup "$PYTHON" -m uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    </dev/null >>"$LOG_FILE" 2>&1 &
fi
pid=$!
printf '%s\n' "$pid" >"$PID_FILE"

ready=0
for _ in {1..30}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  if curl -fsS --max-time 1 "http://${HOST}:${PORT}/" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  rm -f "$PID_FILE"
  printf '服务启动失败或超时，请查看日志：%s\n' "$LOG_FILE" >&2
  kill "$pid" 2>/dev/null || true
  exit 1
fi

# A successful HTTP probe is not enough if the process exits immediately
# afterwards. Give the process a short grace period and verify it again.
sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  printf '服务启动后立即退出，请查看日志：%s\n' "$LOG_FILE" >&2
  exit 1
fi

printf '服务已启动：PID %s\n' "$pid"
printf '访问地址：http://%s:%s/\n' "$HOST" "$PORT"
printf '日志文件：%s\n' "$LOG_FILE"
