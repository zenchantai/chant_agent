#!/usr/bin/env python3
"""Compare frozen old/new runs without opening either database for writing."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

PROTECTED = ('market_bars','stock_pool','drawing_objects','watchlist_groups','watchlist_group_members','watchlist_section_order')

def connect(path):
    db = sqlite3.connect(f'file:{quote(str(path.resolve()))}?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    return db

def digest(db,table):
    rows = sorted(json.dumps(list(r),ensure_ascii=False,default=str) for r in db.execute(f'SELECT * FROM {table}'))
    return dict(count=len(rows),sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())

def runs(db):
    return {(r['symbol'],r['timeframe'],r['adjustflag']):dict(r) for r in db.execute('SELECT r.* FROM chan_active_runs a JOIN chan_structure_runs r ON r.id=a.run_id')}

def rows(db,table,run,column='evidence_json'):
    return [json.loads(r[0]) for r in db.execute(f'SELECT {column} FROM {table} WHERE run_id=? ORDER BY rowid',(run,))]

def center_summary(values):
    return [{k:c.get(k) for k in ('id','family_id','level','start_date','end_date','core_start_date','core_end_date','formation_type','formed_at','zd','zg','dd','gg','z_unit_ids','promotion_confirmed_at','boundary_status')}
            for c in values if c.get('active')]

def compare(old_path,new_path):
    old,new=connect(old_path),connect(new_path)
    protected={t:{'old':digest(old,t),'new':digest(new,t)} for t in PROTECTED}
    for item in protected.values(): item['unchanged']=item['old']==item['new']
    old_runs,new_runs=runs(old),runs(new)
    items=[]
    for key in sorted(old_runs):
        before,after=old_runs[key],new_runs[key]
        old_id,new_id=before['id'],after['id']
        point_fields=('point_type','point_date','point_price','status','confirmed_at','center_revision_id')
        points=lambda db,run:[{k:p.get(k) for k in point_fields} for p in rows(db,'chan_point_revisions',run)]
        items.append(dict(symbol=key[0],timeframe=key[1],adjustflag=key[2],old_run_id=old_id,new_run_id=new_id,
            old_definition=before['definition_version'],new_definition=after['definition_version'],
            same_market=before['market_version']==after['market_version'],
            same_pens=rows(old,'chan_pens',old_id,'payload_json')==rows(new,'chan_pens',new_id,'payload_json'),
            old_centers=center_summary(rows(old,'chan_center_revisions',old_id)),new_centers=center_summary(rows(new,'chan_center_revisions',new_id)),
            old_relations=rows(old,'chan_relations',old_id),new_relations=rows(new,'chan_relations',new_id),
            old_points=points(old,old_id),new_points=points(new,new_id)))
    old.close();new.close()
    return dict(protected=protected,matrix=items,protected_unchanged=all(i['unchanged'] for i in protected.values()),
                same_market_and_pens=all(i['same_market'] and i['same_pens'] for i in items))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    args=p.parse_args();report=compare(args.baseline,args.candidate)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='matrix' and k!='protected'},ensure_ascii=False))
    raise SystemExit(0 if report['protected_unchanged'] and report['same_market_and_pens'] else 1)
