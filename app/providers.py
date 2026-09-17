from __future__ import annotations

import csv
import asyncio
import os
import re
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

TIMEFRAMES = ("1", "5", "15", "30", "60", "120", "d", "w", "m", "y")
INTRADAY_TIMEFRAMES = {"1", "5", "15", "30", "60", "120"}
OFFICIAL_START = {**{key: "2020-01-03" for key in INTRADAY_TIMEFRAMES}, "d": "1990-12-19", "w": "1990-12-19", "m": "1990-12-19", "y": "1990-12-19"}
SUPPLEMENTAL_SECURITIES = {
    # BaoStock's stock catalogue omits a number of exchange index instruments.
    # Keep these in a separate, explicit index catalogue; they are never
    # confused with the six-digit SZ stock identifiers.
    "sh.000001": {"market_code": "sh.000001", "symbol": "1A0001", "name": "上证指数（上证综合指数）", "market": "SH", "trade_status": "1", "provider": "tencent"},
    "sh.000016": {"market_code": "sh.000016", "symbol": "1A0016", "name": "上证50指数", "market": "SH", "trade_status": "1", "provider": "tencent"},
    "sh.000300": {"market_code": "sh.000300", "symbol": "1A0300", "name": "沪深300指数", "market": "SH", "trade_status": "1", "provider": "tencent"},
    "sh.000688": {
        "market_code": "sh.000688", "symbol": "1A0688", "name": "科创50指数",
        "market": "SH", "trade_status": "1", "provider": "tencent",
    },
    "sh.000680": {"market_code": "sh.000680", "symbol": "1A0680", "name": "科创综指（上证科创板综合指数）", "market": "SH", "trade_status": "1", "provider": "tencent"},
    "sh.000852": {"market_code": "sh.000852", "symbol": "1A0852", "name": "中证1000指数", "market": "SH", "trade_status": "1", "provider": "tencent"},
    "sz.399001": {"market_code": "sz.399001", "symbol": "399001", "name": "深圳成指（深证成份指数）", "market": "SZ", "trade_status": "1", "provider": "tencent"},
    "sz.399006": {"market_code": "sz.399006", "symbol": "399006", "name": "创业板指数（创业板指）", "market": "SZ", "trade_status": "1", "provider": "tencent"},
    "sz.399673": {"market_code": "sz.399673", "symbol": "399673", "name": "创业板50指数", "market": "SZ", "trade_status": "1", "provider": "tencent"},
}


def normalize_security_symbol(symbol: str) -> str:
    """Normalize the app-facing identifier without losing exchange identity."""
    value = symbol.strip().upper()
    if re.fullmatch(r"1A\d{4}", value):
        return value
    if re.fullmatch(r"\d{6}", value):
        return value
    raise ValueError("证券代码必须是 6 位数字或 1A 加 4 位数字")


def catalog_symbol(market_code: str) -> str:
    """Use TongdaXin-style aliases for Shanghai indices that clash with SZ codes."""
    market, separator, code = market_code.strip().lower().partition(".")
    if not separator or market not in {"sh", "sz", "bj"} or not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别证券代码: {market_code}")
    if market == "sh" and code.startswith("000"):
        return f"1A{code[-4:]}"
    return code


def normalize_baostock_symbol(symbol: str) -> str:
    raw = symbol.strip().lower()
    if re.fullmatch(r"(?:sh|sz|bj)\.\d{6}", raw):
        return raw
    app_symbol = normalize_security_symbol(symbol)
    if app_symbol.startswith("1A"):
        return f"sh.00{app_symbol[2:]}"
    code = app_symbol
    if not code.isdigit() or len(code) != 6:
        raise ValueError("证券代码必须是 6 位数字")
    if code.startswith(("5", "6")):
        market = "sh"
    elif code.startswith(("0", "1", "2", "3")):
        market = "sz"
    elif code.startswith(("4", "8")):
        market = "bj"
    else:
        raise ValueError(f"无法识别股票市场: {symbol}")
    return f"{market}.{code}"


def fetch_tencent(symbol: str, timeframe: str, start_date: str = "2015-01-01",
                  end_date: str = "", adjustflag: str = "2") -> list[dict[str, Any]]:
    """Fetch indexes absent from BaoStock without inventing missing minute history."""
    market_code = normalize_baostock_symbol(symbol)
    secid = market_code.replace(".", "")
    end_date = end_date or date.today().isoformat()
    rows: list[list[Any]] = []
    if timeframe in INTRADAY_TIMEFRAMES:
        key = f"m{timeframe}"
        response = httpx.get(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            params={"param": f"{secid},{key},,320"}, timeout=15,
        )
        response.raise_for_status()
        rows = response.json().get("data", {}).get(secid, {}).get(key, [])
    else:
        key = {"d": "day", "w": "week", "m": "month", "y": "year"}[timeframe]
        cursor_end = end_date
        seen_dates: set[str] = set()
        for _ in range(30):
            response = httpx.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{secid},{key},{start_date},{cursor_end},640,qfq"}, timeout=15,
            )
            response.raise_for_status()
            payload = response.json().get("data", {}).get(secid, {})
            batch = payload.get(f"qfq{key}") or payload.get(key) or []
            fresh = [raw for raw in batch if str(raw[0]) not in seen_dates]
            if not fresh:
                break
            rows.extend(fresh)
            seen_dates.update(str(raw[0]) for raw in fresh)
            oldest = min(str(raw[0]) for raw in fresh)
            if oldest <= start_date or len(batch) < 640:
                break
            cursor_end = (datetime.fromisoformat(oldest).date() - timedelta(days=1)).isoformat()
        rows.sort(key=lambda raw: str(raw[0]))
    parsed = []
    for raw in rows:
        stamp = str(raw[0])
        if timeframe in INTRADAY_TIMEFRAMES:
            stamp = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]}:00"
        if stamp[:10] < start_date or stamp[:10] > end_date:
            continue
        parsed.append({
            "trade_date": stamp, "open": float(raw[1]), "high": float(raw[3]),
            "low": float(raw[4]), "close": float(raw[2]), "volume": float(raw[5] or 0),
            "amount": 0.0, "adjustflag": adjustflag, "source": "tencent",
        })
    if not parsed:
        raise RuntimeError(f"腾讯行情返回空数据: {symbol} {timeframe} {start_date}~{end_date}")
    revision = f"tencent:{secid}:{timeframe}:{parsed[-1]['trade_date']}"
    for item in parsed:
        item["source_revision"] = revision
    return parsed


def fetch_tencent_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    """Fetch one lightweight quote snapshot for each application symbol."""
    if not symbols:
        return []
    mappings = []
    result = []
    for symbol in dict.fromkeys(symbols):
        try:
            mappings.append((symbol, normalize_baostock_symbol(symbol).replace(".", "")))
        except ValueError:
            result.append({"symbol": symbol, "status": "unavailable", "source": "tencent", "error": "不支持的证券代码"})
    if not mappings:
        return result
    response = httpx.get(
        "https://qt.gtimg.cn/q=" + ",".join(provider_symbol for _, provider_symbol in mappings),
        timeout=15,
    )
    response.raise_for_status()
    payload = response.content.decode("gb18030", errors="replace")
    for symbol, provider_symbol in mappings:
        match = re.search(rf'v_{re.escape(provider_symbol)}="([^"]*)"', payload)
        fields = match.group(1).split("~") if match else []
        try:
            if len(fields) < 33 or fields[2] != provider_symbol[2:]:
                raise ValueError("证券不匹配")
            latest = float(fields[3]) if fields[3] else None
            previous_close = float(fields[4]) if fields[4] else None
            open_price = float(fields[5]) if len(fields) > 5 and fields[5] else latest
            high = float(fields[33]) if len(fields) > 33 and fields[33] else latest
            low = float(fields[34]) if len(fields) > 34 and fields[34] else latest
            volume = float(fields[36]) if len(fields) > 36 and fields[36] else 0.0
            amount = float(fields[37]) if len(fields) > 37 and fields[37] else None
            change = float(fields[31]) if len(fields) > 31 and fields[31] else None
            change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else None
            quote_time = fields[30] if len(fields) > 30 and fields[30] else None
            if latest is None or latest <= 0 or not math.isfinite(latest):
                raise ValueError("无有效成交价")
            previous_close = previous_close if previous_close and previous_close > 0 and math.isfinite(previous_close) else None
            change = change if change is not None and math.isfinite(change) else None
            change_pct = change_pct if change_pct is not None and math.isfinite(change_pct) else None
            if not all(math.isfinite(value) and value > 0 for value in (open_price, high, low)):
                raise ValueError("无效盘口价格")
            if volume < 0 or not math.isfinite(volume) or amount is not None and (amount < 0 or not math.isfinite(amount)):
                raise ValueError("无效成交数据")
            if previous_close is None:
                change = change_pct = None
        except (IndexError, TypeError, ValueError):
            latest = previous_close = change = change_pct = None
            open_price = high = low = volume = amount = None
            quote_time = None
        status = "success" if latest is not None else "unavailable"
        result.append({"symbol": symbol, "latest": latest, "previous_close": previous_close,
                       "change": change, "change_pct": change_pct, "quote_time": quote_time,
                       "open": open_price, "high": high, "low": low, "volume": volume, "amount": amount,
                       "source": "tencent", "status": status,
                       "error": None if status == "success" else "行情源未返回有效报价"})
    return result


def fetch_baostock(symbol: str, timeframe: str, start_date: str = "2015-01-01", end_date: str = "", adjustflag: str = "2") -> list[dict[str, Any]]:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"不支持的周期: {timeframe}")
    import baostock as bs

    code = normalize_baostock_symbol(symbol)
    end_date = end_date or "2099-12-31"
    fields = "date,time,code,open,high,low,close,volume,amount,adjustflag" if timeframe in INTRADAY_TIMEFRAMES else "date,code,open,high,low,close,volume,amount,adjustflag"
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        result = bs.query_history_k_data_plus(code, fields=fields, start_date=start_date, end_date=end_date,
                                              frequency=timeframe, adjustflag=adjustflag)
        if result.error_code != "0":
            # BaoStock does not provide reliable intraday/yearly data for the
            # supplemental index catalogue. Use Tencent for those instruments
            # before surfacing a sync failure.
            if code in SUPPLEMENTAL_SECURITIES:
                return fetch_tencent(symbol, timeframe, start_date, end_date, adjustflag)
            raise RuntimeError(f"BaoStock 查询失败: {result.error_code} {result.error_msg}")
        rows = []
        while result.next():
            raw = dict(zip(result.fields, result.get_row_data()))
            stamp = raw["date"]
            if timeframe in INTRADAY_TIMEFRAMES and raw.get("time"):
                stamp = f"{raw['date']} {raw['time'][8:10]}:{raw['time'][10:12]}:{raw['time'][12:14]}"
            rows.append({"trade_date": stamp, "open": float(raw["open"]), "high": float(raw["high"]),
                         "low": float(raw["low"]), "close": float(raw["close"]),
                         "volume": float(raw.get("volume") or 0), "amount": float(raw.get("amount") or 0),
                         "adjustflag": adjustflag})
        # BaoStock does not expose intraday bars for many Shanghai indices
        # (for example sh.000002). Fall back for the whole Shanghai index
        # family, while retaining BaoStock as the preferred source.
        if not rows and (code in SUPPLEMENTAL_SECURITIES or (timeframe in {"5", "30", "d", "w", "m"} and code.startswith("sh.000"))):
            return fetch_tencent(symbol, timeframe, start_date, end_date, adjustflag)
        if not rows:
            raise RuntimeError(f"BaoStock 返回空数据: {symbol} {timeframe} {start_date}~{end_date}")
        return rows
    finally:
        bs.logout()


def fetch_security_profile(symbol: str) -> dict[str, Any] | None:
    code = normalize_baostock_symbol(symbol)
    if code in SUPPLEMENTAL_SECURITIES:
        return dict(SUPPLEMENTAL_SECURITIES[code])
    import baostock as bs
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        result = bs.query_stock_basic(code=code)
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock 查询证券资料失败: {result.error_code} {result.error_msg}")
        if result.next():
            row = dict(zip(result.fields, result.get_row_data()))
            market_code = row.get("code", code)
            if market_code != code:
                return None
            return {"market_code": market_code, "symbol": catalog_symbol(market_code),
                    "name": row.get("code_name", ""), "market": market_code[:2].upper(),
                    "trade_status": row.get("status", "1")}
        return None
    finally:
        bs.logout()


def fetch_stock_name(symbol: str) -> str:
    profile = fetch_security_profile(symbol)
    return profile["name"] if profile else ""


def fetch_security_catalog(reference_date: str | None = None) -> dict[str, Any]:
    """Return the latest BaoStock catalog supported by the app's bare-code routing."""
    import baostock as bs

    cursor = date.fromisoformat(reference_date) if reference_date else date.today()
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        for offset in range(15):
            catalog_date = (cursor - timedelta(days=offset)).isoformat()
            result = bs.query_all_stock(day=catalog_date)
            if result.error_code != "0":
                continue
            items = []
            while result.next():
                row = dict(zip(result.fields, result.get_row_data()))
                market_code = row.get("code", "").lower()
                try:
                    symbol = catalog_symbol(market_code)
                    if normalize_baostock_symbol(symbol) != market_code:
                        continue
                except ValueError:
                    continue
                items.append({"market_code": market_code, "symbol": symbol,
                              "name": row.get("code_name", ""),
                              "market": market_code[:2].upper(),
                              "trade_status": row.get("tradeStatus", "")})
            existing = {item["market_code"] for item in items}
            items.extend(dict(item) for code, item in SUPPLEMENTAL_SECURITIES.items() if code not in existing)
            if len(items) >= 3000:
                return {"catalog_date": catalog_date, "items": items}
        raise RuntimeError("BaoStock 最近15日没有返回完整证券目录")
    finally:
        bs.logout()


def is_trading_day(date: str) -> bool:
    import baostock as bs
    login = bs.login()
    if login.error_code != "0":
        return False
    try:
        result = bs.query_trade_dates(start_date=date, end_date=date)
        if result.error_code == "0" and result.next():
            return dict(zip(result.fields, result.get_row_data())).get("is_trading_day") == "1"
        return False
    finally:
        bs.logout()


def fetch_trade_calendar(start_date: str, end_date: str) -> list[dict[str, Any]]:
    import baostock as bs
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        result = bs.query_trade_dates(start_date=start_date, end_date=end_date)
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock 交易日历查询失败: {result.error_code} {result.error_msg}")
        rows = []
        while result.next():
            raw = dict(zip(result.fields, result.get_row_data()))
            rows.append({"trade_date": raw["calendar_date"],
                         "is_trading_day": raw["is_trading_day"] == "1"})
        return rows
    finally:
        bs.logout()


def read_csv(path: str, symbol: str) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        rows = [{k: (float(v) if k in {"open", "high", "low", "close", "volume", "amount", "adjust_factor", "limit_up", "limit_down"} and v else v) for k, v in row.items()} for row in csv.DictReader(f)]
    required = {"trade_date", "open", "high", "low", "close", "volume"}
    if rows and not required.issubset(rows[0]):
        raise ValueError(f"CSV缺少字段: {', '.join(sorted(required - set(rows[0])))}")
    for row in rows:
        if row.get("symbol") and str(row["symbol"]).zfill(6) != symbol:
            raise ValueError(f"CSV包含其他股票: {row['symbol']}")
        row["source"] = "csv"
    return rows


async def fetch_api(url: str, symbol: str, start_date: str = "", end_date: str = "", adjustflag: str = "2") -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params={"symbol": symbol, "timeframe": "5", "start_date": start_date,
                                                  "end_date": end_date, "adjustflag": adjustflag})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            data = {"data": data}
        rows = data.get("data", data.get("rows", []))
        snapshot_id = str(data.get("snapshot_id", ""))
        for row in rows:
            row["source"] = "api"
            row["snapshot_id"] = snapshot_id
        return {"rows": rows, "snapshot_id": snapshot_id, "coverage_start": data.get("coverage_start"),
                "coverage_end": data.get("coverage_end"), "suspended_dates": data.get("suspended_dates", [])}


class MarketDataProvider:
    """Unified authoritative 5-minute feed with BaoStock as a fallback."""

    def __init__(self, api_url: str | None = None):
        self.api_url = api_url or os.getenv("MARKET_DATA_API_URL", "").strip()

    async def fetch_5m(self, symbol: str, start_date: str, end_date: str, adjustflag: str = "2") -> dict[str, Any]:
        if self.api_url:
            return await fetch_api(self.api_url, symbol, start_date, end_date, adjustflag)
        rows = await asyncio.to_thread(fetch_baostock, symbol, "5", start_date, end_date, adjustflag)
        for row in rows:
            row["source"] = "baostock"
        return {"rows": rows, "snapshot_id": "", "coverage_start": rows[0]["trade_date"] if rows else None,
                "coverage_end": rows[-1]["trade_date"] if rows else None, "suspended_dates": []}
