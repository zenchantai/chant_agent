import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.store import Store


TZ = ZoneInfo("Asia/Shanghai")


def bar(trade_date: str, price: float = 10, **changes):
    return {
        "trade_date": trade_date,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
        "volume": 100,
        "amount": 1000,
        **changes,
    }


def legacy_market_database(path, rows):
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE market_bars (
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
        trade_date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
        close REAL NOT NULL, volume REAL NOT NULL, amount REAL DEFAULT 0, adjustflag TEXT NOT NULL,
        UNIQUE(symbol,timeframe,trade_date,adjustflag))""")
    connection.executemany("""INSERT INTO market_bars
        (symbol,timeframe,trade_date,open,high,low,close,volume,amount,adjustflag)
        VALUES ('000001',?,?,?,?,?,?,?,?,'2')""", [
        (row["timeframe"], row["trade_date"], row["open"], row["high"], row["low"],
         row["close"], row["volume"], row["amount"])
        for row in rows
    ])
    connection.commit()
    connection.close()


def test_initialization_backfills_period_keys_without_silently_deleting_duplicates(tmp_path):
    path = tmp_path / "legacy-periods.db"
    legacy_market_database(path, [
        {**bar("2026-01-05"), "timeframe": "w"},
        {**bar("2026-01-06", 11), "timeframe": "w"},
        {**bar("2026-01-31"), "timeframe": "m"},
    ])

    store = Store(str(path))
    rows = store.db.execute(
        "SELECT timeframe,trade_date,period_key FROM market_bars ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("w", "2026-01-05", "2026-W02"),
        ("w", "2026-01-06", "2026-W02"),
        ("m", "2026-01-31", "2026-01"),
    ]
    assert store.db.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 3
    assert store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='uq_market_bars_period'"
    ).fetchone() is None


def test_canonicalization_requires_exact_reviewed_ids_and_keeps_latest_period_row(tmp_path):
    path = tmp_path / "duplicate-periods.db"
    legacy_market_database(path, [
        {**bar("2026-01-05"), "timeframe": "w"},
        {**bar("2026-01-06", 11), "timeframe": "w"},
    ])
    store = Store(str(path))
    report = store.period_key_migration_report()
    assert report["duplicate_group_count"] == report["delete_row_count"] == 1
    assert report["groups"][0]["keep_trade_date"] == "2026-01-06"

    with pytest.raises(ValueError, match="dry-run"):
        store.canonicalize_period_bars([])
    assert store.db.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 2

    result = store.canonicalize_period_bars(report["delete_ids"])
    assert result["status"] == "success" and result["deleted_row_count"] == 1
    assert result["duplicate_group_count"] == 0 and result["unique_index_ready"] is True
    assert [row["trade_date"] for row in store.market_bars("000001", "w", "2")] == [
        "2026-01-06"
    ]


@pytest.mark.parametrize("timeframe,dates,expected_key", [
    ("w", ("2026-01-05", "2026-01-06"), "2026-W02"),
    ("m", ("2026-01-05", "2026-01-30"), "2026-01"),
])
def test_upsert_is_idempotent_by_logical_period(tmp_path, timeframe, dates, expected_key):
    store = Store(str(tmp_path / f"{timeframe}.db"))
    store.upsert_bars("000001", timeframe, "2", [bar(dates[0])])
    store.upsert_bars("000001", timeframe, "2", [bar(dates[1], 12)])

    rows = store.market_bars("000001", timeframe, "2")
    assert len(rows) == 1
    assert rows[0]["trade_date"] == dates[1]
    assert rows[0]["period_key"] == expected_key
    assert rows[0]["close"] == 12


def test_daily_confirmation_is_mirrored_to_generic_period_proof(tmp_path):
    now = datetime(2026, 9, 18, 15, 0, 15, tzinfo=TZ)
    store = Store(str(tmp_path / "confirm.db"), clock=lambda: now)
    store.upsert_trade_calendar([{"trade_date": "2026-09-18", "is_trading_day": True}])
    store.upsert_bars_with_changes(
        "000001", "d", "2", [bar("2026-09-18", source_revision="r1")],
        fetched_at=now, request_started_at=now,
    )

    proof = dict(store.db.execute("SELECT * FROM period_confirmations").fetchone())
    assert (proof["timeframe"], proof["period_key"], proof["source_revision"]) == (
        "d", "2026-09-18", "r1",
    )
    assert proof["request_started_at"] == proof["fetched_at"] == proof["confirmed_at"]


def test_non_daily_confirmation_and_old_response_guard_use_period_key(tmp_path):
    now = datetime(2026, 9, 18, 15, 0, 15, tzinfo=TZ)
    store = Store(str(tmp_path / "period-confirm.db"), clock=lambda: now)
    store.upsert_bars("000001", "30", "2", [bar("2026-09-18 14:30:00", 10)])
    assert store.confirm_period_bars(
        "000001", "30", "2", [bar("2026-09-18 14:30:00", 10)], now, now
    ) == 1
    assert [row["period_key"] for row in store.confirmed_period_bars("000001", "30")] == [
        "2026-09-18 14:30:00"
    ]

    older = now - timedelta(seconds=15)
    count, changed = store.upsert_bars_with_changes(
        "000001", "30", "2", [bar("2026-09-18 14:30:00", 99)],
        fetched_at=older, request_started_at=older,
    )
    assert (count, changed) == (0, None)
    assert store.market_bars("000001", "30", "2")[0]["close"] == 10
