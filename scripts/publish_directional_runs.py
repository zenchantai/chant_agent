#!/usr/bin/env python3
"""Publish a validated frozen run matrix. The serving process must be stopped first."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.store import Store
from app.period_structure import calculate_calculator_fingerprint
from app.rules import PERIOD_DEFINITION_VERSION
from compare_directional_runs import connect,digest,runs,PROTECTED


def publish(candidate:Path,target:Path,output:Path):
    if candidate.resolve()==target.resolve():
        raise ValueError('候选与目标数据库不能相同')
    output.mkdir(parents=True,exist_ok=False)
    source=connect(candidate); current=connect(target)
    matrix=runs(source); previous=runs(current)
    if matrix.keys()!=previous.keys():
        raise ValueError('发布矩阵与目标活动矩阵不一致')
    if digest(source,'market_bars') != digest(current,'market_bars'):
        raise ValueError('冻结行情已变化，必须重新计算和验收')
    fingerprint=calculate_calculator_fingerprint(ROOT)
    if any(r['definition_version']!=PERIOD_DEFINITION_VERSION or r['calculator_fingerprint']!=fingerprint for r in matrix.values()):
        raise ValueError('候选运行与当前代码指纹不匹配')
    before={t:digest(current,t) for t in PROTECTED}
    with sqlite3.connect(output/'before.db') as backup:
        current.backup(backup)
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    current.close();source.close()
    manifest=dict(created_at=datetime.now(timezone.utc).isoformat(),target=str(target.resolve()),
        candidate=str(candidate.resolve()),definition_version=PERIOD_DEFINITION_VERSION,calculator_fingerprint=fingerprint,
        old_runs=list(previous.values()),new_run_ids=[],protected_before=before,status='staging')
    write=lambda:(output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    write()
    candidate_store=Store(str(candidate)); target_store=Store(str(target))
    for key,run in sorted(matrix.items()):
        snapshot=candidate_store.load_chan_structure(run['id'])
        identifier=target_store.replace_chan_structure(*key,snapshot,run['market_version'],activate=False)
        manifest['new_run_ids'].append(identifier);write()
    target_store.activate_chan_runs(manifest['new_run_ids'],definition_version=PERIOD_DEFINITION_VERSION,calculator_fingerprint=fingerprint)
    manifest['protected_after']={t:digest(target_store.db,t) for t in PROTECTED}
    manifest['protected_unchanged']=manifest['protected_after']==before
    manifest['integrity_check']=target_store.db.execute('PRAGMA integrity_check').fetchone()[0]
    manifest['foreign_key_errors']=[list(r) for r in target_store.db.execute('PRAGMA foreign_key_check')]
    manifest['status']='activated';write()
    candidate_store.db.close();target_store.db.close()
    if not manifest['protected_unchanged'] or manifest['integrity_check']!='ok' or manifest['foreign_key_errors']:
        raise RuntimeError('切换后数据一致性检查失败')
    return manifest


def rollback(manifest_path:Path):
    manifest=json.loads(manifest_path.read_text())
    db=sqlite3.connect(manifest['target'])
    with db:
        for run in manifest['old_runs']:
            row=db.execute('SELECT definition_version,calculator_fingerprint,status FROM chan_structure_runs WHERE id=?',(run['id'],)).fetchone()
            if row!=(run['definition_version'],run['calculator_fingerprint'],'success'):
                raise ValueError('回滚运行缺失或不匹配')
        for run in manifest['old_runs']:
            db.execute('UPDATE chan_active_runs SET run_id=?,activated_at=? WHERE symbol=? AND timeframe=? AND adjustflag=?',
                       (run['id'],datetime.now(timezone.utc).isoformat(),run['symbol'],run['timeframe'],run['adjustflag']))
    db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path);parser.add_argument('--target',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--rollback-manifest',type=Path)
    args=parser.parse_args()
    if args.rollback_manifest:
        rollback(args.rollback_manifest);print('活动指针已回滚；还需恢复匹配的代码与前端。')
    elif args.candidate and args.target and args.output:
        result=publish(args.candidate,args.target,args.output)
        print(json.dumps({k:result[k] for k in ('status','new_run_ids','protected_unchanged','integrity_check')},ensure_ascii=False))
    else:
        parser.error('发布必须同时提供 --candidate --target --output，或明确提供 --rollback-manifest')
