#!/usr/bin/env python3
"""Review and canonicalize logical market-period identities.

The command is intentionally dry-run by default.  Run it only after the
service is stopped and the SQLite WAL trio has been backed up.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/chant_agent.db")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-delete-ids", default="",
                        help="Comma-separated IDs copied from the dry-run report")
    args = parser.parse_args()
    path = Path(args.db)
    store = Store(str(path))
    try:
        report = store.period_key_migration_report()
        if not args.execute:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if not args.confirm_delete_ids:
            raise SystemExit("--execute requires --confirm-delete-ids from a reviewed dry-run")
        ids = [int(item) for item in args.confirm_delete_ids.split(",") if item.strip()]
        result = store.canonicalize_period_bars(ids)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
