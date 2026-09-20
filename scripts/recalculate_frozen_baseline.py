#!/usr/bin/env python3
"""Run archived code against the candidate's exact frozen analysis ranges."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--code',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
p.add_argument('--target',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
a=p.parse_args()
sys.path.insert(0,str(a.code.resolve()))
from app.store import Store
from app.period_structure import analyze_period_ranges,calculate_calculator_fingerprint

source=sqlite3.connect(f'file:{a.source.resolve()}?mode=ro',uri=True)
source.row_factory=sqlite3.Row
target=Store(str(a.target));items=[]
fingerprint=calculate_calculator_fingerprint(a.code.resolve())
for row in source.execute('SELECT r.* FROM chan_active_runs a JOIN chan_structure_runs r ON a.run_id=r.id ORDER BY r.symbol,r.timeframe'):
    meta=json.loads(row['meta_json']);symbol,period,adjust=row['symbol'],row['timeframe'],row['adjustflag']
    data=target.confirmed_daily_bars(symbol,adjust) if period=='d' else target.market_bars(symbol,period,adjust,'0000-01-01')
    result=analyze_period_ranges(data,symbol,period,meta['coverage']['continuous_ranges'],fingerprint)
    result['meta'].update(market_version=row['market_version'],coverage=meta['coverage'],adjustflag=adjust,
                          source_cutoff=meta['source_cutoff'],preview=False,persisted=True,
                          comparison_input='candidate_frozen_analysis_ranges')
    run=target.replace_chan_structure(symbol,period,adjust,result,row['market_version'])
    items.append(dict(symbol=symbol,timeframe=period,run_id=run,pens=len(result['structure']['pens'])))
a.report.write_text(json.dumps(dict(calculator_fingerprint=fingerprint,comparison_input='same_frozen_rows_and_analysis_ranges',items=items),ensure_ascii=False,indent=2))
target.db.close();source.close()
print(f'Archived baseline recomputed: {len(items)} runs')
