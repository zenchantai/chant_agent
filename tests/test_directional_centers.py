from copy import deepcopy

import pytest

from app.chan_direction import breakout_context
from app.chan_structure import (atomic_pen_units, build_level_centers, build_structure_hierarchy,
                                classify_center_relation, validate_structure)
from tests.chan_fixtures import pens_from_prices
from tests.test_z_wave_market import FIXTURES


@pytest.mark.parametrize('mirror', [False, True])
def test_breakout_direction_precedes_core_and_uses_confirmation_time(mirror):
    prices = [1,10,6,15,8]
    if mirror:
        prices = [20-x for x in prices]
    pens = pens_from_prices(prices)
    pens[2]['confirmed_at'] = '2026-01-09'
    context = breakout_context(pens)
    assert context['process_direction'] == ('down' if mirror else 'up')
    center = build_level_centers(pens, 1)[0][0]
    assert center['core_unit_ids'] == ['p1','p2','p3']
    assert center['z_unit_ids'] == ['p1','p3']
    assert center['formation_type'] == ('rebound' if mirror else 'pullback')
    assert center['formed_at'] == '2026-01-09'
    assert center['direction_established_at'] == '2026-01-09'


@pytest.mark.parametrize('prices', [[1,10,1,15], [1,10,6,10], [1,10,0,15]])
def test_equal_or_insufficient_breakout_does_not_establish_direction(prices):
    assert breakout_context(pens_from_prices(prices)) is None


def test_origin_normalization_is_an_append_only_event_and_truncation_is_named():
    pens = pens_from_prices([1,10,6,15,8])
    before = build_level_centers(pens[:3], 1)[0][0]
    after = build_level_centers(pens, 1)[0][0]
    assert before['formation_stage'] == 'origin_overlap'
    assert not before['recursive_eligible']
    assert after['_history'][0] == before['_history'][0]
    assert after['normalization_event']['previous_revision_id'] == before['id']
    truncated = build_level_centers(pens[:3], 1, origin_kind='truncated_left')[0][0]
    assert truncated['formation_stage'] == 'boundary_candidate'
    assert not truncated['recursive_eligible']


def test_identical_local_triple_has_context_sensitive_ownership():
    units = atomic_pen_units(pens_from_prices([1,10,6,15,8,20,12]))
    up = {'process_direction':'up','start_index':0,'available_at':units[0]['confirmed_at']}
    down = {**up,'process_direction':'down'}
    a = build_level_centers(deepcopy(units),1,direction_context=up)[0][0]
    b = build_level_centers(deepcopy(units),1,direction_context=down)[0][0]
    assert a['core_unit_ids'] == ['p1','p2','p3']
    assert b['core_unit_ids'] == ['p2','p3','p4']
    assert a['z_unit_ids'] != b['z_unit_ids']


@pytest.mark.parametrize('delta,expected', [(-2e-9,'expansion_up'), (0,'expansion_up'), (2e-9,'newborn_up')])
def test_contact_uses_closed_interval_with_shared_tolerance(delta, expected):
    left = dict(level=1, continuous_range_id=0, sequence_id=0, structure_sequence_id='s',zd=8,zg=10,dd=6,gg=15)
    right = {**left,'zd':18,'zg':20,'dd':15+delta,'gg':25}
    assert classify_center_relation(left,right) == expected


def test_boundary_touch_z_extends_but_single_point_initial_core_does_not_form():
    pens = pens_from_prices([1,10,6,15,8,20,10])
    c = build_structure_hierarchy(pens)['centers'][0]
    assert c['z_unit_ids'] == ['p1','p3','p5']
    assert c['touch_unit_ids'] == ['p5']
    assert not c['retest_unit_ids']  # Existing strict third-point convention.
    assert (c['z_high_min'],c['z_low_max']) == (10,10)
    touching = build_level_centers(pens_from_prices([0,10,5,15,10]),1)[0]
    assert not any(c['formation_stage']=='directional' for c in touching)


def test_sse_2015_core_contact_and_evidence_date_regression():
    pens = [p for p in FIXTURES['1A0001']['pens'] if p['end_date'] <= '2015-12-31']
    result = build_structure_hierarchy(pens)
    assert validate_structure({'pens':pens,**result}) == []
    first, second = result['centers'][:2]
    by_id = {p['id']:p for p in pens}
    assert [by_id[i]['start_date'] for i in first['core_unit_ids']] == ['2015-04-28','2015-05-08','2015-06-12']
    assert (first['zd'],first['zg'],first['dd']) == (4099.042,4572.391,3373.54)
    assert first['formation_type'] == 'pullback'
    assert [by_id[i]['start_date'] for i in second['core_unit_ids']] == ['2015-08-26','2015-09-09','2015-09-29']
    assert second['formed_at'] == '2015-10-29'
    event = next(r for r in result['relations'] if r.get('expansion_status')=='confirmed')
    assert event['confirmed_at'] == '2015-10-29'
    assert event['evidence']['contact_interval'] == [3373.54,3457.517]
    assert event['boundary_status'] == 'unresolved'
    assert not any(c['level']>1 for c in result['centers'])


def test_full_crossing_departure_waits_for_first_retest_and_cannot_stall_forever():
    # The upward unit crosses the whole [8,10] core; its down retest stays above.
    pens = pens_from_prices([1,10,6,15,8,20,9,18,7,17,11])
    before = build_structure_hierarchy(pens[:-1])
    old = before['centers'][0]
    assert old['departure_unit_ids'] == ['p8']
    assert old['z_unit_ids'] == ['p1','p3','p5','p7']
    assert old['revision_at'] == pens[8]['confirmed_at']
    after = build_structure_hierarchy(pens)
    center = after['centers'][0]
    assert center['status']=='broken'
    assert center['retest_unit_ids']==['p9']
    assert center['departure_unit_ids']==['p8','p9']
    assert not set(center['departure_unit_ids']) & set(center['owned_unit_ids'])
    assert validate_structure({'pens':pens,**after}) == []


def test_same_time_direction_priority_and_internal_breakout_do_not_retype_center():
    from app.chan_direction import select_context
    units = pens_from_prices([1,10,6,15,8,20,9,18,7])
    bootstrap = breakout_context(units)
    boundary = {**bootstrap,'reason':'confirmed_movement_boundary','process_direction':'down'}
    broken = {**bootstrap,'reason':'confirmed_departure_retest'}
    assert select_context(units,evidence=[broken,boundary]) == boundary
    result = build_level_centers(units,1)[0][0]
    assert result['formation_type']=='pullback'
    assert any(e['process_direction']=='down' for e in result['successor_direction_evidence'])
    assert all(e['available_at'] <= result['revision_at'] for e in result['successor_direction_evidence'])


def test_week_month_coverage_uses_latest_retained_period_date():
    from app.coverage import validate_coverage
    for timeframe, dates in [('w',['2026-09-16','2026-09-17','2026-09-18']),('m',['2026-09-10','2026-09-16','2026-09-18'])]:
        rows = [dict(trade_date=d,open=10,high=12,low=9,close=11) for d in dates]
        for ordered in (rows,list(reversed(rows))):
            coverage=validate_coverage(ordered,timeframe,dates)
            assert coverage['continuous_ranges'][-1]['end_date']=='2026-09-18'


def test_expansion_event_identity_dates_and_contact_do_not_move_with_extension():
    pens=pens_from_prices([1,10,6,15,8,20,12,25,18,30,19,28,17,35])
    old=None
    for count in range(8,len(pens)+1):
        events=[r for r in build_structure_hierarchy(pens[:count])['relations'] if r.get('expansion_status')=='confirmed']
        if not events: continue
        event=events[0]
        value={k:event[k] for k in ('id','from_id','to_id','start_date','end_date','confirmed_at','evidence')}
        if old: assert value==old
        old=value
    assert old is not None
