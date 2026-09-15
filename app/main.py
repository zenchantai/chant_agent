from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import run_agent
from .coverage import validate_coverage, validate_5m_coverage
from .providers import TIMEFRAMES, fetch_api, fetch_baostock, normalize_security_symbol, read_csv
from .store import Store
from .period_structure import PeriodStructureService
from .sync import SyncService
from .rules import DEFINITION_VERSION, PERIOD_DEFINITION_VERSION
from .securities import SecurityCatalogService

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
store = Store(str(ROOT / "data" / "chant_agent.db"))
period_structure_service = PeriodStructureService(store)
sync_service = SyncService(store)
security_catalog_service = SecurityCatalogService(store)
app = FastAPI(title="缠论 AI 交易 Agent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
if (WEB / "dist" / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB / "dist" / "assets"), name="assets")


@app.on_event("startup")
async def startup_event():
    async def refresh_catalog():
        try:
            await asyncio.to_thread(security_catalog_service.refresh_if_stale)
        except Exception:
            # Search requests expose catalog failures without blocking app startup.
            pass
    asyncio.create_task(refresh_catalog())
    for item in store.list_stock_pool():
        for timeframe in TIMEFRAMES:
            if store.market_range(item["symbol"], timeframe, "2")[0]:
                await asyncio.to_thread(period_structure_service.ensure, item["symbol"], timeframe, "2", False)
    await sync_service.start()


@app.on_event("shutdown")
async def shutdown_event():
    await sync_service.stop()


class AnalyzeRequest(BaseModel):
    symbol: str = "DEMO"
    rows: list[dict] | None = None
    csv_path: str | None = None
    api_url: str | None = None
    question: str | None = None
    timeframes: list[str] = list(TIMEFRAMES)
    start_date: str = "2015-01-01"
    end_date: str | None = None
    adjustflag: str = "2"
    refresh: bool = False


class JournalRequest(BaseModel):
    analysis_id: int
    action: str = "WATCH"
    note: str = ""


class StockPoolRequest(BaseModel):
    symbol: str
    name: str = ""
    group_id: int | None = None


class StockPoolPatch(BaseModel):
    name: str | None = None
    move: str | None = None


class StockPoolOrderRequest(BaseModel):
    symbols: list[str]


class WatchlistGroupRequest(BaseModel):
    name: str


class WatchlistGroupOrderRequest(BaseModel):
    group_ids: list[int]


class WatchlistMemberOrderRequest(BaseModel):
    symbols: list[str]


class BacktestRequest(BaseModel):
    rows: list[dict]
    entry_indices: list[int]
    hold_bars: int = 8
    fee_rate: float = 0.0003
    slippage_rate: float = 0.001

class StructureOverrideRequest(BaseModel):
    timeframe: str
    adjustflag: str = "2"
    structure_type: str
    operation: str
    target_id: str | None = None
    payload: dict = {}
    base_run_id: int
    base_structure_version: str

class StructureOverridePatch(BaseModel):
    payload: dict = {}
    base_run_id: int
    base_structure_version: str

class DrawingRequest(BaseModel):
    timeframe: str
    object_type: str
    start_anchor: dict
    end_anchor: dict
    style: dict = {}
    label: str = ""
    visible: bool = True


class DrawingBatchRequest(BaseModel):
    timeframe: str
    base_version: str = ""
    create: list[DrawingRequest] = []
    update: list[dict] = []
    delete_ids: list[int] = []


class StructureOverrideBatchRequest(BaseModel):
    timeframe: str
    adjustflag: str = "2"
    base_run_id: int
    base_structure_version: str
    operations: list[dict] = []


@app.get("/")
def index():
    entry = WEB / "dist" / "index.html"
    return FileResponse(entry if entry.exists() else WEB / "index.html")


@app.get("/api/health")
def health():
    max_level = store.highest_active_center_level(PERIOD_DEFINITION_VERSION)
    return {"ok": True, "mode": "paper_only", "definition_version": PERIOD_DEFINITION_VERSION,
            "calculator_fingerprint": period_structure_service.calculator_fingerprint,
            "timeframes": list(TIMEFRAMES), "structure_mode": "formal_hierarchy",
            "movement_confirmation_mode": "reverse_independent_center",
            "max_center_level": max_level,
            "movement_mode": "hierarchy_component"}


@app.post("/api/analyze")
async def analyze_endpoint(request: AnalyzeRequest):
    try:
        if request.rows:
            rows = request.rows
            timeframe = request.timeframes[0] if request.timeframes else "d"
            _, changed_from = store.upsert_bars_with_changes(request.symbol, timeframe, request.adjustflag, rows)
            precomputed = period_structure_service.ensure(request.symbol, timeframe, request.adjustflag, bool(changed_from))
            result = await run_agent(rows, request.symbol, request.question, precomputed)
        elif request.csv_path:
            rows = read_csv(request.csv_path, request.symbol)
            timeframe = request.timeframes[0] if request.timeframes else "d"
            _, changed_from = store.upsert_bars_with_changes(request.symbol, timeframe, request.adjustflag, rows, source="csv")
            precomputed = period_structure_service.ensure(request.symbol, timeframe, request.adjustflag, bool(changed_from))
            result = await run_agent(rows, request.symbol, request.question, precomputed)
        elif request.api_url:
            fetched = await fetch_api(request.api_url, request.symbol, request.start_date, request.end_date or "", request.adjustflag)
            rows = fetched["rows"]
            timeframe = request.timeframes[0] if request.timeframes else "d"
            _, changed_from = store.upsert_bars_with_changes(request.symbol, timeframe, request.adjustflag, rows, source="api", snapshot_id=fetched["snapshot_id"])
            precomputed = period_structure_service.ensure(request.symbol, timeframe, request.adjustflag, bool(changed_from))
            result = await run_agent(rows, request.symbol, request.question, precomputed)
        elif request.symbol.upper() == "DEMO":
            rows = demo_rows()
            result = await run_agent(rows, request.symbol, request.question)
        else:
            end_date = request.end_date or datetime.now().date().isoformat()
            invalid = set(request.timeframes) - set(TIMEFRAMES)
            if invalid:
                raise ValueError(f"不支持的周期: {', '.join(sorted(invalid))}")
            timeframe_results = {}
            for timeframe in request.timeframes:
                cached_start, cached_end = store.market_range(request.symbol, timeframe, request.adjustflag)
                needs_fetch = request.refresh or not cached_start
                if not cached_start:
                    fetch_start = request.start_date
                else:
                    overlap = (datetime.fromisoformat(cached_end[:10]) - timedelta(days=7)).date().isoformat()
                    fetch_start = max(request.start_date, overlap)
                if needs_fetch:
                    fetched = await asyncio.to_thread(fetch_baostock, request.symbol, timeframe, fetch_start, end_date, request.adjustflag)
                    _, changed_from = store.upsert_bars_with_changes(request.symbol, timeframe, request.adjustflag, fetched)
                else:
                    changed_from = None
                period_rows = store.market_bars(request.symbol, timeframe, request.adjustflag, request.start_date, end_date)
                period_structure = period_structure_service.ensure(request.symbol, timeframe, request.adjustflag, bool(changed_from))
                timeframe_results[timeframe] = await run_agent(period_rows, request.symbol, request.question, period_structure)
            aggregate = {"symbol": request.symbol, "status": "paper_only", "source": "baostock", "timeframes": timeframe_results,
                         "updated_at": datetime.now(timezone.utc).isoformat()}
            saved = store.save(request.symbol, aggregate, aggregate["updated_at"])
            aggregate.update(saved)
            return aggregate
        saved = store.save(request.symbol, result, result["as_of"])
        result.update(saved)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analyses")
def analyses():
    return store.list()


@app.get("/api/stock-pool")
def stock_pool():
    return store.list_stock_pool()


@app.get("/api/watchlist")
def watchlist():
    return store.watchlist()


@app.post("/api/watchlist-groups")
def create_watchlist_group(request: WatchlistGroupRequest):
    try:
        return store.create_watchlist_group(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/watchlist-groups/{group_id}")
def patch_watchlist_group(group_id: int, request: WatchlistGroupRequest):
    try:
        group = store.update_watchlist_group(group_id, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not group:
        raise HTTPException(status_code=404, detail="自选分组不存在")
    return group


@app.delete("/api/watchlist-groups/{group_id}")
def remove_watchlist_group(group_id: int):
    if not store.delete_watchlist_group(group_id):
        raise HTTPException(status_code=404, detail="自选分组不存在")
    return {"ok": True, "groups": store.list_watchlist_groups()}


@app.put("/api/watchlist-groups/order")
def order_watchlist_groups(request: WatchlistGroupOrderRequest):
    try:
        return store.reorder_watchlist_groups(request.group_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/watchlist-groups/{group_id}/members/{symbol}")
def add_watchlist_group_member(group_id: int, symbol: str):
    try:
        symbol = normalize_security_symbol(symbol)
        return store.add_watchlist_group_member(group_id, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/watchlist-groups/{group_id}/members/{symbol}")
def remove_watchlist_group_member(group_id: int, symbol: str):
    try:
        symbol = normalize_security_symbol(symbol)
        removed = store.remove_watchlist_group_member(group_id, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "removed": removed, "group_id": group_id, "symbol": symbol}


@app.put("/api/watchlist-groups/{group_id}/members/order")
def order_watchlist_group_members(group_id: int, request: WatchlistMemberOrderRequest):
    try:
        symbols = [normalize_security_symbol(symbol) for symbol in request.symbols]
        return store.reorder_watchlist_group_members(group_id, symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/securities/search")
async def search_securities(q: str = "", limit: int = 20):
    query = q.strip()
    if len(query) < 2 and not (len(query) == 6 and query.isdigit()):
        return {"query": query, "items": [], **store.security_catalog_meta()}
    try:
        return await asyncio.to_thread(security_catalog_service.search, query, min(max(limit, 1), 20))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"证券目录暂不可用: {exc}") from exc


@app.post("/api/stock-pool")
async def add_stock(request: StockPoolRequest):
    try:
        symbol = normalize_security_symbol(request.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = next((item for item in store.list_stock_pool() if item["symbol"] == symbol), None)
    if existing:
        if request.group_id is not None:
            try:
                store.add_watchlist_group_member(request.group_id, symbol)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**existing, "already_selected": True}
    try:
        candidate = await asyncio.to_thread(security_catalog_service.resolve, symbol)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"证券资料暂不可用: {exc}") from exc
    if not candidate:
        raise HTTPException(status_code=404, detail="未找到该证券，请先通过搜索选择有效证券")
    try:
        if request.group_id is None:
            item = await sync_service.add_stock(symbol, candidate["name"], sync=True)
        else:
            item = await sync_service.add_stock(
                symbol, candidate["name"], sync=True, group_id=request.group_id
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**item, "sync_status": "pending", "already_selected": False}


@app.patch("/api/stock-pool/{symbol}")
def patch_stock(symbol: str, request: StockPoolPatch):
    item = store.update_stock(symbol, request.name, request.move)
    if not item:
        raise HTTPException(status_code=404, detail="股票不在股票池中")
    return item


@app.put("/api/stock-pool/order")
def order_stock_pool(request: StockPoolOrderRequest):
    try:
        return store.reorder_stock_pool(request.symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/stock-pool/{symbol}")
def remove_stock(symbol: str):
    if not store.delete_stock(symbol):
        raise HTTPException(status_code=404, detail="股票不在股票池中")
    return {"ok": True, "symbol": symbol}


@app.post("/api/stock-pool/sync")
async def sync_all_stocks():
    asyncio.create_task(sync_service.sync_pool(TIMEFRAMES, "incremental"))
    return {"ok": True, "status": "pending"}


@app.post("/api/stock-pool/{symbol}/sync")
async def sync_stock(symbol: str):
    if not any(item["symbol"] == symbol for item in store.list_stock_pool()):
        raise HTTPException(status_code=404, detail="股票不在股票池中")
    asyncio.create_task(sync_service.sync_pool(TIMEFRAMES, "incremental", symbols=[symbol]))
    return {"ok": True, "symbol": symbol, "status": "pending"}


@app.get("/api/sync-runs")
def sync_runs(limit: int = 100):
    return store.list_sync_runs(min(max(limit, 1), 500))


@app.get("/api/market-data/{symbol}")
def market_data(symbol: str, timeframe: str = "d", adjustflag: str = "2", start_date: str = "2015-01-01", end_date: str = "9999-12-31", limit: int = 3000):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    rows = store.market_bars(symbol, timeframe, adjustflag, start_date, end_date, min(max(limit, 1), 3000))
    return {"symbol": symbol, "timeframe": timeframe, "adjustflag": adjustflag, "count": len(rows), "rows": rows}


@app.get("/api/chart-data/{symbol}")
def chart_data(symbol: str, timeframe: str = "d", adjustflag: str = "2", before: str | None = None, limit: int = 300, ma_periods: str = "5,10,20,60", boll_period: int = 20, boll_multiplier: float = 2.0, structure_level: int = 1):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    periods = tuple(sorted({int(item) for item in ma_periods.split(",") if item.strip()})) or (5, 10, 20, 60)
    periods = tuple(item for item in periods if 1 <= item <= 1000)[:10] or (5, 10, 20, 60)
    result = period_structure_service.chart_page(symbol, timeframe, adjustflag, before, min(max(limit, 1), 300), periods, max(1, min(boll_period, 1000)), max(0.01, boll_multiplier))
    result["active_structure_level"] = 1
    result["coverage_required"] = True
    result["coverage_timeframe"] = timeframe
    stored = store.market_coverage(symbol, timeframe, adjustflag)
    if stored:
        # Keep `coverage` reserved for the 5-minute structure source; expose
        # the selected chart period independently so a complete daily series
        # cannot mask a 5-minute structural gap.
        result["sampling_coverage"] = stored["payload"]
        result["sampling_coverage_version"] = stored["coverage_version"]
    return result


def _structure_base(symbol: str, timeframe: str, adjustflag: str, run_id: int, version: str):
    run = store.active_period_structure_run(symbol, timeframe, adjustflag)
    if not run or run["id"] != run_id or run.get("structure_version", "") != version:
        raise HTTPException(status_code=409, detail="结构快照已变化，请刷新后重试")
    return run


def _validate_structure_override_target(
    run: dict,
    structure_type: str,
    operation: str,
    target_id: str | None,
    payload: dict | None = None,
):
    """Restrict manual structural edits to pens and canonical L1 centers.

    Same-level evidence centers and all derived higher-level centers are
    read-only.  Validation happens before an override row is written so an
    invalid request cannot become an apparently active but irreproducible
    manual snapshot.
    """
    if structure_type == "pen":
        if operation == "create":
            return
        if not target_id:
            raise HTTPException(status_code=400, detail="笔修订缺少 target_id")
        nodes = store.period_rows("period_pens", run["id"])
        if not any(node.get("id") == target_id for node in nodes):
            raise HTTPException(status_code=400, detail="目标笔不存在或已失效")
        return
    if structure_type != "pen_center":
        raise HTTPException(status_code=400, detail="结构修订类型不合法")
    if operation == "create":
        candidate = payload or {}
        try:
            level = int(candidate.get("level", 1))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="中枢级别不合法") from exc
        role = candidate.get("role", "hierarchy")
        if level != 1 or role != "hierarchy":
            raise HTTPException(status_code=400, detail="只能人工修订 L1 hierarchy 中枢")
        return
    if not target_id:
        raise HTTPException(status_code=400, detail="中枢修订缺少 target_id")
    nodes = store.period_rows("period_pen_centers", run["id"])
    target = next((node for node in nodes if node.get("id") == target_id), None)
    if target is None:
        raise HTTPException(status_code=400, detail="目标中枢不存在或已失效")
    try:
        level = int(target.get("level", 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="目标中枢级别不合法") from exc
    if level != 1 or target.get("role", "hierarchy") != "hierarchy":
        raise HTTPException(status_code=400, detail="只能人工修订 L1 hierarchy 中枢")

@app.get("/api/structure-overrides/{symbol}")
def list_structure_overrides(symbol: str, timeframe: str = "d", adjustflag: str = "2"):
    if timeframe not in TIMEFRAMES: raise HTTPException(status_code=400, detail="不支持的周期")
    return period_structure_service.effective_structure(symbol, timeframe, adjustflag).get("overrides", [])

@app.post("/api/structure-overrides/{symbol}")
def create_structure_override(symbol: str, request: StructureOverrideRequest):
    raise HTTPException(status_code=409, detail="严格走势确认模式暂时关闭人工结构修订；历史记录保留且不参与计算")


@app.post("/api/structure-overrides/{symbol}/batch")
def batch_structure_overrides(symbol: str, request: StructureOverrideBatchRequest):
    raise HTTPException(status_code=409, detail="严格走势确认模式暂时关闭人工结构修订；历史记录保留且不参与计算")


@app.patch("/api/structure-overrides/{symbol}/{override_id}")
def patch_structure_override(symbol: str, override_id: int, request: StructureOverridePatch):
    raise HTTPException(status_code=409, detail="严格走势确认模式暂时关闭人工结构修订；历史记录保留且不参与计算")


@app.delete("/api/structure-overrides/{symbol}/{override_id}")
def delete_structure_override(symbol: str, override_id: int):
    raise HTTPException(status_code=409, detail="严格走势确认模式暂时关闭人工结构修订；历史记录保留且不参与计算")


@app.post("/api/structure-overrides/{symbol}/{override_id}/restore")
def restore_structure_override(symbol: str, override_id: int):
    raise HTTPException(status_code=409, detail="严格走势确认模式暂时关闭人工结构修订；历史记录保留且不参与计算")


@app.post("/api/structure-overrides/{symbol}/restore-all")
def restore_all_structure_overrides(symbol: str, timeframe: str = "d", adjustflag: str = "2"):
    raise HTTPException(status_code=409, detail="严格走势确认模式暂时关闭人工结构修订；历史记录保留且不参与计算")


def _validate_drawing(symbol: str, request: DrawingRequest):
    if request.timeframe not in TIMEFRAMES or request.object_type not in {"segment", "line", "rectangle"}:
        raise HTTPException(status_code=400, detail="绘图参数不合法")
    for anchor in (request.start_anchor, request.end_anchor):
        if not anchor.get("trade_date") or not isinstance(anchor.get("price"), (int, float)):
            raise HTTPException(status_code=400, detail="绘图锚点必须包含交易日和价格")
    style = request.style or {}
    color_keys = ("color", "fill_color") if request.object_type == "rectangle" else ("color",)
    for key in color_keys:
        if key in style and style[key] is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", str(style[key])):
            raise HTTPException(status_code=400, detail=f"{key}必须是#RRGGBB或#RRGGBBAA")
    if "width" in style:
        try: width = float(style["width"])
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="线宽必须是数字")
        if not 1 <= width <= 10: raise HTTPException(status_code=400, detail="线宽必须在1到10之间")
    if style.get("line_type") not in (None, "solid", "dashed", "dotted"):
        raise HTTPException(status_code=400, detail="线型必须是solid、dashed或dotted")
    if request.object_type != "rectangle" and any(key in style for key in ("fill_color", "fill_opacity")):
        raise HTTPException(status_code=400, detail="只有矩形支持填充样式")
    for key in ("opacity", "fill_opacity"):
        if key in style:
            try: opacity = float(style[key])
            except (TypeError, ValueError): raise HTTPException(status_code=400, detail="透明度必须是数字")
            if not 0 <= opacity <= 1: raise HTTPException(status_code=400, detail="透明度必须在0到1之间")

@app.get("/api/drawings/{symbol}")
def list_drawings(symbol: str, timeframe: str = "d"):
    if timeframe not in TIMEFRAMES: raise HTTPException(status_code=400, detail="不支持的周期")
    return store.drawings(symbol, timeframe)

@app.post("/api/drawings/{symbol}")
def create_drawing(symbol: str, request: DrawingRequest):
    _validate_drawing(symbol, request)
    item = request.model_dump(); item["symbol"] = symbol
    drawing_id = store.create_drawing(item)
    return store.drawing(drawing_id)

@app.patch("/api/drawings/{symbol}/{drawing_id}")
def patch_drawing(symbol: str, drawing_id: int, request: DrawingRequest):
    _validate_drawing(symbol, request)
    current = store.drawing(drawing_id)
    if not current or current["symbol"] != symbol: raise HTTPException(status_code=404, detail="绘图不存在")
    item = request.model_dump(); item["symbol"] = symbol
    return store.update_drawing(drawing_id, item)

@app.delete("/api/drawings/{symbol}/{drawing_id}")
def remove_drawing(symbol: str, drawing_id: int):
    current = store.drawing(drawing_id)
    if not current or current["symbol"] != symbol: raise HTTPException(status_code=404, detail="绘图不存在")
    return {"ok": store.delete_drawing(drawing_id)}

@app.post("/api/drawings/{symbol}/batch")
def batch_drawings(symbol: str, request: DrawingBatchRequest):
    if request.timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="不支持的周期")
    for item in request.create:
        if item.timeframe != request.timeframe:
            raise HTTPException(status_code=400, detail="批量绘图周期不一致")
        _validate_drawing(symbol, item)
    for raw in request.update:
        try:
            drawing_id = int(raw["id"])
            drawing = DrawingRequest(**raw["drawing"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="绘图更新参数不合法") from exc
        if drawing.timeframe != request.timeframe:
            raise HTTPException(status_code=400, detail="批量绘图周期不一致")
        _validate_drawing(symbol, drawing)
        current = store.drawing(drawing_id)
        if not current or current["symbol"] != symbol or current["timeframe"] != request.timeframe:
            raise HTTPException(status_code=404, detail=f"绘图不存在: {drawing_id}")
    try:
        return store.batch_drawings(symbol, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/market-coverage/{symbol}")
def market_coverage(symbol: str, timeframe: str = "5", adjustflag: str = "2"):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    coverage = period_structure_service.coverage(symbol, timeframe, adjustflag)
    meta = store.save_market_coverage(symbol, timeframe, adjustflag, coverage)
    coverage["coverage_version"] = meta["coverage_version"]
    coverage["gap_tasks"] = store.market_gap_tasks(symbol, timeframe, adjustflag)
    return {"symbol": symbol, "timeframe": timeframe, "adjustflag": adjustflag, **coverage}


@app.post("/api/market-data/{symbol}/repair")
async def repair_market_data(symbol: str, timeframe: str = "5", adjustflag: str = "2"):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    await sync_service.sync_one(symbol, timeframe, "incremental", adjustflag=adjustflag)
    return market_coverage(symbol, timeframe, adjustflag)


@app.post("/api/journals")
def create_journal(request: JournalRequest):
    allowed = {"WATCH", "CONFIRMED", "REJECTED", "PAPER_ENTER", "PAPER_EXIT"}
    if request.action not in allowed:
        raise HTTPException(status_code=400, detail="unsupported journal action")
    return store.journal(request.analysis_id, request.action, request.note, datetime.now(timezone.utc).isoformat())


@app.get("/api/journals")
def journals():
    return store.journals()


@app.post("/api/backtest")
def backtest(request: BacktestRequest):
    trades = []
    for index in request.entry_indices:
        entry_index = index + 1
        exit_index = min(entry_index + request.hold_bars, len(request.rows) - 1)
        if entry_index >= len(request.rows):
            continue
        entry = float(request.rows[entry_index]["open"]) * (1 + request.slippage_rate)
        exit_price = float(request.rows[exit_index]["open"]) * (1 - request.slippage_rate)
        net_return = exit_price / entry - 1 - request.fee_rate * 2
        trades.append({"signal_index": index, "entry_index": entry_index, "exit_index": exit_index, "entry": entry, "exit": exit_price, "net_return": net_return})
    return {"trades": trades, "count": len(trades), "total_return": float(sum(t["net_return"] for t in trades)), "execution": "next_bar_open"}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    target = ROOT / "data" / file.filename
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(await file.read())
    return {"path": str(target), "filename": file.filename}


@app.post("/api/market-data/import")
async def import_market_data(file: UploadFile = File(...), symbol: str = "", timeframe: str = "5", adjustflag: str = "2"):
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(status_code=400, detail="symbol必须是6位数字")
    target = ROOT / "data" / "imports" / file.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
    try:
        rows = read_csv(str(target), symbol)
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"不支持的周期: {timeframe}")
        coverage = validate_5m_coverage(rows) if timeframe == "5" else validate_coverage(rows, timeframe)
        count, changed_from = store.upsert_bars_with_changes(symbol, timeframe, adjustflag, rows, source="csv")
        structure = period_structure_service.ensure(symbol, timeframe, adjustflag, bool(changed_from))
        return {"symbol": symbol, "imported": count, "coverage": coverage,
                "structure_available": structure.get("available", False), "structure_version": structure.get("structure_version"),
                "stale_reason": structure.get("stale_reason"), "conflicts": len(coverage["duplicates"])}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def demo_rows():
    rows = []
    price = 100.0
    for i in range(90):
        drift = 0.7 if i < 20 else (-0.8 if i < 42 else (0.55 if i < 68 else -0.15))
        price += drift + ((i * 17) % 7 - 3) * 0.18
        rows.append({"trade_date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "open": price - 0.35, "high": price + 0.8, "low": price - 0.9, "close": price, "volume": 100000 + i * 500})
    return rows
