from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


MARKET_TZ = ZoneInfo("Asia/Shanghai")
MARKET_BAR_FIELDS = "trade_date,open,high,low,close,volume,amount,source,snapshot_id,source_revision,adjust_factor,is_suspended,limit_up,limit_down"


class Store:
    def __init__(self, path: str = "data/chant_agent.db", clock=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(MARKET_TZ))
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        with self._lock:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=5000")
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
            self.db.execute("""CREATE TABLE IF NOT EXISTS watchlist_section_order (
                section_key TEXT PRIMARY KEY, sort_order INTEGER NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS security_catalog (
                market_code TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
                market TEXT NOT NULL, trade_status TEXT NOT NULL DEFAULT '',
                catalog_date TEXT NOT NULL, refreshed_at TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_security_catalog_symbol ON security_catalog(symbol)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_security_catalog_name ON security_catalog(name)")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(stock_pool)")}
            if "sort_order" not in columns:
                self.db.execute("ALTER TABLE stock_pool ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_market_bars_lookup ON market_bars(symbol,timeframe,adjustflag,trade_date)")
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
            self._initialize_daily_confirmations()
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_structure_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, adjustflag TEXT NOT NULL,
                definition_version TEXT NOT NULL, calculator_fingerprint TEXT NOT NULL,
                market_version TEXT NOT NULL, structure_version TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, max_level INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL, finished_at TEXT, error TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}')""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_chan_runs_lookup ON chan_structure_runs(symbol,timeframe,adjustflag,id DESC)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_active_runs (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, adjustflag TEXT NOT NULL,
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                activated_at TEXT NOT NULL,
                PRIMARY KEY(symbol,timeframe,adjustflag))""")
            for table in ("chan_processed_bars", "chan_fractals", "chan_pens"):
                self.db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                    id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(run_id,id), UNIQUE(run_id,ordinal))""")
                self.db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_range ON {table}(run_id,start_date,end_date)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_components (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, level INTEGER NOT NULL, role TEXT NOT NULL,
                direction TEXT, status TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                start_price REAL NOT NULL, end_price REAL NOT NULL, low REAL NOT NULL, high REAL NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id,id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_component_units (
                run_id INTEGER NOT NULL, component_id TEXT NOT NULL, unit_kind TEXT NOT NULL,
                unit_id TEXT NOT NULL, role TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY(run_id,component_id,role,ordinal),
                FOREIGN KEY(run_id,component_id) REFERENCES chan_components(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_center_families (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, current_revision_id TEXT NOT NULL, base_level INTEGER NOT NULL,
                current_level INTEGER NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY(run_id,id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_center_revisions (
                run_id INTEGER NOT NULL, id TEXT NOT NULL, family_id TEXT NOT NULL,
                revision_no INTEGER NOT NULL, previous_revision_id TEXT, level INTEGER NOT NULL,
                status TEXT NOT NULL, active INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                zd REAL NOT NULL, zg REAL NOT NULL, dd REAL NOT NULL, gg REAL NOT NULL,
                entry_direction TEXT, core_formation_pattern TEXT, departure_direction TEXT,
                owner_movement_id TEXT, evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no),
                FOREIGN KEY(run_id,family_id) REFERENCES chan_center_families(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_center_units (
                run_id INTEGER NOT NULL, center_revision_id TEXT NOT NULL, unit_kind TEXT NOT NULL,
                unit_id TEXT NOT NULL, role TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY(run_id,center_revision_id,role,ordinal),
                FOREIGN KEY(run_id,center_revision_id) REFERENCES chan_center_revisions(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS idx_chan_core_owner
                ON chan_center_units(run_id,unit_kind,unit_id) WHERE role='core'""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_center_candidates (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, family_id TEXT NOT NULL, revision_no INTEGER NOT NULL,
                previous_revision_id TEXT, active INTEGER NOT NULL, level INTEGER NOT NULL,
                stream_id TEXT NOT NULL, source_kind TEXT NOT NULL, status TEXT NOT NULL,
                direction TEXT, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                zd REAL, zg REAL, observed_at TEXT NOT NULL, evidence_available_at TEXT NOT NULL,
                rejection_code TEXT, evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no))""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS idx_chan_center_candidate_active
                ON chan_center_candidates(run_id,level,stream_id,active)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_movement_families (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, current_revision_id TEXT NOT NULL, base_level INTEGER NOT NULL,
                current_level INTEGER NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY(run_id,id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_movement_revisions (
                run_id INTEGER NOT NULL, id TEXT NOT NULL, family_id TEXT NOT NULL,
                revision_no INTEGER NOT NULL, level INTEGER NOT NULL, status TEXT NOT NULL,
                classification TEXT, direction TEXT NOT NULL, active INTEGER NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL, start_price REAL NOT NULL, end_price REAL NOT NULL,
                confirmed_at TEXT, recursive_eligible INTEGER NOT NULL,
                termination_reason TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no),
                FOREIGN KEY(run_id,family_id) REFERENCES chan_movement_families(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_movement_units (
                run_id INTEGER NOT NULL, movement_revision_id TEXT NOT NULL, unit_kind TEXT NOT NULL,
                unit_id TEXT NOT NULL, role TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY(run_id,movement_revision_id,role,ordinal),
                FOREIGN KEY(run_id,movement_revision_id) REFERENCES chan_movement_revisions(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS idx_chan_movement_unit_owner
                ON chan_movement_units(run_id,unit_kind,unit_id) WHERE role='source'""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_movement_centers (
                run_id INTEGER NOT NULL, movement_revision_id TEXT NOT NULL,
                center_family_id TEXT NOT NULL, center_revision_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY(run_id,movement_revision_id,ordinal),
                FOREIGN KEY(run_id,movement_revision_id) REFERENCES chan_movement_revisions(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_point_families (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, current_revision_id TEXT NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY(run_id,id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_point_revisions (
                run_id INTEGER NOT NULL, id TEXT NOT NULL, family_id TEXT NOT NULL,
                revision_no INTEGER NOT NULL, level INTEGER NOT NULL, point_type TEXT NOT NULL,
                status TEXT NOT NULL, active INTEGER NOT NULL, point_date TEXT NOT NULL, point_price REAL NOT NULL,
                confirmed_at TEXT, source_unit_id TEXT NOT NULL, center_family_id TEXT NOT NULL,
                center_revision_id TEXT NOT NULL, movement_family_id TEXT, invalidated_reason TEXT,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no),
                FOREIGN KEY(run_id,family_id) REFERENCES chan_point_families(run_id,id) ON DELETE CASCADE)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_relations (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, level INTEGER NOT NULL, relation_type TEXT NOT NULL,
                from_id TEXT NOT NULL, to_id TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id,id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_issues (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 0, issue_type TEXT NOT NULL,
                start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id,id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_promotion_candidates (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, family_id TEXT NOT NULL, revision_no INTEGER NOT NULL,
                active INTEGER NOT NULL, status TEXT NOT NULL, candidate_source TEXT NOT NULL,
                child_level INTEGER NOT NULL, parent_level INTEGER NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL, observed_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no))""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS idx_chan_promotion_candidate_active
                ON chan_promotion_candidates(run_id,family_id,active)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS chan_segment_proofs (
                run_id INTEGER NOT NULL REFERENCES chan_structure_runs(id) ON DELETE CASCADE,
                id TEXT NOT NULL, family_id TEXT NOT NULL, revision_no INTEGER NOT NULL,
                previous_revision_id TEXT, active INTEGER NOT NULL, level INTEGER NOT NULL,
                source_kind TEXT NOT NULL, status TEXT NOT NULL, direction TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                evidence_available_at TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no))""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS idx_chan_segment_proof_active
                ON chan_segment_proofs(run_id,family_id,active)""")
            self.db.commit()

    def _initialize_daily_confirmations(self) -> None:
        # The table's existence marks this one-time migration as complete, so
        # its DDL and historical inserts must commit (or roll back) together.
        self.db.execute("SAVEPOINT daily_confirmations_migration")
        try:
            existed = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_bar_confirmations'"
            ).fetchone()
            self.db.execute("""CREATE TABLE IF NOT EXISTS daily_bar_confirmations (
                symbol TEXT NOT NULL, adjustflag TEXT NOT NULL, trade_date TEXT NOT NULL,
                row_hash TEXT NOT NULL, confirmed_at TEXT NOT NULL,
                PRIMARY KEY(symbol,adjustflag,trade_date))""")
            if not existed:
                self._backfill_daily_confirmations(self._market_time(self.clock()))
            self.db.execute("RELEASE SAVEPOINT daily_confirmations_migration")
        except BaseException:
            self.db.execute("ROLLBACK TO SAVEPOINT daily_confirmations_migration")
            self.db.execute("RELEASE SAVEPOINT daily_confirmations_migration")
            raise

    def _backfill_daily_confirmations(self, migrated_at: datetime) -> None:
        # Never revisit this baseline on a later day: post-migration intraday
        # caches require new successful-fetch evidence before becoming formal.
        legacy_rows = self.db.execute(
            "SELECT * FROM market_bars WHERE timeframe='d' AND trade_date<?",
            (migrated_at.date().isoformat(),),
        ).fetchall()
        self.db.executemany("""INSERT INTO daily_bar_confirmations
            (symbol,adjustflag,trade_date,row_hash,confirmed_at) VALUES(?,?,?,?,?)""",
            [(row["symbol"], row["adjustflag"], row["trade_date"],
              self._daily_bar_hash(dict(row)), migrated_at.isoformat()) for row in legacy_rows])

    def list_stock_pool(self):
        with self._lock:
            rows = self.db.execute("SELECT symbol,name,enabled,sort_order,created_at,updated_at FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                latest = self.db.execute("""SELECT r.status,r.finished_at,r.error
                    FROM chan_structure_runs r JOIN chan_active_runs a ON a.run_id=r.id
                    WHERE a.symbol=? ORDER BY r.id DESC""", (item["symbol"],)).fetchall()
                item["sync_status"] = "success" if latest else None
                item["last_sync"] = max(
                    (run["finished_at"] for run in latest if run["finished_at"]), default=None,
                )
                item["sync_error"] = ""
                item["market"] = self.market_summary(item["symbol"], "2")
                result.append(item)
            return result

    def watchlist_symbols(self) -> list[str]:
        with self._lock:
            return [row[0] for row in self.db.execute(
                "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
            ).fetchall()]

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
        with self._lock:
            return {
                "stocks": self.list_stock_pool(),
                "groups": self.list_watchlist_groups(),
                "memberships": self.watchlist_memberships(),
                "section_order": self.list_watchlist_section_order(),
            }

    def list_watchlist_section_order(self) -> list[str]:
        with self._lock:
            groups = self.list_watchlist_groups()
            valid = {"all", "ungrouped", *(f"group-{group['id']}" for group in groups)}
            saved = [row[0] for row in self.db.execute(
                "SELECT section_key FROM watchlist_section_order ORDER BY sort_order,section_key"
            ).fetchall()]
            ordered = [key for key in saved if key in valid]
            return ordered + [key for key in ("all", "ungrouped", *(f"group-{group['id']}" for group in groups))
                              if key not in set(ordered)]

    def reorder_watchlist_sections(self, section_keys: list[str]) -> list[str]:
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("分组排序包含重复分组")
        with self._lock:
            current = self.list_watchlist_section_order()
            if set(current) != set(section_keys) or len(current) != len(section_keys):
                raise ValueError("分组列表已变化，请刷新后重试")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("DELETE FROM watchlist_section_order")
                self.db.executemany("INSERT INTO watchlist_section_order(section_key,sort_order) VALUES(?,?)",
                                    [(key, index) for index, key in enumerate(section_keys)])
                group_ids = [int(key.removeprefix("group-")) for key in section_keys if key.startswith("group-")]
                now = datetime.now(timezone.utc).isoformat()
                self.db.executemany("UPDATE watchlist_groups SET sort_order=?,updated_at=? WHERE id=?",
                                    [(index, now, group_id) for index, group_id in enumerate(group_ids)])
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return section_keys

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
                self.db.execute("DELETE FROM watchlist_section_order WHERE section_key=?", (f"group-{group_id}",))
                self.db.commit()
                return True
            except Exception:
                self.db.rollback()
                raise

    def reorder_watchlist_groups(self, group_ids: list[int]) -> list[dict[str, Any]]:
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("分组排序包含重复分组")
        with self._lock:
            current = [row[0] for row in self.db.execute(
                "SELECT id FROM watchlist_groups ORDER BY sort_order,id"
            ).fetchall()]
            if set(current) != set(group_ids) or len(current) != len(group_ids):
                raise ValueError("分组列表已变化，请刷新后重试")
            current_sections = self.list_watchlist_section_order()
            custom = iter(group_ids)
            sections = [f"group-{next(custom)}" if key.startswith("group-") else key for key in current_sections]
            self.reorder_watchlist_sections(sections)
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

    @staticmethod
    def _market_time(value: datetime | str) -> datetime:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=MARKET_TZ)
        return stamp.astimezone(MARKET_TZ)

    @staticmethod
    def _daily_bar_hash(row: dict[str, Any]) -> str:
        """Hash accepted market content and provenance, never SQLite identity."""
        payload = {field: float(row.get(field, default) or 0) for field, default in (
            ("open", 0), ("high", 0), ("low", 0), ("close", 0), ("volume", 0),
            ("amount", 0), ("adjust_factor", 1),
        )}
        payload.update({field: float(row[field]) if row.get(field) is not None else None
                        for field in ("limit_up", "limit_down")})
        payload.update({"trade_date": str(row["trade_date"]),
                        "is_suspended": bool(row.get("is_suspended", False)),
                        "source": row.get("source", "baostock"),
                        "snapshot_id": row.get("snapshot_id", ""),
                        "source_revision": row.get("source_revision", "")})
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _confirm_daily_rows(self, symbol: str, adjustflag: str, rows: list[dict[str, Any]],
                            fetched_at: datetime, request_started_at: datetime) -> list[str]:
        """Called inside the writer transaction; return newly confirmed dates."""
        fetched_at = self._market_time(fetched_at)
        request_started_at = self._market_time(request_started_at)
        if fetched_at < request_started_at:
            raise ValueError("日线确认的请求开始时间不能晚于响应时间")
        changed = []
        for candidate in rows:
            day = str(candidate["trade_date"])
            close_at = datetime.fromisoformat(day).replace(
                hour=15, minute=0, second=15, microsecond=0, tzinfo=MARKET_TZ,
            )
            if request_started_at < close_at:
                continue
            calendar = self.db.execute("""SELECT is_trading_day FROM trade_calendar
                WHERE exchange='CN' AND trade_date=?""", (day,)).fetchone()
            # A historical response is already a completed source. For a same-day
            # response, positive exchange-calendar evidence is also required.
            if (calendar and not calendar[0]) or (day == fetched_at.date().isoformat() and not calendar):
                continue
            stored = self.db.execute("""SELECT * FROM market_bars
                WHERE symbol=? AND timeframe='d' AND adjustflag=? AND trade_date=?""",
                (symbol, adjustflag, day)).fetchone()
            candidate_hash = self._daily_bar_hash(candidate)
            if not stored or self._daily_bar_hash(dict(stored)) != candidate_hash:
                continue
            previous = self.db.execute("""SELECT row_hash FROM daily_bar_confirmations
                WHERE symbol=? AND adjustflag=? AND trade_date=?""", (symbol, adjustflag, day)).fetchone()
            if not previous or previous[0] != candidate_hash:
                changed.append(day)
            self.db.execute("""INSERT INTO daily_bar_confirmations
                (symbol,adjustflag,trade_date,row_hash,confirmed_at) VALUES(?,?,?,?,?)
                ON CONFLICT(symbol,adjustflag,trade_date) DO UPDATE SET
                row_hash=excluded.row_hash,confirmed_at=excluded.confirmed_at""",
                (symbol, adjustflag, day, candidate_hash, fetched_at.isoformat()))
        return changed

    def confirm_daily_bars(self, symbol: str, adjustflag: str, rows: list[dict[str, Any]],
                           fetched_at: datetime, request_started_at: datetime | None = None) -> int:
        """Register explicit successful-fetch evidence matching the stored rows.

        Callers that also write rows should pass these timestamps to
        upsert_bars_with_changes so both actions share a transaction.
        """
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                changed = self._confirm_daily_rows(symbol, adjustflag, rows, fetched_at,
                                                   request_started_at or fetched_at)
                self.db.commit()
                return len(changed)
            except Exception:
                self.db.rollback()
                raise

    def confirmed_daily_bars(self, symbol: str, adjustflag: str = "2") -> list[dict[str, Any]]:
        """Return full-history daily input with matching content and close evidence."""
        with self._lock:
            rows = self.market_bars(symbol, "d", adjustflag, "0000-01-01")
            confirmations = {row["trade_date"]: dict(row) for row in self.db.execute("""
                SELECT trade_date,row_hash,confirmed_at FROM daily_bar_confirmations
                WHERE symbol=? AND adjustflag=?""", (symbol, adjustflag))}
            now = self._market_time(self.clock())
            confirmed = []
            for row in rows:
                proof = confirmations.get(row["trade_date"])
                if not proof or proof["row_hash"] != self._daily_bar_hash(row):
                    continue
                try:
                    stamp = self._market_time(proof["confirmed_at"])
                    close_at = datetime.fromisoformat(row["trade_date"]).replace(
                        hour=15, minute=0, second=15, microsecond=0, tzinfo=MARKET_TZ,
                    )
                except (ValueError, TypeError):
                    continue
                if close_at <= stamp <= now:
                    confirmed.append(row)
            return confirmed

    def upsert_bars_with_changes(self, symbol: str, timeframe: str, adjustflag: str, rows: list[dict[str, Any]], source: str = "baostock", snapshot_id: str = "", allow_lower_priority: bool = False,
                                *, fetched_at: datetime | None = None,
                                request_started_at: datetime | None = None) -> tuple[int, str | None]:
        priority = {"tencent": 0, "baostock": 1, "api": 2, "csv": 3}
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
                if old and not allow_lower_priority and priority.get(new_source, 0) < priority.get(old.get("source", "baostock"), 0):
                    continue
                new_prices = tuple(float(row.get(key, 0) or 0) for key in ("open", "high", "low", "close", "volume", "amount"))
                old_prices = tuple(old[key] for key in ("open", "high", "low", "close", "volume", "amount")) if old else None
                if old_prices != new_prices:
                    changed.append(row["trade_date"])
                accepted.append(value)
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.executemany("""INSERT INTO market_bars(symbol,timeframe,trade_date,open,high,low,close,volume,amount,adjustflag,source,snapshot_id,source_revision,adjust_factor,is_suspended,limit_up,limit_down)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,trade_date,adjustflag) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,amount=excluded.amount,
                    source=excluded.source,snapshot_id=excluded.snapshot_id,source_revision=excluded.source_revision,adjust_factor=excluded.adjust_factor,
                    is_suspended=excluded.is_suspended,limit_up=excluded.limit_up,limit_down=excluded.limit_down""", accepted)
                if timeframe == "d":
                    accepted_dates = {value[2] for value in accepted}
                    supplied = [{**row, "source": row.get("source", source),
                                 "snapshot_id": row.get("snapshot_id", snapshot_id)}
                                for row in rows if row["trade_date"] in accepted_dates]
                    # Invalidate immediately; reverting to older prices must not
                    # resurrect their former close proof without a new fetch.
                    for row in supplied:
                        invalidated = self.db.execute("""DELETE FROM daily_bar_confirmations
                            WHERE symbol=? AND adjustflag=? AND trade_date=? AND row_hash<>?""",
                            (symbol, adjustflag, row["trade_date"], self._daily_bar_hash(row)))
                        if invalidated.rowcount:
                            changed.append(row["trade_date"])
                    if fetched_at is not None:
                        changed.extend(self._confirm_daily_rows(symbol, adjustflag, supplied,
                                       fetched_at, request_started_at or fetched_at))
                    else:
                        # New historical imports are an explicit completed-history
                        # source. Existing unconfirmed rows never gain a proof here.
                        imported_at = self._market_time(self.clock())
                        historical = [row for row in supplied
                                      if row["trade_date"] not in existing
                                      and row["trade_date"] < imported_at.date().isoformat()]
                        changed.extend(self._confirm_daily_rows(symbol, adjustflag, historical,
                                       imported_at, imported_at))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return len(accepted), min(changed) if changed else None

    def market_bars(self, symbol: str, timeframe: str, adjustflag: str, start_date: str = "2015-01-01", end_date: str = "9999-12-31", limit: int | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [symbol, timeframe, adjustflag, start_date, end_date]
        fields = MARKET_BAR_FIELDS
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
        return {
            "bars": rows,
            "has_more": has_more,
            "next_before": rows[0]["trade_date"] if rows else None,
        }

    def market_summary(self, symbol: str, adjustflag: str):
        with self._lock:
            rows = self.db.execute("SELECT timeframe,MIN(trade_date) range_start,MAX(trade_date) range_end,COUNT(*) bar_count FROM market_bars WHERE symbol=? AND adjustflag=? GROUP BY timeframe", (symbol, adjustflag)).fetchall()
            daily = self.db.execute("SELECT close FROM market_bars WHERE symbol=? AND timeframe='d' AND adjustflag=? ORDER BY trade_date DESC LIMIT 2", (symbol, adjustflag)).fetchall()
        latest = daily[0][0] if daily else None
        change_pct = ((daily[0][0] / daily[1][0] - 1) * 100) if len(daily) > 1 and daily[1][0] else None
        return {"ranges": {row["timeframe"]: dict(row) for row in rows}, "latest": latest, "change_pct": change_pct}

    def active_chan_run(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        with self._lock:
            row = self.db.execute("""SELECT r.* FROM chan_active_runs a
                JOIN chan_structure_runs r ON r.id=a.run_id
                WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""",
                (symbol, timeframe, adjustflag)).fetchone()
        return dict(row) if row else None

    def highest_active_chan_level(self, definition_version: str) -> int:
        with self._lock:
            row = self.db.execute("""SELECT MAX(r.max_level) FROM chan_active_runs a
                JOIN chan_structure_runs r ON r.id=a.run_id
                WHERE r.definition_version=? AND r.status='success'""", (definition_version,)).fetchone()
        return int(row[0] or 0)

    @staticmethod
    def _payload_json(item: dict[str, Any]) -> str:
        return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _insert_base_rows(self, run_id: int, table: str, values: list[dict[str, Any]]) -> None:
        rows = []
        for ordinal, item in enumerate(values):
            identifier = str(item.get("id") or f"{table}-{ordinal}")
            start = str(item.get("start_date") or item.get("trade_date") or "")
            end = str(item.get("end_date") or item.get("trade_date") or start)
            rows.append((run_id, identifier, ordinal, start, end, self._payload_json(item)))
        if rows:
            self.db.executemany(
                f"INSERT INTO {table}(run_id,id,ordinal,start_date,end_date,payload_json) VALUES(?,?,?,?,?,?)",
                rows,
            )

    def _insert_components(self, run_id: int, values: list[dict[str, Any]]) -> None:
        for item in values:
            self.db.execute("""INSERT INTO chan_components
                (run_id,id,level,role,direction,status,start_date,end_date,start_price,end_price,low,high,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], int(item["level"]), item["role"], item.get("direction"),
                item["status"], item["start_date"], item["end_date"], float(item["start_price"]),
                float(item["end_price"]), float(item["low"]), float(item["high"]), self._payload_json(item),
            ))
            for ordinal, unit_id in enumerate(item.get("source_unit_ids", [])):
                self.db.execute("""INSERT INTO chan_component_units
                    (run_id,component_id,unit_kind,unit_id,role,ordinal) VALUES(?,?,?,?,?,?)""",
                    (run_id, item["id"], item["unit_kind"], unit_id, "source", ordinal))

    def _insert_centers(self, run_id: int, values: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in values:
            grouped.setdefault(item["family_id"], []).append(item)
        for family_id, revisions in grouped.items():
            active = [item for item in revisions if item.get("active")]
            if len(active) > 1:
                raise ValueError("中枢族存在多个活动修订")
            current = active[0] if active else max(revisions, key=lambda item: int(item["revision_no"]))
            self.db.execute("""INSERT INTO chan_center_families
                (run_id,id,current_revision_id,base_level,current_level,status) VALUES(?,?,?,?,?,?)""",
                (run_id, family_id, current["id"], min(int(item["level"]) for item in revisions),
                 int(current["level"]), current["status"]))
        for item in values:
            self.db.execute("""INSERT INTO chan_center_revisions
                (run_id,id,family_id,revision_no,previous_revision_id,level,status,active,start_date,end_date,
                 zd,zg,dd,gg,entry_direction,core_formation_pattern,departure_direction,owner_movement_id,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], item["family_id"], int(item["revision_no"]), item.get("previous_revision_id"),
                int(item["level"]), item["status"], int(bool(item.get("active"))), item["start_date"], item["end_date"],
                float(item["zd"]), float(item["zg"]), float(item["dd"]), float(item["gg"]),
                item.get("entry_direction"), item.get("core_formation_pattern"), item.get("departure_direction"),
                item.get("owner_movement_id"), self._payload_json(item),
            ))
            role_fields = {
                "entry": "entry_unit_ids", "core": "core_unit_ids", "extension": "extension_unit_ids",
                "peripheral": "peripheral_unit_ids", "departure": "departure_unit_ids", "retest": "retest_unit_ids",
                "child_center": "child_center_ids", "child_movement": "child_movement_ids",
                "z_wave": "z_unit_ids",
            }
            for role, field in role_fields.items():
                for ordinal, unit_id in enumerate(item.get(field, [])):
                    self.db.execute("""INSERT INTO chan_center_units
                        (run_id,center_revision_id,unit_kind,unit_id,role,ordinal) VALUES(?,?,?,?,?,?)""",
                        (run_id, item["id"], "center_revision" if role == "child_center" else "movement" if role == "child_movement" else item["unit_kind"], unit_id, role, ordinal))

    def _insert_center_candidates(self, run_id: int, values: list[dict[str, Any]]) -> None:
        for item in values:
            self.db.execute("""INSERT INTO chan_center_candidates
                (run_id,id,family_id,revision_no,previous_revision_id,active,level,stream_id,source_kind,
                 status,direction,start_date,end_date,zd,zg,observed_at,evidence_available_at,rejection_code,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], item["family_id"], int(item.get("revision_no", 1)), item.get("previous_revision_id"),
                int(bool(item.get("active"))), int(item["level"]), str(item.get("stream_id", "")),
                item.get("source_kind", "pen"), item["status"], item.get("direction"), item.get("start_date", ""),
                item.get("end_date", ""), item.get("zd"), item.get("zg"), item.get("observed_at", ""),
                item.get("evidence_available_at", item.get("observed_at", "")), item.get("rejection_code"),
                self._payload_json(item),
            ))

    def _insert_movements(self, run_id: int, values: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in values:
            grouped.setdefault(item["family_id"], []).append(item)
        for family_id, revisions in grouped.items():
            current = max(revisions, key=lambda item: (bool(item.get("active")), int(item["level"]), int(item["revision_no"])))
            self.db.execute("""INSERT INTO chan_movement_families
                (run_id,id,current_revision_id,base_level,current_level,status) VALUES(?,?,?,?,?,?)""",
                (run_id, family_id, current["id"], min(int(item["level"]) for item in revisions),
                 int(current["level"]), current["status"]))
        for item in values:
            self.db.execute("""INSERT INTO chan_movement_revisions
                (run_id,id,family_id,revision_no,level,status,classification,direction,active,start_date,end_date,
                 start_price,end_price,confirmed_at,recursive_eligible,termination_reason,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], item["family_id"], int(item["revision_no"]), int(item["level"]),
                item["status"], item.get("classification"), item["direction"], int(bool(item.get("active"))),
                item["start_date"], item["end_date"], float(item["start_price"]), float(item["end_price"]),
                item.get("confirmed_at"), int(bool(item.get("recursive_eligible"))), item["termination_reason"],
                self._payload_json(item),
            ))
            for ordinal, unit_id in enumerate(item.get("source_unit_ids", [])):
                self.db.execute("""INSERT INTO chan_movement_units
                    (run_id,movement_revision_id,unit_kind,unit_id,role,ordinal) VALUES(?,?,?,?,?,?)""",
                    (run_id, item["id"], "unit", unit_id, "source", ordinal))
            for ordinal, (family_id, revision_id) in enumerate(zip(
                item.get("center_family_ids", []), item.get("center_revision_ids", []),
            )):
                self.db.execute("""INSERT INTO chan_movement_centers
                    (run_id,movement_revision_id,center_family_id,center_revision_id,ordinal) VALUES(?,?,?,?,?)""",
                    (run_id, item["id"], family_id, revision_id, ordinal))

    def _insert_points(self, run_id: int, values: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in values:
            grouped.setdefault(item["family_id"], []).append(item)
        for family_id, revisions in grouped.items():
            current = max(revisions, key=lambda item: (bool(item.get("active")), int(item["revision_no"])))
            self.db.execute("""INSERT INTO chan_point_families
                (run_id,id,current_revision_id,status) VALUES(?,?,?,?)""",
                (run_id, family_id, current["id"], current["status"]))
        for item in values:
            self.db.execute("""INSERT INTO chan_point_revisions
                (run_id,id,family_id,revision_no,level,point_type,status,active,point_date,point_price,
                 confirmed_at,source_unit_id,center_family_id,center_revision_id,movement_family_id,
                 invalidated_reason,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], item["family_id"], int(item["revision_no"]), int(item["level"]),
                item["point_type"], item["status"], int(bool(item.get("active"))), item["point_date"],
                float(item["point_price"]), item.get("confirmed_at"), item["source_unit_id"],
                item["center_family_id"], item["center_revision_id"], item.get("movement_family_id"),
                item.get("invalidated_reason"), self._payload_json(item),
            ))

    def _insert_promotion_candidates(self, run_id: int, values: list[dict[str, Any]]) -> None:
        for item in values:
            self.db.execute("""INSERT INTO chan_promotion_candidates
                (run_id,id,family_id,revision_no,active,status,candidate_source,child_level,parent_level,
                 start_date,end_date,observed_at,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], item["family_id"], int(item["revision_no"]),
                int(bool(item.get("active"))), item["status"], item["candidate_source"],
                int(item["child_level"]), int(item["parent_level"]), item["start_date"],
                item["end_date"], item["observed_at"], self._payload_json(item),
            ))

    def _insert_segment_proofs(self, run_id: int, values: list[dict[str, Any]]) -> None:
        for item in values:
            self.db.execute("""INSERT INTO chan_segment_proofs
                (run_id,id,family_id,revision_no,previous_revision_id,active,level,source_kind,status,
                 direction,start_date,end_date,evidence_available_at,evidence_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, item["id"], item["family_id"], int(item.get("revision_no", 1)),
                item.get("previous_revision_id"), int(bool(item.get("active", True))),
                int(item["level"]), item.get("source_kind", "local_pen_group"), item["status"],
                item["direction"], item["start_date"], item["end_date"],
                item.get("evidence_available_at", item.get("available_at", item["end_date"])),
                self._payload_json(item),
            ))

    def replace_chan_structure(
        self, symbol: str, timeframe: str, adjustflag: str,
        result: dict[str, Any], market_version: str, *, activate: bool = True,
    ) -> int:
        from .chan_structure import assert_valid_structure

        assert_valid_structure(result)
        structure = result["structure"]
        meta = {**result["meta"], "structure_aux": {k: structure[k] for k in
                ("hierarchy_version", "unassigned_by_level", "pen_diagnostics") if k in structure}}
        started_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                cursor = self.db.execute("""INSERT INTO chan_structure_runs
                    (symbol,timeframe,adjustflag,definition_version,calculator_fingerprint,market_version,
                     structure_version,status,max_level,started_at,meta_json)
                    VALUES(?,?,?,?,?,?,?,'running',?,?,?)""", (
                    symbol, timeframe, adjustflag, meta["definition_version"], meta["calculator_fingerprint"],
                    market_version, meta["structure_version"], int(meta.get("max_level", 0)), started_at,
                    self._payload_json(meta),
                ))
                run_id = int(cursor.lastrowid)
                self._insert_base_rows(run_id, "chan_processed_bars", structure.get("processed_bars", []))
                self._insert_base_rows(run_id, "chan_fractals", structure.get("fractals", []))
                self._insert_base_rows(run_id, "chan_pens", structure.get("pens", []))
                self._insert_components(run_id, structure.get("components", []))
                self._insert_centers(run_id, structure.get("center_revisions", []))
                self._insert_center_candidates(run_id, structure.get("center_candidate_revisions", []))
                self._insert_movements(run_id, structure.get("movement_revisions", []))
                self._insert_points(run_id, structure.get("point_revisions", []))
                self._insert_segment_proofs(run_id, structure.get("segment_proof_revisions", []))
                self._insert_promotion_candidates(
                    run_id, structure.get("promotion_candidate_revisions", []),
                )
                for item in structure.get("relations", []):
                    self.db.execute("""INSERT INTO chan_relations
                        (run_id,id,level,relation_type,from_id,to_id,start_date,end_date,evidence_json)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (
                        run_id, item["id"], int(item.get("level", 0)), item["relation_type"],
                        item["from_id"], item["to_id"], item.get("start_date", ""), item.get("end_date", ""),
                        self._payload_json(item),
                    ))
                for ordinal, item in enumerate(structure.get("issues", [])):
                    issue_id = str(item.get("id") or f"issue-{ordinal}")
                    self.db.execute("""INSERT INTO chan_issues
                        (run_id,id,level,issue_type,start_date,end_date,evidence_json) VALUES(?,?,?,?,?,?,?)""", (
                        run_id, issue_id, int(item.get("level", 0)), item.get("issue_type", "structure_issue"),
                        item.get("start_date", ""), item.get("end_date", ""), self._payload_json(item),
                    ))
                finished_at = datetime.now(timezone.utc).isoformat()
                self.db.execute("UPDATE chan_structure_runs SET status='success',finished_at=? WHERE id=?", (finished_at, run_id))
                if activate:
                    self.db.execute("""INSERT INTO chan_active_runs(symbol,timeframe,adjustflag,run_id,activated_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(symbol,timeframe,adjustflag) DO UPDATE SET
                        run_id=excluded.run_id,activated_at=excluded.activated_at""",
                        (symbol, timeframe, adjustflag, run_id, finished_at))
                # Successful runs are immutable evidence and rollback targets.
                # Retention is an explicit maintenance operation, never activation.
                self.db.commit()
                return run_id
            except Exception as exc:
                self.db.rollback()
                self.db.execute("""INSERT INTO chan_structure_runs
                    (symbol,timeframe,adjustflag,definition_version,calculator_fingerprint,market_version,
                     structure_version,status,max_level,started_at,finished_at,error,meta_json)
                    VALUES(?,?,?,?,?,?,?,'failed',?,?,?,?,?)""", (
                    symbol, timeframe, adjustflag, meta.get("definition_version", ""),
                    meta.get("calculator_fingerprint", ""), market_version, meta.get("structure_version", ""),
                    int(meta.get("max_level", 0)), started_at, datetime.now(timezone.utc).isoformat(),
                    f"{type(exc).__name__}: {exc}"[:2000], self._payload_json(meta),
                ))
                self.db.execute("""DELETE FROM chan_structure_runs WHERE id NOT IN (
                    SELECT id FROM chan_structure_runs WHERE symbol=? AND timeframe=? AND adjustflag=?
                    AND status='failed' ORDER BY id DESC LIMIT 1
                ) AND symbol=? AND timeframe=? AND adjustflag=? AND status='failed'""",
                    (symbol, timeframe, adjustflag, symbol, timeframe, adjustflag))
                self.db.commit()
                raise

    def activate_chan_runs(self, run_ids: list[int], *, definition_version: str, calculator_fingerprint: str) -> None:
        """Switch a validated release matrix in one transaction, retaining old runs."""
        if not run_ids or len(run_ids) != len(set(run_ids)):
            raise ValueError("运行矩阵为空或重复")
        with self._lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = [self.db.execute("SELECT * FROM chan_structure_runs WHERE id=?", (i,)).fetchone() for i in run_ids]
            if any(not row or row["status"] != "success" or row["definition_version"] != definition_version
                   or row["calculator_fingerprint"] != calculator_fingerprint for row in rows):
                raise ValueError("运行矩阵版本或状态不匹配")
            keys = [(r["symbol"], r["timeframe"], r["adjustflag"]) for r in rows]
            if len(keys) != len(set(keys)):
                raise ValueError("运行矩阵有重复标的周期")
            stamp = datetime.now(timezone.utc).isoformat()
            for row in rows:
                self.db.execute("""INSERT INTO chan_active_runs(symbol,timeframe,adjustflag,run_id,activated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(symbol,timeframe,adjustflag) DO UPDATE SET
                    run_id=excluded.run_id,activated_at=excluded.activated_at""",
                    (row["symbol"], row["timeframe"], row["adjustflag"], row["id"], stamp))

    def _load_json_rows(self, table: str, run_id: int, column: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(f"SELECT {column} FROM {table} WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def load_chan_structure(self, run_id: int) -> dict[str, Any]:
        with self._lock:
            return self._load_chan_structure(run_id)

    def _load_chan_structure(self, run_id: int) -> dict[str, Any]:
        run = self.db.execute("SELECT * FROM chan_structure_runs WHERE id=? AND status='success'", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"结构运行不存在或未成功: {run_id}")
        meta = json.loads(run["meta_json"])
        meta.update({"run_id": run_id, "market_version": run["market_version"]})
        center_revisions = self._load_json_rows("chan_center_revisions", run_id, "evidence_json")
        center_candidate_revisions = self._load_json_rows("chan_center_candidates", run_id, "evidence_json")
        movement_revisions = self._load_json_rows("chan_movement_revisions", run_id, "evidence_json")
        point_revisions = self._load_json_rows("chan_point_revisions", run_id, "evidence_json")
        segment_proof_revisions = self._load_json_rows("chan_segment_proofs", run_id, "evidence_json")
        promotion_candidate_revisions = self._load_json_rows(
            "chan_promotion_candidates", run_id, "evidence_json",
        )
        structure = {
            "processed_bars": self._load_json_rows("chan_processed_bars", run_id, "payload_json"),
            "fractals": self._load_json_rows("chan_fractals", run_id, "payload_json"),
            "pens": self._load_json_rows("chan_pens", run_id, "payload_json"),
            "components": self._load_json_rows("chan_components", run_id, "evidence_json"),
            "centers": [item for item in center_revisions if item.get("active")],
            "center_revisions": center_revisions,
            "center_candidates": [item for item in center_candidate_revisions if item.get("active")],
            "center_candidate_revisions": center_candidate_revisions,
            "movements": [item for item in movement_revisions if item.get("active", True)],
            "movement_revisions": movement_revisions,
            "points": [item for item in point_revisions if item.get("active", True)],
            "point_revisions": point_revisions,
            "segment_proofs": [item for item in segment_proof_revisions if item.get("active", True) and item.get("selection_status") == "selected"],
            "segment_proof_revisions": segment_proof_revisions,
            "promotion_candidates": [
                item for item in promotion_candidate_revisions if item.get("active")
            ],
            "promotion_candidate_revisions": promotion_candidate_revisions,
            "relations": self._load_json_rows("chan_relations", run_id, "evidence_json"),
            "issues": self._load_json_rows("chan_issues", run_id, "evidence_json"),
        }
        structure["levels"] = sorted({int(item["level"]) for item in structure["centers"]})
        structure["max_level"] = max(structure["levels"], default=0)
        structure["unassigned_by_level"] = meta.get("unassigned_by_level", {})
        structure.update(meta.pop("structure_aux", {}))
        return {"meta": meta, "structure": structure}

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
