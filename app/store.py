from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str = "data/chant_agent.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self):
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("CREATE TABLE IF NOT EXISTS analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, as_of TEXT, data_version TEXT, payload TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS journals (id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id INTEGER, action TEXT, note TEXT, created_at TEXT)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS market_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                trade_date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL NOT NULL, amount REAL DEFAULT 0, adjustflag TEXT NOT NULL,
                UNIQUE(symbol,timeframe,trade_date,adjustflag))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS stock_pool (
                symbol TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS watchlist_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS watchlist_group_members (
                group_id INTEGER NOT NULL, symbol TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, PRIMARY KEY(group_id,symbol))""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_group_order ON watchlist_groups(sort_order,id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_member_symbol ON watchlist_group_members(symbol,group_id)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS security_catalog (
                market_code TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
                market TEXT NOT NULL, trade_status TEXT NOT NULL DEFAULT '',
                catalog_date TEXT NOT NULL, refreshed_at TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_security_catalog_symbol ON security_catalog(symbol)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_security_catalog_name ON security_catalog(name)")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(stock_pool)")}
            if "sort_order" not in columns:
                self.db.execute("ALTER TABLE stock_pool ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            self.db.execute("""CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                mode TEXT NOT NULL, scheduled_for TEXT, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, rows_received INTEGER NOT NULL DEFAULT 0,
                range_start TEXT, range_end TEXT, error TEXT NOT NULL DEFAULT '')""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_market_bars_lookup ON market_bars(symbol,timeframe,adjustflag,trade_date)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS structure_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                adjustflag TEXT NOT NULL, structure_type TEXT NOT NULL, target_id TEXT,
                operation TEXT NOT NULL, payload TEXT NOT NULL, base_run_id INTEGER,
                base_structure_version TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_structure_overrides_lookup ON structure_overrides(symbol,timeframe,adjustflag,status,id DESC)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS structure_override_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, override_id INTEGER NOT NULL, action TEXT NOT NULL,
                before_payload TEXT NOT NULL DEFAULT '', after_payload TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS drawing_objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                object_type TEXT NOT NULL, start_anchor TEXT NOT NULL, end_anchor TEXT NOT NULL,
                style TEXT NOT NULL DEFAULT '{}', label TEXT NOT NULL DEFAULT '', visible INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_drawings_lookup ON drawing_objects(symbol,timeframe,deleted_at,id)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS trade_calendar (
                exchange TEXT NOT NULL, trade_date TEXT NOT NULL, is_trading_day INTEGER NOT NULL,
                session_type TEXT NOT NULL DEFAULT 'full', expected_5m_count INTEGER,
                expected_30m_count INTEGER, PRIMARY KEY(exchange, trade_date))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS market_coverage (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, adjustflag TEXT NOT NULL,
                coverage_version TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(symbol,timeframe,adjustflag))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS market_gap_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                adjustflag TEXT NOT NULL, gap_start TEXT NOT NULL, gap_end TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                UNIQUE(symbol,timeframe,adjustflag,gap_start,gap_end))""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_lookup ON sync_runs(symbol,timeframe,id DESC)")
            market_columns = {row[1] for row in self.db.execute("PRAGMA table_info(market_bars)")}
            for name, definition in {
                "source": "TEXT NOT NULL DEFAULT 'baostock'",
                "snapshot_id": "TEXT NOT NULL DEFAULT ''",
                "source_revision": "TEXT NOT NULL DEFAULT ''",
                "adjust_factor": "REAL NOT NULL DEFAULT 1",
                "is_suspended": "INTEGER NOT NULL DEFAULT 0",
                "limit_up": "REAL", "limit_down": "REAL",
            }.items():
                if name not in market_columns:
                    self.db.execute(f"ALTER TABLE market_bars ADD COLUMN {name} {definition}")
            self.db.execute("""CREATE TABLE IF NOT EXISTS market_data_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                trade_date TEXT NOT NULL, adjustflag TEXT NOT NULL, old_source TEXT NOT NULL,
                new_source TEXT NOT NULL, old_payload TEXT NOT NULL, new_payload TEXT NOT NULL,
                created_at TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS period_structure_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                adjustflag TEXT NOT NULL, definition_version TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, market_version TEXT NOT NULL,
                coverage_version TEXT NOT NULL DEFAULT '', structure_version TEXT NOT NULL DEFAULT '',
                calculator_fingerprint TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '')""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_period_runs_lookup ON period_structure_runs(symbol,timeframe,adjustflag,id DESC)")
            for table in ("period_processed_bars", "period_fractals", "period_pens", "period_pen_centers", "period_center_relations", "period_movements"):
                self.db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    run_id INTEGER NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                    adjustflag TEXT NOT NULL, definition_version TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,ordinal))""")
                self.db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_range ON {table}(run_id,start_date,end_date)")
            run_columns = {row[1] for row in self.db.execute("PRAGMA table_info(period_structure_runs)")}
            if "calculator_fingerprint" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN calculator_fingerprint TEXT NOT NULL DEFAULT ''")
            if "movement_count" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN movement_count INTEGER NOT NULL DEFAULT 0")
            if "movement_input_hash" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN movement_input_hash TEXT NOT NULL DEFAULT ''")
            if "center_level_counts" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN center_level_counts TEXT NOT NULL DEFAULT '{}'")
            if "movement_level_counts" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN movement_level_counts TEXT NOT NULL DEFAULT '{}'")
            if "hierarchy_input_hash" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN hierarchy_input_hash TEXT NOT NULL DEFAULT ''")
            if "decomposition_meta" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN decomposition_meta TEXT NOT NULL DEFAULT '{}'")
            if "max_confirmed_center_level" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN max_confirmed_center_level INTEGER NOT NULL DEFAULT 0")
            if "max_available_center_level" not in run_columns:
                self.db.execute("ALTER TABLE period_structure_runs ADD COLUMN max_available_center_level INTEGER NOT NULL DEFAULT 0")
            self.db.execute("""CREATE TABLE IF NOT EXISTS active_period_structure_runs (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, adjustflag TEXT NOT NULL,
                run_id INTEGER NOT NULL, activated_at TEXT NOT NULL,
                PRIMARY KEY(symbol,timeframe,adjustflag))""")
            self.db.commit()

    def list_stock_pool(self):
        with self._lock:
            rows = self.db.execute("SELECT symbol,name,enabled,sort_order,created_at,updated_at FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                latest = self.db.execute("""SELECT s.* FROM sync_runs s JOIN (
                    SELECT timeframe,MAX(id) id FROM sync_runs WHERE symbol=? GROUP BY timeframe
                ) x ON s.id=x.id ORDER BY s.id DESC""", (item["symbol"],)).fetchall()
                statuses = [run["status"] for run in latest]
                if "running" in statuses:
                    item["sync_status"] = "running"
                elif "failed" in statuses and "success" in statuses:
                    item["sync_status"] = "partial_failed"
                elif "failed" in statuses:
                    item["sync_status"] = "failed"
                elif statuses:
                    item["sync_status"] = "success"
                else:
                    item["sync_status"] = None
                successes = [run["finished_at"] for run in latest if run["status"] == "success" and run["finished_at"]]
                item["last_sync"] = max(successes) if successes else None
                item["sync_error"] = "；".join(run["error"] for run in latest if run["status"] == "failed" and run["error"])
                item["market"] = self.market_summary(item["symbol"], "2")
                result.append(item)
            return result

    def upsert_trade_calendar(self, rows: list[dict[str, Any]], exchange: str = "CN") -> int:
        values = [(exchange, row["trade_date"], int(bool(row["is_trading_day"])),
                   row.get("session_type", "full"), row.get("expected_5m_count", 48),
                   row.get("expected_30m_count", 8)) for row in rows]
        with self._lock:
            self.db.executemany("""INSERT INTO trade_calendar
                (exchange,trade_date,is_trading_day,session_type,expected_5m_count,expected_30m_count)
                VALUES(?,?,?,?,?,?) ON CONFLICT(exchange,trade_date) DO UPDATE SET
                is_trading_day=excluded.is_trading_day,session_type=excluded.session_type,
                expected_5m_count=excluded.expected_5m_count,expected_30m_count=excluded.expected_30m_count""", values)
            self.db.commit()
        return len(values)

    def trading_dates(self, start_date: str, end_date: str, exchange: str = "CN") -> list[str]:
        with self._lock:
            rows = self.db.execute("""SELECT trade_date FROM trade_calendar
                WHERE exchange=? AND is_trading_day=1 AND trade_date BETWEEN ? AND ? ORDER BY trade_date""",
                (exchange, start_date[:10], end_date[:10])).fetchall()
        return [row[0] for row in rows]

    def trade_calendar_range(self, exchange: str = "CN") -> tuple[str | None, str | None]:
        with self._lock:
            row = self.db.execute("SELECT MIN(trade_date),MAX(trade_date) FROM trade_calendar WHERE exchange=?",
                                  (exchange,)).fetchone()
        return row[0], row[1]

    def upsert_stock(self, symbol: str, name: str = "", group_id: int | None = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                if group_id is not None and not self.db.execute(
                    "SELECT 1 FROM watchlist_groups WHERE id=?", (group_id,)
                ).fetchone():
                    raise LookupError("自选分组不存在")
                existing = self.db.execute("SELECT name FROM stock_pool WHERE symbol=?", (symbol,)).fetchone()
                if existing:
                    self.db.execute("UPDATE stock_pool SET name=?,enabled=1,updated_at=? WHERE symbol=?", (name or existing["name"], now, symbol))
                else:
                    order = self.db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM stock_pool").fetchone()[0]
                    self.db.execute("INSERT INTO stock_pool(symbol,name,enabled,sort_order,created_at,updated_at) VALUES(?,?,1,?,?,?)", (symbol, name, order, now, now))
                if group_id is not None:
                    member_order = self.db.execute(
                        "SELECT COALESCE(MAX(sort_order),-1)+1 FROM watchlist_group_members WHERE group_id=?",
                        (group_id,),
                    ).fetchone()[0]
                    self.db.execute("""INSERT OR IGNORE INTO watchlist_group_members
                        (group_id,symbol,sort_order,created_at) VALUES(?,?,?,?)""",
                        (group_id, symbol, member_order, now))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return next(item for item in self.list_stock_pool() if item["symbol"] == symbol)

    @staticmethod
    def _watchlist_group_name(name: str) -> str:
        value = name.strip()
        if not value:
            raise ValueError("分组名称不能为空")
        if len(value) > 30:
            raise ValueError("分组名称不能超过30个字符")
        return value

    def list_watchlist_groups(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("""SELECT g.id,g.name,g.sort_order,g.created_at,g.updated_at,
                COUNT(p.symbol) member_count
                FROM watchlist_groups g
                LEFT JOIN watchlist_group_members m ON m.group_id=g.id
                LEFT JOIN stock_pool p ON p.symbol=m.symbol AND p.enabled=1
                GROUP BY g.id ORDER BY g.sort_order,g.id""").fetchall()
        return [dict(row) for row in rows]

    def watchlist_memberships(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("""SELECT m.group_id,m.symbol,m.sort_order
                FROM watchlist_group_members m
                JOIN watchlist_groups g ON g.id=m.group_id
                JOIN stock_pool p ON p.symbol=m.symbol AND p.enabled=1
                ORDER BY g.sort_order,g.id,m.sort_order,m.symbol""").fetchall()
        return [dict(row) for row in rows]

    def watchlist(self) -> dict[str, Any]:
        return {
            "stocks": self.list_stock_pool(),
            "groups": self.list_watchlist_groups(),
            "memberships": self.watchlist_memberships(),
        }

    def create_watchlist_group(self, name: str) -> dict[str, Any]:
        value = self._watchlist_group_name(name)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                order = self.db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM watchlist_groups").fetchone()[0]
                cur = self.db.execute(
                    "INSERT INTO watchlist_groups(name,sort_order,created_at,updated_at) VALUES(?,?,?,?)",
                    (value, order, now, now),
                )
                self.db.commit()
            except sqlite3.IntegrityError as exc:
                self.db.rollback()
                raise ValueError("分组名称已存在") from exc
        return next(group for group in self.list_watchlist_groups() if group["id"] == cur.lastrowid)

    def update_watchlist_group(self, group_id: int, name: str) -> dict[str, Any] | None:
        value = self._watchlist_group_name(name)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                cur = self.db.execute(
                    "UPDATE watchlist_groups SET name=?,updated_at=? WHERE id=?", (value, now, group_id)
                )
                self.db.commit()
            except sqlite3.IntegrityError as exc:
                self.db.rollback()
                raise ValueError("分组名称已存在") from exc
        if not cur.rowcount:
            return None
        return next(group for group in self.list_watchlist_groups() if group["id"] == group_id)

    def delete_watchlist_group(self, group_id: int) -> bool:
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                exists = self.db.execute("SELECT 1 FROM watchlist_groups WHERE id=?", (group_id,)).fetchone()
                if not exists:
                    self.db.rollback()
                    return False
                self.db.execute("DELETE FROM watchlist_group_members WHERE group_id=?", (group_id,))
                self.db.execute("DELETE FROM watchlist_groups WHERE id=?", (group_id,))
                self.db.commit()
                return True
            except Exception:
                self.db.rollback()
                raise

    def reorder_watchlist_groups(self, group_ids: list[int]) -> list[dict[str, Any]]:
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("分组排序包含重复分组")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            current = [row[0] for row in self.db.execute(
                "SELECT id FROM watchlist_groups ORDER BY sort_order,id"
            ).fetchall()]
            if set(current) != set(group_ids) or len(current) != len(group_ids):
                raise ValueError("分组列表已变化，请刷新后重试")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.executemany(
                    "UPDATE watchlist_groups SET sort_order=?,updated_at=? WHERE id=?",
                    [(index, now, group_id) for index, group_id in enumerate(group_ids)],
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return self.list_watchlist_groups()

    def add_watchlist_group_member(self, group_id: int, symbol: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                if not self.db.execute("SELECT 1 FROM watchlist_groups WHERE id=?", (group_id,)).fetchone():
                    raise LookupError("自选分组不存在")
                if not self.db.execute(
                    "SELECT 1 FROM stock_pool WHERE symbol=? AND enabled=1", (symbol,)
                ).fetchone():
                    raise LookupError("股票不在股票池中")
                order = self.db.execute(
                    "SELECT COALESCE(MAX(sort_order),-1)+1 FROM watchlist_group_members WHERE group_id=?",
                    (group_id,),
                ).fetchone()[0]
                self.db.execute("""INSERT OR IGNORE INTO watchlist_group_members
                    (group_id,symbol,sort_order,created_at) VALUES(?,?,?,?)""",
                    (group_id, symbol, order, now))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return next(item for item in self.watchlist_memberships()
                    if item["group_id"] == group_id and item["symbol"] == symbol)

    def remove_watchlist_group_member(self, group_id: int, symbol: str) -> bool:
        with self._lock:
            if not self.db.execute("SELECT 1 FROM watchlist_groups WHERE id=?", (group_id,)).fetchone():
                raise LookupError("自选分组不存在")
            cur = self.db.execute(
                "DELETE FROM watchlist_group_members WHERE group_id=? AND symbol=?", (group_id, symbol)
            )
            self.db.commit()
        return cur.rowcount > 0

    def reorder_watchlist_group_members(self, group_id: int, symbols: list[str]) -> list[dict[str, Any]]:
        if len(symbols) != len(set(symbols)):
            raise ValueError("组内排序包含重复证券")
        with self._lock:
            if not self.db.execute("SELECT 1 FROM watchlist_groups WHERE id=?", (group_id,)).fetchone():
                raise LookupError("自选分组不存在")
            current = [row[0] for row in self.db.execute(
                "SELECT symbol FROM watchlist_group_members WHERE group_id=? ORDER BY sort_order,symbol",
                (group_id,),
            ).fetchall()]
            if set(current) != set(symbols) or len(current) != len(symbols):
                raise ValueError("分组成员已变化，请刷新后重试")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.executemany(
                    "UPDATE watchlist_group_members SET sort_order=? WHERE group_id=? AND symbol=?",
                    [(index, group_id, symbol) for index, symbol in enumerate(symbols)],
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return [item for item in self.watchlist_memberships() if item["group_id"] == group_id]

    def replace_security_catalog(self, items: list[dict[str, Any]], catalog_date: str):
        now = datetime.now(timezone.utc).isoformat()
        values = [(item["market_code"], item["symbol"], item["name"], item["market"],
                   item.get("trade_status", ""), catalog_date, now) for item in items]
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("DELETE FROM security_catalog")
                self.db.executemany("""INSERT INTO security_catalog
                    (market_code,symbol,name,market,trade_status,catalog_date,refreshed_at)
                    VALUES(?,?,?,?,?,?,?)""", values)
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def upsert_security_catalog(self, item: dict[str, Any], catalog_date: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.db.execute("""INSERT INTO security_catalog
                (market_code,symbol,name,market,trade_status,catalog_date,refreshed_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(market_code) DO UPDATE SET
                symbol=excluded.symbol,name=excluded.name,market=excluded.market,
                trade_status=excluded.trade_status,catalog_date=excluded.catalog_date,
                refreshed_at=excluded.refreshed_at""",
                (item["market_code"], item["symbol"], item["name"], item["market"],
                 item.get("trade_status", ""), catalog_date, now))
            self.db.commit()

    def security_catalog_meta(self) -> dict[str, Any]:
        with self._lock:
            row = self.db.execute("""SELECT COUNT(*) count,MAX(catalog_date) catalog_date,
                MAX(refreshed_at) refreshed_at FROM security_catalog""").fetchone()
        return {"catalog_date": row["catalog_date"], "refreshed_at": row["refreshed_at"],
                "count": row["count"]}

    def search_security_catalog(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        prefix = f"{query}%"
        with self._lock:
            rows = self.db.execute("""SELECT c.*,
                CASE WHEN p.symbol IS NULL THEN 0 ELSE 1 END selected
                FROM security_catalog c LEFT JOIN stock_pool p
                  ON p.symbol=c.symbol AND p.enabled=1
                WHERE c.symbol LIKE ? OR c.market_code LIKE ? OR c.name LIKE ?
                ORDER BY CASE WHEN c.symbol=? THEN 0 WHEN c.symbol LIKE ? THEN 1
                              WHEN c.market_code LIKE ? THEN 2 WHEN c.name LIKE ? THEN 3 ELSE 4 END,
                         c.trade_status DESC,c.symbol LIMIT ?""",
                (pattern, pattern, pattern, query, prefix, prefix, prefix, limit)).fetchall()
        return [{**dict(row), "selected": bool(row["selected"])} for row in rows]

    def security_catalog_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM security_catalog WHERE symbol=? LIMIT 1", (symbol,)).fetchone()
        return dict(row) if row else None

    def update_stock(self, symbol: str, name: str | None = None, move: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            current = self.db.execute("SELECT * FROM stock_pool WHERE symbol=? AND enabled=1", (symbol,)).fetchone()
            if not current:
                return None
            if name is not None:
                self.db.execute("UPDATE stock_pool SET name=?,updated_at=? WHERE symbol=?", (name.strip(), now, symbol))
            if move in {"up", "down"}:
                operator, direction = ("<", "DESC") if move == "up" else (">", "ASC")
                other = self.db.execute(f"SELECT symbol,sort_order FROM stock_pool WHERE enabled=1 AND sort_order {operator} ? ORDER BY sort_order {direction} LIMIT 1", (current["sort_order"],)).fetchone()
                if other:
                    self.db.execute("UPDATE stock_pool SET sort_order=? WHERE symbol=?", (other["sort_order"], symbol))
                    self.db.execute("UPDATE stock_pool SET sort_order=? WHERE symbol=?", (current["sort_order"], other["symbol"]))
            self.db.commit()
        return next(item for item in self.list_stock_pool() if item["symbol"] == symbol)

    def reorder_stock_pool(self, symbols: list[str]):
        if len(symbols) != len(set(symbols)):
            raise ValueError("自选排序包含重复证券")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            current = [row[0] for row in self.db.execute(
                "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
            ).fetchall()]
            if set(current) != set(symbols) or len(current) != len(symbols):
                raise ValueError("自选列表已变化，请刷新后重试")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.executemany(
                    "UPDATE stock_pool SET sort_order=?,updated_at=? WHERE symbol=? AND enabled=1",
                    [(index, now, symbol) for index, symbol in enumerate(symbols)],
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return self.list_stock_pool()

    def delete_stock(self, symbol: str):
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("DELETE FROM watchlist_group_members WHERE symbol=?", (symbol,))
                cur = self.db.execute("DELETE FROM stock_pool WHERE symbol=?", (symbol,))
                self.db.commit()
                return cur.rowcount > 0
            except Exception:
                self.db.rollback()
                raise

    def upsert_bars(self, symbol: str, timeframe: str, adjustflag: str, rows: list[dict[str, Any]]) -> int:
        count, _ = self.upsert_bars_with_changes(symbol, timeframe, adjustflag, rows)
        return count

    def upsert_bars_with_changes(self, symbol: str, timeframe: str, adjustflag: str, rows: list[dict[str, Any]], source: str = "baostock", snapshot_id: str = "") -> tuple[int, str | None]:
        priority = {"baostock": 1, "api": 2, "csv": 3}
        values = [(symbol, timeframe, row["trade_date"], row["open"], row["high"], row["low"], row["close"], row.get("volume", 0), row.get("amount", 0), adjustflag,
                   row.get("source", source), row.get("snapshot_id", snapshot_id), row.get("source_revision", ""), row.get("adjust_factor", 1),
                   int(bool(row.get("is_suspended", False))), row.get("limit_up"), row.get("limit_down")) for row in rows]
        if not values:
            return 0, None
        with self._lock:
            start, end = min(row["trade_date"] for row in rows), max(row["trade_date"] for row in rows)
            existing_rows = self.db.execute("SELECT * FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=? AND trade_date BETWEEN ? AND ?", (symbol, timeframe, adjustflag, start, end)).fetchall()
            existing = {row["trade_date"]: dict(row) for row in existing_rows}
            accepted = []
            changed = []
            for row, value in zip(rows, values):
                old = existing.get(row["trade_date"])
                new_source = row.get("source", source)
                if old and priority.get(new_source, 0) < priority.get(old.get("source", "baostock"), 0):
                    continue
                new_prices = tuple(float(row.get(key, 0) or 0) for key in ("open", "high", "low", "close", "volume", "amount"))
                old_prices = tuple(old[key] for key in ("open", "high", "low", "close", "volume", "amount")) if old else None
                if old_prices != new_prices:
                    changed.append(row["trade_date"])
                    if old:
                        self.db.execute("""INSERT INTO market_data_conflicts(symbol,timeframe,trade_date,adjustflag,old_source,new_source,old_payload,new_payload,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?)""", (symbol, timeframe, row["trade_date"], adjustflag, old["source"], new_source,
                            json.dumps(old, ensure_ascii=False, default=str), json.dumps(row, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
                accepted.append(value)
            self.db.executemany("""INSERT INTO market_bars(symbol,timeframe,trade_date,open,high,low,close,volume,amount,adjustflag,source,snapshot_id,source_revision,adjust_factor,is_suspended,limit_up,limit_down)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,trade_date,adjustflag) DO UPDATE SET
                open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,amount=excluded.amount,
                source=excluded.source,snapshot_id=excluded.snapshot_id,source_revision=excluded.source_revision,adjust_factor=excluded.adjust_factor,
                is_suspended=excluded.is_suspended,limit_up=excluded.limit_up,limit_down=excluded.limit_down""", accepted)
            self.db.commit()
        return len(accepted), min(changed) if changed else None

    def market_bars(self, symbol: str, timeframe: str, adjustflag: str, start_date: str = "2015-01-01", end_date: str = "9999-12-31", limit: int | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [symbol, timeframe, adjustflag, start_date, end_date]
        fields = "trade_date,open,high,low,close,volume,amount,source,snapshot_id,source_revision,adjust_factor,is_suspended,limit_up,limit_down"
        sql = f"SELECT {fields} FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date"
        if limit:
            sql = f"SELECT * FROM (SELECT {fields} FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?) ORDER BY trade_date"
            params.append(limit)
        with self._lock:
            rows = self.db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def market_range(self, symbol: str, timeframe: str, adjustflag: str) -> tuple[str | None, str | None]:
        with self._lock:
            row = self.db.execute("SELECT MIN(trade_date),MAX(trade_date) FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=?", (symbol, timeframe, adjustflag)).fetchone()
        return (row[0], row[1]) if row and row[0] else (None, None)

    def retain_market_date(self, symbol: str, timeframe: str, adjustflag: str, trade_date: str):
        """Keep only one trading date for the lightweight intraday snapshot."""
        with self._lock:
            self.db.execute("DELETE FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=? AND trade_date<?", (symbol, timeframe, adjustflag, trade_date[:10]))
            self.db.commit()

    def market_page(self, symbol: str, timeframe: str, adjustflag: str, before: str | None, limit: int):
        params: list[Any] = [symbol, timeframe, adjustflag]
        condition = ""
        if before:
            condition = " AND trade_date<?"
            params.append(before)
        params.append(limit)
        sql = f"SELECT * FROM (SELECT trade_date,open,high,low,close,volume,amount FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=?{condition} ORDER BY trade_date DESC LIMIT ?) ORDER BY trade_date"
        with self._lock:
            rows = [dict(row) for row in self.db.execute(sql, params).fetchall()]
            has_more = False
            if rows:
                has_more = bool(self.db.execute("SELECT 1 FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=? AND trade_date<? LIMIT 1", (symbol, timeframe, adjustflag, rows[0]["trade_date"])).fetchone())
        return rows, has_more

    def market_summary(self, symbol: str, adjustflag: str):
        with self._lock:
            rows = self.db.execute("SELECT timeframe,MIN(trade_date) range_start,MAX(trade_date) range_end,COUNT(*) bar_count FROM market_bars WHERE symbol=? AND adjustflag=? GROUP BY timeframe", (symbol, adjustflag)).fetchall()
            daily = self.db.execute("SELECT close FROM market_bars WHERE symbol=? AND timeframe='d' AND adjustflag=? ORDER BY trade_date DESC LIMIT 2", (symbol, adjustflag)).fetchall()
        latest = daily[0][0] if daily else None
        change_pct = ((daily[0][0] / daily[1][0] - 1) * 100) if len(daily) > 1 and daily[1][0] else None
        return {"ranges": {row["timeframe"]: dict(row) for row in rows}, "latest": latest, "change_pct": change_pct}

    def save_market_coverage(self, symbol: str, timeframe: str, adjustflag: str, payload: dict[str, Any]):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        version = hashlib.sha256(raw.encode()).hexdigest()[:20]
        status = str(payload.get("status", "unknown"))
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.db.execute("""INSERT INTO market_coverage(symbol,timeframe,adjustflag,coverage_version,status,payload,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,adjustflag) DO UPDATE SET
                coverage_version=excluded.coverage_version,status=excluded.status,payload=excluded.payload,updated_at=excluded.updated_at""",
                (symbol, timeframe, adjustflag, version, status, raw, now))
            self.db.commit()
        return {"coverage_version": version, "status": status}

    def market_coverage(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        with self._lock:
            row = self.db.execute("SELECT * FROM market_coverage WHERE symbol=? AND timeframe=? AND adjustflag=?", (symbol, timeframe, adjustflag)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"] or "{}")
        return result

    def replace_gap_tasks(self, symbol: str, timeframe: str, adjustflag: str, gaps: list[dict[str, Any]]):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.db.execute("DELETE FROM market_gap_tasks WHERE symbol=? AND timeframe=? AND adjustflag=? AND status='pending'", (symbol, timeframe, adjustflag))
            self.db.executemany("""INSERT OR IGNORE INTO market_gap_tasks(symbol,timeframe,adjustflag,gap_start,gap_end,status,updated_at)
                VALUES(?,?,?,?,?,'pending',?)""", [(symbol, timeframe, adjustflag, g["start_date"], g["end_date"], now) for g in gaps])
            self.db.commit()

    def market_gap_tasks(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        with self._lock:
            rows = self.db.execute("SELECT * FROM market_gap_tasks WHERE symbol=? AND timeframe=? AND adjustflag=? ORDER BY gap_start", (symbol, timeframe, adjustflag)).fetchall()
        return [dict(row) for row in rows]

    def create_sync_run(self, symbol: str, timeframe: str, mode: str, scheduled_for: str | None):
        with self._lock:
            cur = self.db.execute("INSERT INTO sync_runs(symbol,timeframe,mode,scheduled_for,started_at,status) VALUES(?,?,?,?,?,'running')", (symbol, timeframe, mode, scheduled_for, datetime.now(timezone.utc).isoformat()))
            self.db.commit()
            return cur.lastrowid

    def finish_sync_run(self, run_id: int, status: str, rows_received: int = 0, range_start: str | None = None, range_end: str | None = None, error: str = ""):
        with self._lock:
            self.db.execute("UPDATE sync_runs SET finished_at=?,status=?,rows_received=?,range_start=?,range_end=?,error=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), status, rows_received, range_start, range_end, error[:1000], run_id))
            self.db.commit()

    def has_successful_sync(self, symbol: str, timeframe: str, scheduled_for: str) -> bool:
        with self._lock:
            return bool(self.db.execute("SELECT 1 FROM sync_runs WHERE symbol=? AND timeframe=? AND scheduled_for=? AND status='success' LIMIT 1", (symbol, timeframe, scheduled_for)).fetchone())

    def list_sync_runs(self, limit: int = 100):
        with self._lock:
            rows = self.db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def save(self, symbol: str, payload: dict[str, Any], as_of: str) -> dict[str, Any]:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        version = hashlib.sha256(raw.encode()).hexdigest()[:16]
        with self._lock:
            cur = self.db.execute("INSERT INTO analyses(symbol,as_of,data_version,payload) VALUES(?,?,?,?)", (symbol, as_of, version, raw))
            self.db.commit()
        return {"analysis_id": cur.lastrowid, "data_version": version, "as_of": as_of}

    def list(self, limit: int = 50):
        with self._lock:
            rows = self.db.execute("SELECT id,symbol,as_of,data_version,payload FROM analyses ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"analysis_id": row[0], "symbol": row[1], "as_of": row[2], "data_version": row[3], "payload": json.loads(row[4])} for row in rows]

    def journal(self, analysis_id: int, action: str, note: str, created_at: str):
        with self._lock:
            cur = self.db.execute("INSERT INTO journals(analysis_id,action,note,created_at) VALUES(?,?,?,?)", (analysis_id, action, note, created_at))
            self.db.commit()
        return {"journal_id": cur.lastrowid, "analysis_id": analysis_id, "action": action, "note": note, "created_at": created_at}

    def journals(self, limit: int = 100):
        with self._lock:
            rows = self.db.execute("SELECT id,analysis_id,action,note,created_at FROM journals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"journal_id": row[0], "analysis_id": row[1], "action": row[2], "note": row[3], "created_at": row[4]} for row in rows]

    def active_period_structure_run(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        with self._lock:
            row = self.db.execute("""SELECT r.* FROM active_period_structure_runs a
                JOIN period_structure_runs r ON r.id=a.run_id
                WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""", (symbol, timeframe, adjustflag)).fetchone()
        return dict(row) if row else None

    def highest_active_center_level(self, definition_version: str) -> int:
        with self._lock:
            rows = self.db.execute("""SELECT r.center_level_counts FROM active_period_structure_runs a
                JOIN period_structure_runs r ON r.id=a.run_id WHERE r.definition_version=?""",
                (definition_version,)).fetchall()
        levels = [int(level) for row in rows for level, count in json.loads(row[0] or "{}").items() if count]
        return max(levels, default=0)

    def replace_period_structure(self, symbol: str, timeframe: str, adjustflag: str,
                                 definition_version: str, result: dict[str, Any],
                                 market_version: str, coverage_version: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        mapping = (("period_processed_bars", "processed_bars"), ("period_fractals", "fractals"),
                   ("period_pens", "pens"), ("period_pen_centers", "centers"),
                   ("period_center_relations", "center_relations"),
                   ("period_movements", "movements"))
        centers = result.get("centers", result.get("pen_centers", []))
        center_level_counts: dict[str, int] = {}
        for center in centers:
            key = str(center.get("level", 1))
            center_level_counts[key] = center_level_counts.get(key, 0) + 1
        movement_count = len(result.get("movements", []))
        movement_level_counts: dict[str, int] = {}
        for movement in result.get("movements", []):
            key = f"L{movement.get('level', 1)}:{movement.get('role', 'same_level_decomposition')}"
            movement_level_counts[key] = movement_level_counts.get(key, 0) + 1
        decomposition = result.get("decomposition", {})
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                cur = self.db.execute("""INSERT INTO period_structure_runs
                    (symbol,timeframe,adjustflag,definition_version,started_at,status,market_version,coverage_version,calculator_fingerprint)
                    VALUES(?,?,?,?,?,'running',?,?,?)""", (
                        symbol, timeframe, adjustflag, definition_version, now,
                        market_version, coverage_version,
                        result.get("calculator_fingerprint", ""),
                    ))
                run_id = cur.lastrowid
                for table, key in mapping:
                    values = []
                    for ordinal, item in enumerate(result.get(key, [])):
                        start = item.get("start_date") or item.get("trade_date") or ""
                        end = item.get("end_date") or item.get("trade_date") or start
                        values.append((run_id,symbol,timeframe,adjustflag,definition_version,ordinal,start,end,json.dumps(item,ensure_ascii=False,sort_keys=True)))
                    if values:
                        self.db.executemany(f"INSERT INTO {table}(run_id,symbol,timeframe,adjustflag,definition_version,ordinal,start_date,end_date,payload) VALUES(?,?,?,?,?,?,?,?,?)", values)
                self.db.execute("""UPDATE period_structure_runs SET finished_at=?,status='success',
                    structure_version=?,center_level_counts=?,movement_count=?,movement_input_hash=?,
                    movement_level_counts=?,hierarchy_input_hash=?,decomposition_meta=?,
                    max_confirmed_center_level=?,max_available_center_level=? WHERE id=?""",
                    (datetime.now(timezone.utc).isoformat(), result.get("structure_version", ""),
                     json.dumps(center_level_counts, sort_keys=True), movement_count,
                     result.get("movement_input_hash", ""),
                     json.dumps(movement_level_counts, sort_keys=True),
                     result.get("hierarchy_input_hash", ""),
                     json.dumps(decomposition, ensure_ascii=False, sort_keys=True),
                     int(result.get("max_confirmed_center_level", 0)),
                     int(result.get("max_available_center_level", 0)), run_id))
                self.db.execute("""INSERT INTO active_period_structure_runs(symbol,timeframe,adjustflag,run_id,activated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(symbol,timeframe,adjustflag) DO UPDATE SET run_id=excluded.run_id,activated_at=excluded.activated_at""", (symbol,timeframe,adjustflag,run_id,datetime.now(timezone.utc).isoformat()))
                self.db.commit()
                return self.active_period_structure_run(symbol,timeframe,adjustflag)
            except Exception:
                self.db.rollback(); raise

    def period_rows(self, table: str, run_id: int, start_date: str | None = None, end_date: str | None = None):
        allowed = {"period_processed_bars", "period_fractals", "period_pens", "period_pen_centers", "period_center_relations", "period_movements"}
        if table not in allowed: raise ValueError("invalid period table")
        params: list[Any] = [run_id]; sql = f"SELECT payload FROM {table} WHERE run_id=?"
        if start_date is not None and end_date is not None:
            sql += " AND end_date>=? AND start_date<=?"; params += [start_date,end_date]
        sql += " ORDER BY ordinal"
        with self._lock: rows = self.db.execute(sql, params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def structure_overrides(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        with self._lock:
            rows = self.db.execute("""SELECT * FROM structure_overrides
                WHERE symbol=? AND timeframe=? AND adjustflag=? AND status IN ('active','conflicted')
                ORDER BY id""", (symbol, timeframe, adjustflag)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"] or "{}")} for row in rows]

    def create_structure_override(self, item: dict[str, Any], base_run: dict[str, Any] | None):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if base_run and (item.get("base_run_id") != base_run["id"] or item.get("base_structure_version") != base_run.get("structure_version", "")):
                raise ValueError("结构快照已变化，请刷新后重试")
            cur = self.db.execute("""INSERT INTO structure_overrides
                (symbol,timeframe,adjustflag,structure_type,target_id,operation,payload,base_run_id,base_structure_version,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'active',?,?)""", (
                    item["symbol"], item["timeframe"], item.get("adjustflag", "2"), item["structure_type"],
                    item.get("target_id"), item["operation"], json.dumps(item.get("payload", {}), ensure_ascii=False, sort_keys=True),
                    item.get("base_run_id") or (base_run["id"] if base_run else None),
                    item.get("base_structure_version") or (base_run.get("structure_version", "") if base_run else ""), now, now))
            override_id = cur.lastrowid
            self.db.execute("INSERT INTO structure_override_events(override_id,action,after_payload,created_at) VALUES(?,?,?,?)", (override_id, item["operation"], json.dumps(item.get("payload", {}), ensure_ascii=False), now))
            self.db.commit()
        return self.structure_override(override_id)

    def structure_override(self, override_id: int):
        with self._lock:
            row = self.db.execute("SELECT * FROM structure_overrides WHERE id=?", (override_id,)).fetchone()
        if not row: return None
        item = dict(row); item["payload"] = json.loads(item["payload"] or "{}"); return item

    def update_structure_override(self, override_id: int, payload: dict[str, Any], base_run: dict[str, Any] | None):
        current = self.structure_override(override_id)
        if not current: return None
        if base_run and (current.get("base_run_id") != base_run["id"] or current.get("base_structure_version") != base_run.get("structure_version", "")): raise ValueError("结构修订基准已变化，请刷新后重试")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.db.execute("UPDATE structure_overrides SET payload=?,operation='update',status='active',updated_at=? WHERE id=?", (json.dumps(payload, ensure_ascii=False, sort_keys=True), now, override_id))
            self.db.execute("INSERT INTO structure_override_events(override_id,action,before_payload,after_payload,created_at) VALUES(?,?,?,?,?)", (override_id, "update", json.dumps(current["payload"], ensure_ascii=False), json.dumps(payload, ensure_ascii=False), now))
            self.db.commit()
        return self.structure_override(override_id)

    def set_structure_override_status(self, override_id: int, status: str, operation: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.db.execute("UPDATE structure_overrides SET status=?,operation=?,updated_at=? WHERE id=?", (status, operation, now, override_id)); self.db.commit()
        return self.structure_override(override_id)

    def restore_structure_overrides(self, symbol: str, timeframe: str, adjustflag: str = "2", override_id: int | None = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if override_id is None:
                self.db.execute("UPDATE structure_overrides SET status='restored',operation='restore',updated_at=? WHERE symbol=? AND timeframe=? AND adjustflag=? AND status='active'", (now,symbol,timeframe,adjustflag))
            else:
                self.db.execute("UPDATE structure_overrides SET status='restored',operation='restore',updated_at=? WHERE id=?", (now,override_id))
            self.db.commit()

    def batch_structure_overrides(self, symbol: str, request: dict[str, Any], base_run: dict[str, Any]):
        if base_run["id"] != request["base_run_id"] or base_run.get("structure_version", "") != request["base_structure_version"]:
            raise ValueError("结构快照已变化，请刷新后重试")
        allowed_types = {"pen", "pen_center"}
        allowed_operations = {"create", "update", "delete"}
        now = datetime.now(timezone.utc).isoformat()
        created_ids: list[int] = []
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                for operation in request.get("operations", []):
                    typ = operation.get("structure_type")
                    action = operation.get("operation")
                    target_id = operation.get("target_id")
                    if typ not in allowed_types or action not in allowed_operations:
                        raise ValueError("结构修订参数不合法")
                    payload = operation.get("payload") or {}
                    cur = self.db.execute("""INSERT INTO structure_overrides
                        (symbol,timeframe,adjustflag,structure_type,target_id,operation,payload,
                         base_run_id,base_structure_version,status,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,'active',?,?)""", (
                        symbol, request["timeframe"], request.get("adjustflag", "2"), typ,
                        target_id, action, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        request["base_run_id"], request["base_structure_version"], now, now))
                    override_id = cur.lastrowid
                    created_ids.append(override_id)
                    self.db.execute("""INSERT INTO structure_override_events
                        (override_id,action,after_payload,created_at) VALUES(?,?,?,?)""",
                        (override_id, action, json.dumps(payload, ensure_ascii=False), now))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return {"items": [self.structure_override(i) for i in created_ids]}

    def drawings(self, symbol: str, timeframe: str):
        with self._lock:
            rows = self.db.execute("SELECT * FROM drawing_objects WHERE symbol=? AND timeframe=? AND deleted_at IS NULL ORDER BY id", (symbol,timeframe)).fetchall()
        return [{**dict(r), "start_anchor": json.loads(r["start_anchor"]), "end_anchor": json.loads(r["end_anchor"]), "style": json.loads(r["style"] or "{}"), "visible": bool(r["visible"])} for r in rows]

    def create_drawing(self, item: dict[str, Any]):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self.db.execute("""INSERT INTO drawing_objects(symbol,timeframe,object_type,start_anchor,end_anchor,style,label,visible,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (item["symbol"],item["timeframe"],item["object_type"],json.dumps(item["start_anchor"],ensure_ascii=False),json.dumps(item["end_anchor"],ensure_ascii=False),json.dumps(item.get("style",{}),ensure_ascii=False),item.get("label", ""),int(item.get("visible", True)),now,now)); self.db.commit(); return cur.lastrowid

    def update_drawing(self, drawing_id: int, item: dict[str, Any]):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.db.execute("""UPDATE drawing_objects SET start_anchor=?,end_anchor=?,style=?,label=?,visible=?,updated_at=? WHERE id=? AND deleted_at IS NULL""", (json.dumps(item["start_anchor"],ensure_ascii=False),json.dumps(item["end_anchor"],ensure_ascii=False),json.dumps(item.get("style",{}),ensure_ascii=False),item.get("label", ""),int(item.get("visible", True)),now,drawing_id)); self.db.commit()
        return next((x for x in self.drawings(item["symbol"],item["timeframe"]) if x["id"] == drawing_id), None)

    def delete_drawing(self, drawing_id: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self.db.execute("UPDATE drawing_objects SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL", (now,now,drawing_id)); self.db.commit(); return cur.rowcount > 0

    def drawing(self, drawing_id: int):
        with self._lock:
            row = self.db.execute("SELECT * FROM drawing_objects WHERE id=? AND deleted_at IS NULL", (drawing_id,)).fetchone()
        if not row: return None
        item = dict(row)
        item["start_anchor"] = json.loads(item["start_anchor"]); item["end_anchor"] = json.loads(item["end_anchor"])
        item["style"] = json.loads(item.get("style") or "{}"); item["visible"] = bool(item["visible"])
        return item

    def drawings_version(self, symbol: str, timeframe: str) -> str:
        rows = self.drawings(symbol, timeframe)
        payload = [{k: row.get(k) for k in ("id", "object_type", "start_anchor", "end_anchor", "style", "label", "visible", "updated_at")} for row in rows]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]

    def batch_drawings(self, symbol: str, request: dict[str, Any]):
        timeframe = request["timeframe"]
        current_version = self.drawings_version(symbol, timeframe)
        base_version = request.get("base_version", "")
        if base_version and base_version != current_version:
            raise ValueError("绘图已在其他窗口发生变化，请刷新后重试")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                for raw in request.get("create", []):
                    self.db.execute("""INSERT INTO drawing_objects
                        (symbol,timeframe,object_type,start_anchor,end_anchor,style,label,visible,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                        symbol, timeframe, raw["object_type"], json.dumps(raw["start_anchor"], ensure_ascii=False),
                        json.dumps(raw["end_anchor"], ensure_ascii=False), json.dumps(raw.get("style", {}), ensure_ascii=False),
                        raw.get("label", ""), int(raw.get("visible", True)), now, now))
                for change in request.get("update", []):
                    drawing_id = int(change["id"]); raw = change["drawing"]
                    cur = self.db.execute("""UPDATE drawing_objects SET start_anchor=?,end_anchor=?,style=?,label=?,visible=?,updated_at=?
                        WHERE id=? AND symbol=? AND timeframe=? AND deleted_at IS NULL""", (
                        json.dumps(raw["start_anchor"], ensure_ascii=False), json.dumps(raw["end_anchor"], ensure_ascii=False),
                        json.dumps(raw.get("style", {}), ensure_ascii=False), raw.get("label", ""),
                        int(raw.get("visible", True)), now, drawing_id, symbol, timeframe))
                    if cur.rowcount != 1: raise ValueError(f"绘图不存在: {drawing_id}")
                for drawing_id in request.get("delete_ids", []):
                    cur = self.db.execute("""UPDATE drawing_objects SET deleted_at=?,updated_at=?
                        WHERE id=? AND symbol=? AND timeframe=? AND deleted_at IS NULL""",
                        (now, now, int(drawing_id), symbol, timeframe))
                    if cur.rowcount != 1: raise ValueError(f"绘图不存在: {drawing_id}")
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return {"items": self.drawings(symbol, timeframe), "version": self.drawings_version(symbol, timeframe)}
