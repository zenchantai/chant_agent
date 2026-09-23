from app.store import Store


def test_fresh_v25_store_never_creates_legacy_structure_or_analysis_tables(tmp_path):
    store = Store(str(tmp_path / "v25.db"))
    tables = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({name for name in tables if name.startswith("period_")} - {"period_confirmations"})
    assert not {
        "active_period_structure_runs", "structure_overrides", "structure_override_events",
        "analyses", "journals", "sync_runs", "market_data_conflicts", "market_coverage",
        "market_gap_tasks", "period_segments",
    } & tables


def test_deleting_run_cascades_all_normalized_detail_rows(tmp_path):
    store = Store(str(tmp_path / "cascade.db"))
    tables = [
        "chan_processed_bars", "chan_fractals", "chan_pens", "chan_components",
        "chan_center_revisions", "chan_promotion_candidates", "chan_segment_proofs",
        "chan_relations", "chan_issues",
    ]
    store.db.execute("PRAGMA foreign_keys=ON")
    store.db.execute("""INSERT INTO chan_structure_runs
        (symbol,timeframe,adjustflag,definition_version,calculator_fingerprint,market_version,
         structure_version,status,max_level,started_at,meta_json)
        VALUES('s','d','2','v25','f','m','s','success',0,'now','{}')""")
    run_id = store.db.execute("SELECT id FROM chan_structure_runs").fetchone()[0]
    store.db.execute(
        "INSERT INTO chan_processed_bars(run_id,id,ordinal,start_date,end_date,payload_json) VALUES(?,?,?,?,?,?)",
        (run_id, "b", 0, "2026-01-01", "2026-01-01", "{}"),
    )
    store.db.commit()
    store.db.execute("DELETE FROM chan_structure_runs WHERE id=?", (run_id,))
    store.db.commit()
    assert all(store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in tables)
