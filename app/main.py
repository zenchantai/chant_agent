from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import run_agent
from .coverage import validate_5m_coverage, validate_coverage
from .indicators import calculate_bollinger, calculate_macd, calculate_moving_averages
from .intraday import LIVE_STRUCTURE_PERIODS, IntradayService, merge_period_rows
from .period_structure import PeriodStructureService, REFERENCE_TIMEFRAMES, STRUCTURE_TIMEFRAMES, calculation_profile
from .providers import TIMEFRAMES, fetch_api, fetch_baostock, normalize_security_symbol, read_csv
from .rules import PERIOD_DEFINITION_VERSION
from .securities import SecurityCatalogService
from .store import Store
from .structure_display import project_center_display, project_daily_l2
from .sync import SyncService
from .watchlist_quotes import WatchlistQuoteService


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
WEB_DIST = Path(os.environ.get("CHANT_AGENT_WEB_DIST", str(WEB / "dist")))
store = Store(os.environ.get("CHANT_AGENT_DB_PATH", str(ROOT / "data" / "chant_agent.db")))
period_structure_service = PeriodStructureService(store)
intraday_service = IntradayService(store)
watchlist_quote_service = WatchlistQuoteService(store, intraday_service)
sync_service = SyncService(store, structures=period_structure_service, intraday=intraday_service)
security_catalog_service = SecurityCatalogService(store)
app = FastAPI(title="缠论 AI 交易 Agent", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
if (WEB_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.on_event("startup")
async def startup_event():
    async def refresh_catalog():
        try:
            await asyncio.to_thread(security_catalog_service.refresh_if_stale)
        except Exception:
            pass

    background_sync = os.environ.get("CHANT_AGENT_BACKGROUND_SYNC", "1") != "0"
    if background_sync:
        asyncio.create_task(refresh_catalog())
    for item in store.list_stock_pool():
        for timeframe in STRUCTURE_TIMEFRAMES:
            if store.market_range(item["symbol"], timeframe, "2")[0]:
                await asyncio.to_thread(
                    period_structure_service.ensure, item["symbol"], timeframe, "2", False,
                )
    if background_sync:
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
    timeframes: list[str] = Field(default_factory=lambda: list(STRUCTURE_TIMEFRAMES))
    start_date: str = "2015-01-01"
    end_date: str | None = None
    adjustflag: str = "2"
    refresh: bool = False


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


class WatchlistSectionOrderRequest(BaseModel):
    section_keys: list[str]


class WatchlistMemberOrderRequest(BaseModel):
    symbols: list[str]


class DrawingRequest(BaseModel):
    timeframe: str
    object_type: str
    start_anchor: dict
    end_anchor: dict
    style: dict = Field(default_factory=dict)
    label: str = ""
    visible: bool = True


class DrawingBatchRequest(BaseModel):
    timeframe: str
    base_version: str = ""
    create: list[DrawingRequest] = Field(default_factory=list)
    update: list[dict] = Field(default_factory=list)
    delete_ids: list[int] = Field(default_factory=list)


@app.get("/")
def index():
    entry = WEB_DIST / "index.html"
    return FileResponse(entry if entry.exists() else WEB / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mode": "paper_only",
        "definition_version": PERIOD_DEFINITION_VERSION,
        "calculator_fingerprint": period_structure_service.calculator_fingerprint,
        "timeframes": list(TIMEFRAMES),
        "structure_timeframes": list(STRUCTURE_TIMEFRAMES),
        "structure_mode": "period_profiled",
        "calculation_profiles": {period: calculation_profile(period) for period in STRUCTURE_TIMEFRAMES},
        "reference_timeframes": list(REFERENCE_TIMEFRAMES),
        "decomposition_mode": "non_same_level",
        "base_unit_mode": "current_period_confirmed_pen",
        "center_selection_mode": "entry_then_earliest_three_unit_core",
        "center_envelope_mode": "owned_z_units",
        "center_promotion_mode": "verified_expansion_or_recursive_core",
        "envelope_touch_mode": "candidate",
        "movement_boundary_mode": "structural_buy_sell_point",
        "divergence_mode": "structural_strength_vector",
        "max_computed_level": store.highest_active_chan_level(PERIOD_DEFINITION_VERSION),
        "active_run_status": "ready",
    }


@app.post("/api/analyze")
async def analyze_endpoint(request: AnalyzeRequest):
    try:
        if request.rows is not None:
            timeframe = request.timeframes[0] if request.timeframes else "d"
            _, changed_from = store.upsert_bars_with_changes(
                request.symbol, timeframe, request.adjustflag, request.rows,
            )
            snapshot = period_structure_service.ensure(
                request.symbol, timeframe, request.adjustflag, bool(changed_from),
            )
            return await run_agent(request.rows, request.symbol, request.question, snapshot)
        if request.csv_path:
            timeframe = request.timeframes[0] if request.timeframes else "d"
            rows = read_csv(request.csv_path, request.symbol)
            _, changed_from = store.upsert_bars_with_changes(
                request.symbol, timeframe, request.adjustflag, rows, source="csv",
            )
            snapshot = period_structure_service.ensure(
                request.symbol, timeframe, request.adjustflag, bool(changed_from),
            )
            return await run_agent(rows, request.symbol, request.question, snapshot)
        if request.api_url:
            timeframe = request.timeframes[0] if request.timeframes else "d"
            fetched = await fetch_api(
                request.api_url, request.symbol, request.start_date, request.end_date or "", request.adjustflag,
            )
            rows = fetched["rows"]
            _, changed_from = store.upsert_bars_with_changes(
                request.symbol, timeframe, request.adjustflag, rows,
                source="api", snapshot_id=fetched["snapshot_id"],
            )
            snapshot = period_structure_service.ensure(
                request.symbol, timeframe, request.adjustflag, bool(changed_from),
            )
            return await run_agent(rows, request.symbol, request.question, snapshot)
        if request.symbol.upper() == "DEMO":
            rows = demo_rows()
            return await run_agent(rows, request.symbol, request.question)

        invalid = set(request.timeframes) - set(STRUCTURE_TIMEFRAMES)
        if invalid:
            raise ValueError(f"不支持的结构周期: {', '.join(sorted(invalid))}")
        end_date = request.end_date or datetime.now().date().isoformat()
        results = {}
        for timeframe in request.timeframes:
            cached_start, cached_end = store.market_range(request.symbol, timeframe, request.adjustflag)
            changed_from = None
            if request.refresh or not cached_start:
                fetch_start = request.start_date
                if cached_end:
                    overlap = (datetime.fromisoformat(cached_end[:10]) - timedelta(days=7)).date().isoformat()
                    fetch_start = max(fetch_start, overlap)
                fetched = await asyncio.to_thread(
                    fetch_baostock, request.symbol, timeframe, fetch_start, end_date, request.adjustflag,
                )
                _, changed_from = store.upsert_bars_with_changes(
                    request.symbol, timeframe, request.adjustflag, fetched,
                )
            rows = store.market_bars(
                request.symbol, timeframe, request.adjustflag, request.start_date, end_date,
            )
            snapshot = period_structure_service.ensure(
                request.symbol, timeframe, request.adjustflag, bool(changed_from),
            )
            results[timeframe] = await run_agent(rows, request.symbol, request.question, snapshot)
        return {
            "symbol": request.symbol,
            "status": "paper_only",
            "timeframes": results,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/stock-pool")
def stock_pool():
    return store.list_stock_pool()


@app.get("/api/watchlist")
def watchlist():
    return store.watchlist()


@app.put("/api/watchlist/order")
def order_watchlist_sections(request: WatchlistSectionOrderRequest):
    try:
        return {"section_order": store.reorder_watchlist_sections(request.section_keys)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/watchlist/quotes")
def watchlist_quotes(current_symbol: str | None = None):
    return watchlist_quote_service.snapshot(current_symbol=current_symbol)


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
        return store.add_watchlist_group_member(group_id, normalize_security_symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/watchlist-groups/{group_id}/members/{symbol}")
def remove_watchlist_group_member(group_id: int, symbol: str):
    try:
        normalized = normalize_security_symbol(symbol)
        removed = store.remove_watchlist_group_member(group_id, normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "removed": removed, "group_id": group_id, "symbol": normalized}


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
            store.add_watchlist_group_member(request.group_id, symbol)
        return {**existing, "already_selected": True}
    try:
        candidate = await asyncio.to_thread(security_catalog_service.resolve, symbol)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"证券资料暂不可用: {exc}") from exc
    if not candidate:
        raise HTTPException(status_code=404, detail="未找到该证券")
    item = await sync_service.add_stock(
        symbol, candidate["name"], sync=True, group_id=request.group_id,
    )
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


@app.get("/api/market-data/{symbol}")
def market_data(
    symbol: str, timeframe: str = "d", adjustflag: str = "2",
    start_date: str = "2015-01-01", end_date: str = "9999-12-31", limit: int = 3000,
):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    rows = store.market_bars(
        symbol, timeframe, adjustflag, start_date, end_date, min(max(limit, 1), 3000),
    )
    return {
        "symbol": symbol, "timeframe": timeframe, "adjustflag": adjustflag,
        "count": len(rows), "rows": rows,
    }


def _live_quote(rows: list[dict], *, daily_rows: list[dict], metadata: dict,
                snapshot: dict | None) -> dict | None:
    if not rows:
        return None
    latest = rows[-1]
    day = latest["trade_date"][:10]
    today_rows = [row for row in rows if row["trade_date"][:10] == day]
    previous_close = next(
        (row["close"] for row in reversed(daily_rows) if row["trade_date"][:10] < day), None,
    )
    quote_latest = float(snapshot["latest"]) if snapshot and snapshot.get("latest") is not None else latest["close"]
    previous_close = snapshot.get("previous_close") if snapshot else previous_close
    change = snapshot.get("change") if snapshot else (quote_latest - previous_close if previous_close else None)
    high = max(row["high"] for row in today_rows)
    low = min(row["low"] for row in today_rows)
    return {
        "trade_date": latest["trade_date"], "latest": quote_latest,
        "previous_close": previous_close, "change": change,
        "change_pct": snapshot.get("change_pct") if snapshot else (change / previous_close * 100 if previous_close else None),
        "open": today_rows[0]["open"], "high": high, "low": low,
        "volume": sum(row["volume"] for row in today_rows),
        "amount": sum(row["amount"] for row in today_rows) if any(row["amount"] for row in today_rows) else None,
        "amplitude_pct": (high - low) / previous_close * 100 if previous_close else None,
        "market_status": metadata["market_status"],
        "quote_time": snapshot.get("quote_time") if snapshot else metadata.get("latest_data_at"),
        "source": snapshot.get("source", "tencent") if snapshot else "tencent",
        "status": snapshot.get("status", "success") if snapshot else "success",
    }


@app.get("/api/chart-data/{symbol}")
def chart_data(
    symbol: str, timeframe: str = "d", adjustflag: str = "2", before: str | None = None,
    limit: int = 300, ma_periods: str = "5,10,20,60", boll_period: int = 20,
    boll_multiplier: float = 2.0, structure_level: int = 1,
    diagnostics: bool = False, refresh: bool = False,
):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    if refresh and before is not None:
        raise HTTPException(status_code=400, detail="实时刷新不支持历史分页参数 before")
    periods = tuple(sorted({int(item) for item in ma_periods.split(",") if item.strip()}))
    periods = tuple(item for item in periods if 1 <= item <= 1000)[:10] or (5, 10, 20, 60)
    page_limit = min(max(limit, 1), 300)
    boll_period = max(1, min(boll_period, 1000))
    boll_multiplier = max(0.01, boll_multiplier)
    if timeframe == "1":
        if refresh:
            intraday_service.refresh_period(symbol, timeframe, adjustflag, include_quote=True)
        return intraday_service.chart_page(
            symbol, adjustflag, before, page_limit, periods, boll_period, boll_multiplier,
        )

    attempt = intraday_service.refresh_period(
        symbol, timeframe, adjustflag, include_quote=True,
    ) if refresh else None
    daily_error = intraday_service.daily_source_error(symbol, adjustflag)
    if refresh and timeframe in REFERENCE_TIMEFRAMES:
        try:
            daily_attempt = intraday_service.refresh_period(symbol, "d", adjustflag)
            if daily_attempt.get("result") != "success":
                daily_error = daily_attempt.get("error") or "日线刷新失败，显示已确认数据"
            else:
                daily_error = intraday_service.daily_source_error(symbol, adjustflag)
        except Exception as exc:
            daily_error = f"日线刷新失败：{type(exc).__name__}"
    # Calendar helpers acquire their own lock before the Store lock. Read live
    # presentation state first, never call them inside the captured DB snapshot.
    live_rows = intraday_service.live_rows(symbol, timeframe, adjustflag)
    include_live = bool(refresh or live_rows)
    forming = intraday_service.forming_bar(symbol, timeframe, adjustflag) if include_live else None
    refresh_meta = intraday_service.period_metadata(symbol, timeframe, adjustflag) if include_live else None
    quote_snapshot = intraday_service.latest_quote(symbol, adjustflag) if include_live else None
    preview, daily_snapshot = None, None
    all_rows, quote_daily_rows = [], []
    # Fetches have finished. The run, page, coverage, and projection source now
    # come from one Store state even when another request is activating a run.
    with store._lock:
        snapshot = period_structure_service.ensure(
            symbol, timeframe, adjustflag,
            bool(attempt and attempt.get("finalized_count") and timeframe in STRUCTURE_TIMEFRAMES),
        )
        result = period_structure_service.chart_page(
            symbol, timeframe, adjustflag, before, page_limit, periods,
            boll_period, boll_multiplier, structure_level, diagnostics,
            snapshot=snapshot,
        )
        formal_coverage = snapshot["meta"].get("coverage") or period_structure_service.coverage(
            symbol, timeframe, adjustflag,
        )
        if include_live:
            stored_rows = store.market_bars(symbol, timeframe, adjustflag, "0000-01-01")
            all_rows = merge_period_rows(stored_rows, live_rows, timeframe)
            quote_daily_rows = store.confirmed_daily_bars(symbol, adjustflag)
            if forming and timeframe in LIVE_STRUCTURE_PERIODS:
                preview = period_structure_service.preview_period(symbol, timeframe, adjustflag, all_rows)
        if timeframe in REFERENCE_TIMEFRAMES:
            try:
                daily_snapshot = period_structure_service.ensure(symbol, "d", adjustflag)
            except Exception as exc:
                daily_error = f"日线正式结构暂不可用：{type(exc).__name__}"
    display_source = snapshot["structure"]
    if include_live:
        eligible = [row for row in all_rows if before is None or row["trade_date"] < before]
        page = eligible[-page_limit:]
        if forming:
            page = [
                {
                    **row,
                    "is_forming": row["trade_date"] == forming["trade_date"],
                    "status": "provisional" if row["trade_date"] == forming["trade_date"] else "confirmed",
                }
                for row in page
            ]
        stamps = {row["trade_date"] for row in page}
        result["market"].update({
            "bars": page,
            "quote": _live_quote(all_rows, daily_rows=quote_daily_rows,
                                 metadata=refresh_meta, snapshot=quote_snapshot),
            "forming_bar": forming,
            "intraday_refresh": refresh_meta,
        })
        result["pagination"] = {
            "has_more": len(eligible) > len(page),
            "next_before": page[0]["trade_date"] if page else None,
        }
        result["indicators"] = {
            "macd": [item for item in calculate_macd(all_rows) if item["trade_date"] in stamps],
            "ma": [
                {**item, "values": {str(period): item.get(f"ma{period}") for period in periods}}
                for item in calculate_moving_averages(all_rows, periods)
                if item["trade_date"] in stamps
            ],
            "boll": [
                item for item in calculate_bollinger(all_rows, boll_period, boll_multiplier)
                if item["trade_date"] in stamps
            ],
        }
        if preview is not None:
            result["meta"].update(preview["meta"])
            result["structure"] = preview["structure"]
            display_source = preview["structure"]
        else:
            result["meta"].update({"preview": False, "persisted": True})
        result["meta"]["available"] = bool(page)
    result["meta"]["formal_coverage"] = formal_coverage
    result["meta"]["sampling_coverage"] = preview["meta"]["coverage"] if preview else formal_coverage
    result = project_center_display(result, display_source, structure_level)
    if timeframe in REFERENCE_TIMEFRAMES:
        result = project_daily_l2(result, daily_snapshot, daily_error)
    return result


def _validate_drawing(symbol: str, request: DrawingRequest):
    del symbol
    if request.timeframe not in TIMEFRAMES or request.object_type not in {"segment", "line", "rectangle"}:
        raise HTTPException(status_code=400, detail="绘图参数不合法")
    for anchor in (request.start_anchor, request.end_anchor):
        if not anchor.get("trade_date") or not isinstance(anchor.get("price"), (int, float)):
            raise HTTPException(status_code=400, detail="绘图锚点必须包含交易日和价格")
    color_keys = ("color", "fill_color") if request.object_type == "rectangle" else ("color",)
    for key in color_keys:
        value = request.style.get(key)
        if value is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", str(value)):
            raise HTTPException(status_code=400, detail=f"{key}必须是#RRGGBB或#RRGGBBAA")
    if "width" in request.style and not 1 <= float(request.style["width"]) <= 10:
        raise HTTPException(status_code=400, detail="线宽必须在1到10之间")
    if request.style.get("line_type") not in (None, "solid", "dashed", "dotted"):
        raise HTTPException(status_code=400, detail="线型不合法")
    if request.object_type != "rectangle" and any(
        key in request.style for key in ("fill_color", "fill_opacity")
    ):
        raise HTTPException(status_code=400, detail="只有矩形支持填充样式")
    for key in ("opacity", "fill_opacity"):
        if key in request.style and not 0 <= float(request.style[key]) <= 1:
            raise HTTPException(status_code=400, detail="透明度必须在0到1之间")


@app.get("/api/drawings/{symbol}")
def list_drawings(symbol: str, timeframe: str = "d"):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="不支持的周期")
    return {"items": store.drawings(symbol, timeframe), "version": store.drawings_version(symbol, timeframe)}


@app.post("/api/drawings/{symbol}")
def create_drawing(symbol: str, request: DrawingRequest):
    _validate_drawing(symbol, request)
    item = request.model_dump()
    item["symbol"] = symbol
    return store.drawing(store.create_drawing(item))


@app.patch("/api/drawings/{symbol}/{drawing_id}")
def patch_drawing(symbol: str, drawing_id: int, request: DrawingRequest):
    _validate_drawing(symbol, request)
    current = store.drawing(drawing_id)
    if not current or current["symbol"] != symbol:
        raise HTTPException(status_code=404, detail="绘图不存在")
    item = request.model_dump()
    item["symbol"] = symbol
    return store.update_drawing(drawing_id, item)


@app.delete("/api/drawings/{symbol}/{drawing_id}")
def remove_drawing(symbol: str, drawing_id: int):
    current = store.drawing(drawing_id)
    if not current or current["symbol"] != symbol:
        raise HTTPException(status_code=404, detail="绘图不存在")
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
    gaps = [
        {"start_date": item.get("date", item.get("start_date")),
         "end_date": item.get("date", item.get("end_date"))}
        for item in coverage.get("incomplete_days", [])
    ]
    gaps.extend({"start_date": item, "end_date": item} for item in coverage.get("missing_sessions", []))
    return {
        "symbol": symbol, "timeframe": timeframe, "adjustflag": adjustflag,
        **coverage, "gap_tasks": [item for item in gaps if item["start_date"] and item["end_date"]],
    }


@app.post("/api/market-data/{symbol}/repair")
async def repair_market_data(symbol: str, timeframe: str = "5", adjustflag: str = "2"):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {timeframe}")
    result = await sync_service.sync_one(symbol, timeframe, "incremental", adjustflag=adjustflag)
    if result and result.get("result") == "failed":
        raise HTTPException(status_code=502, detail=result["error"])
    return market_coverage(symbol, timeframe, adjustflag)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    target = ROOT / "data" / Path(file.filename).name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(await file.read())
    return {"path": str(target), "filename": target.name}


@app.post("/api/market-data/import")
async def import_market_data(
    file: UploadFile = File(...), symbol: str = "", timeframe: str = "5", adjustflag: str = "2",
):
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(status_code=400, detail="symbol必须是6位数字")
    target = ROOT / "data" / "imports" / Path(file.filename).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
    try:
        rows = read_csv(str(target), symbol)
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"不支持的周期: {timeframe}")
        coverage = validate_5m_coverage(rows) if timeframe == "5" else validate_coverage(rows, timeframe)
        count, changed_from = store.upsert_bars_with_changes(
            symbol, timeframe, adjustflag, rows, source="csv",
        )
        snapshot = None
        if timeframe in STRUCTURE_TIMEFRAMES:
            snapshot = period_structure_service.ensure(
                symbol, timeframe, adjustflag, bool(changed_from),
            )
        return {
            "symbol": symbol,
            "imported": count,
            "coverage": coverage,
            "structure_available": bool(snapshot and snapshot["structure"].get("pens")),
            "structure_version": snapshot["meta"].get("structure_version") if snapshot else None,
            "conflicts": len(coverage["duplicates"]),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def demo_rows():
    rows = []
    price = 100.0
    for index in range(90):
        drift = 0.7 if index < 20 else (-0.8 if index < 42 else (0.55 if index < 68 else -0.15))
        price += drift + ((index * 17) % 7 - 3) * 0.18
        rows.append({
            "trade_date": f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}",
            "open": price - 0.35, "high": price + 0.8, "low": price - 0.9,
            "close": price, "volume": 100000 + index * 500,
        })
    return rows
