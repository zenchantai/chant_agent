#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

fail() {
  printf '\n安装失败：%s\n' "$1" >&2
  exit 1
}

printf '缠论分析工作台安装程序\n'
printf '安装目录：%s\n\n' "$ROOT_DIR"

command -v uv >/dev/null 2>&1 || fail "未找到 uv。请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
command -v node >/dev/null 2>&1 || fail "未找到 Node.js。请先安装 Node.js 20 或更高版本：https://nodejs.org/"
command -v npm >/dev/null 2>&1 || fail "未找到 npm。请安装 Node.js（会同时提供 npm）。"

node_major="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$node_major" -lt 20 ]; then
  fail "Node.js 版本过低：$(node --version)。需要 Node.js 20 或更高版本。"
fi

if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  printf '创建 Python 虚拟环境...\n'
  uv venv "$ROOT_DIR/.venv"
fi

printf '安装 Python 依赖...\n'
uv pip install --python "$ROOT_DIR/.venv/bin/python" -r "$ROOT_DIR/requirements.txt"

printf '安装前端依赖...\n'
npm --prefix "$ROOT_DIR/web" ci

printf '构建前端...\n'
npm --prefix "$ROOT_DIR/web" run build

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/logs"
touch "$ROOT_DIR/data/.gitkeep" "$ROOT_DIR/logs/.gitkeep"

if [ ! -f "$ROOT_DIR/.env" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  printf '已创建 .env，请按需填写 AI_API_KEY。\n'
else
  printf '检测到已有 .env，已保留原配置。\n'
fi

printf '\n安装完成。\n'
printf '启动服务：\n'
printf '  uv run --with-requirements requirements.txt --with uvicorn python -m uvicorn app.main:app --host 127.0.0.1 --port 8765\n'
printf '访问地址：http://127.0.0.1:8765/\n'
printf '首次使用：打开页面，点击“添加自选”，再点击“同步行情”。\n'
