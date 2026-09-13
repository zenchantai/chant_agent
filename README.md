# 缠论分析工作台

一个用于 A 股行情研究和缠论结构分析的本地工作台。

它支持分时、5 分钟、30 分钟、日线、周线和月线行情，计算分型、笔、方向性 L1 笔中枢及周期内高级中枢，并提供图表标注、结构证据和 Agent 追问功能。

> 本项目仅用于研究，所有结果保持 `paper_only`，不连接真实交易账户。

## 一键安装

系统要求：

- macOS 或 Linux
- Python 3.12+
- `uv`
- Node.js 20+

在项目目录执行：

```bash
bash install.sh
```

安装脚本会自动完成：

1. 创建 Python 虚拟环境 `.venv`
2. 安装 `requirements.txt` 中的依赖
3. 安装前端依赖
4. 构建前端页面
5. 创建 `.env`、`data/` 和 `logs/` 目录

如果系统没有 `uv`，请先按官方说明安装：

<https://docs.astral.sh/uv/getting-started/installation/>

如果系统没有 Node.js，请安装 20 或更高版本：

<https://nodejs.org/>

## 配置

安装时会自动生成 `.env`。如需使用 Agent 追问，填写：

```env
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=你的密钥
AI_MODEL=gpt-5.6-sol
AI_API_STYLE=responses
```

不使用 Agent 时可以不填写 `AI_API_KEY`。

`.env` 只保存在本机，不要提交到 Git。

## 启动

推荐使用项目自带脚本后台运行：

```bash
bash start.sh
```

停止服务：

```bash
bash stop.sh
```

服务日志写入 `logs/chant_agent.log`，进程号写入 `logs/chant_agent.pid`。如需修改监听地址或端口，可在启动前设置 `CHANT_AGENT_HOST` 和 `CHANT_AGENT_PORT`。

也可以前台启动并在终端按 `Ctrl+C` 停止：

```bash
uv run --with-requirements requirements.txt --with uvicorn \
  python -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8765
```

浏览器打开：

<http://127.0.0.1:8765/>

## 首次使用

1. 打开页面。
2. 点击左侧“添加自选”。
3. 输入股票名称或代码并添加。
4. 点击“同步行情”。
5. 选择行情周期查看 K 线和缠论结构。
6. 点击笔或中枢，在右侧查看结构证据。

首次启动会自动创建本地 SQLite 数据库：

```text
data/chant_agent.db
```

数据库、行情缓存、自选股和画图数据都属于本机运行数据，不需要从 Git 下载。

## 数据来源

行情来源按可用性使用：

- BaoStock
- `.env` 中配置的行情 API
- 人工导入 CSV

导入 CSV 示例：

```bash
curl -X POST \
  -F 'file=@bars.csv' \
  'http://127.0.0.1:8765/api/market-data/import?symbol=000001&timeframe=5'
```

## 常用接口

```text
GET  /api/watchlist
GET  /api/chart-data/{symbol}?timeframe=d&structure_level=1&limit=300
GET  /api/market-coverage/{symbol}?timeframe=5
POST /api/stock-pool/{symbol}/sync
POST /api/market-data/{symbol}/repair?timeframe=5
POST /api/analyze
```

## 数据备份

Git 仓库不包含数据库。需要保留个人数据时，请在项目外单独备份：

```bash
cp data/chant_agent.db ~/chant_agent-backup.db
```

恢复前先停止服务：

```bash
cp ~/chant_agent-backup.db data/chant_agent.db
```

## 开发验证

后端测试：

```bash
uv run --with-requirements requirements.txt --with pytest python -m pytest -q
```

前端测试和构建：

```bash
cd web
npm test -- --run
npm run build
```

## Git 提交范围

建议提交源码、测试、规则、文档和依赖锁定文件：

```text
app/ tests/ scripts/ knowledge/ docs/ web/src/
README.md requirements.txt pyproject.toml uv.lock
web/package.json web/package-lock.json
.env.example .gitignore
```

不要提交：

```text
.env .venv/ web/node_modules/ web/dist/
data/ logs/ __pycache__/ .pytest_cache/ *.pyc
```
