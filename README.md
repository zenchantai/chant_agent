# 缠论分析工作台

一个用于 A 股行情研究和缠论结构分析的本地工作台。

它支持分时、5 分钟、30 分钟、日线、周线和月线行情，计算分型、笔、方向性 L1 笔中枢及周期内高级中枢，并提供图表标注、结构证据和 Agent 追问功能。

> 本项目仅用于研究，所有结果保持 `paper_only`，不连接真实交易账户。

## 实时周期刷新

分时图始终显示 09:30–15:00 的完整交易日时间轴，午休折叠为同一个 `11:30/13:00` 坐标，13:01 紧接 11:30，午间连线不留空隙；未到达或其他缺失的分钟留白，主图与副图统一按时间对齐，不因实时数据增加而拉伸，不使用旧横向缩放范围。

打开分时图、5 分钟、30 分钟、日线、周线或月线时，先显示缓存，再获取当前周期最新数据。交易时段内按上海时区与交易日历约每 15 秒刷新；午休、收盘或周期边界后按 `next_period_finalize_at` 补取一次，然后暂停至下一交易时段。页面隐藏时停止轮询，返回页面立即补刷；切换股票或周期时取消旧请求。

5 分钟、30 分钟和日线的最后一根未收口 K 线以 `provisional` 实时预览显示，不写入正式结构；周线、月线由已确认日线和当日预览聚合，并按 ISO 周/月键合并为一根逻辑 K 线。周期收口后重新拉取并确认，才更新正式行情和结构。刷新只更新当前股票和当前周期，不执行股票池全周期同步；副图、缩放和未保存的绘图不因自动刷新重置。失败时保留旧图并显示提示，非今日数据明确标注日期；交易日历缺失时显示“日历未知”，每 60 秒尝试恢复日历。15 秒是请求频率，不代表数据源提供逐笔或秒级行情。

接口 `GET /api/chart-data/{symbol}?timeframe=1|5|30|d|w|m&refresh=true` 主动获取当前周期数据；省略 `refresh` 时只读缓存和已有预览。`refresh=true` 不允许搭配 `before`。响应中的 `intraday_refresh` 保持兼容，并增加 `period_refresh`，包含逻辑周期键、`cached`/`provisional`/`confirmed`/`stale` 状态、服务器时间、下次收口时间、数据时间和错误提示。昨收无法确认或成交额不可用时显示 `--`。

## 周期内严格走势模型

日线等周期各自使用本周期笔构造方向性 L1 中枢，不依赖缺失的分钟行情。走势只有在同级反向独立中枢确认后才结束；最右侧以虚线显示 `provisional`，候选极值和实际尾部分开保存。三个连续、已确认的低级正式走势重叠才生成高级中枢，不按九笔数量自动升级。L1/L2/L3 是本周期内部结构级别，配色不代表真实周线或月线。

人工结构修订暂时禁用，历史修订仍保留但不参与计算；普通画线和矩形标注继续可用。旧结构快照留作审计，新算法必须从本地行情重新计算。迁移前停止服务并备份数据库；重算后运行 `scripts/audit_structure.py`，核对反向确认中枢与版本指纹。

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

左侧所有分组（含“全部”“未分组”）支持拖动标题混合排序：拖到目标标题上半部插入前面，下半部插入后面，以插入线为准。松开后自动保存，刷新仍保留顺序；系统分组不能重命名或删除。分组内股票仍可独立拖动排序。

左侧价格与涨跌幅使用腾讯批量行情快照，交易时段每 10 秒刷新；按上海时区和交易日历判断时段，午休、休市及日历未知时降为每 60 秒。隐藏页面暂停、返回立即补刷，切换主图周期不停止左栏更新。报价缓存仅保存在服务内存中，不写入 K 线或触发结构重算；启动后重新获取。显示报价时间，过期、缺失或失败时明确标记，不回退到历史日线冒充实时价格。行情源存在延迟，轮询频率不代表逐笔实时保障。

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
