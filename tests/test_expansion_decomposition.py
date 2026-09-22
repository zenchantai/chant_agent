from copy import deepcopy

from app.chan_expansion import build_parent_center_proofs, decomposition_proofs
from app.chan_structure import atomic_pen_units
from app.chan_structure import (
    _candidate_record,
    _commit_parent_centers,
    _merge_level_center_sources,
    _normalize_segment_proof_revisions,
)
from tests.chan_fixtures import pens_from_prices


def proof_input():
    units = atomic_pen_units(pens_from_prices([0,10,4,12,6,11,3,9,5,13,8,14]))
    movements, centers = [], []
    for index, part in enumerate((units[:3],units[3:6],units[6:])):
        cid = f'c{index}'
        stamp = part[2]['confirmed_at']
        centers.append(dict(id=cid, level=1, formation_stage='directional',
                            formed_at=stamp,revision_at=stamp,owned_unit_ids=[u['id'] for u in part[:3]]))
        movements.append(dict(id=f'm{index}',level=1,classification='consolidation',status='confirmed' if index<2 else 'provisional',
            direction='down' if index==1 else 'up',start_date=part[0]['start_date'],end_date=part[-1]['end_date'],
            start_price=part[0]['start_price'],end_price=part[-1]['end_price'],source_unit_ids=[u['id'] for u in part],
            confirmed_at=part[-1]['confirmed_at'] if index<2 else None,center_revision_ids=[cid],end_point_id=f'point{index}'))
    points = [dict(
        id=f'point{i}:r1', family_id=f'point{i}', level=1, status='confirmed',
        point_type='consolidation_divergence_sell' if i % 2 == 0 else 'consolidation_divergence_buy',
        source_unit_id=part[-1]['id'], point_date=part[-1]['end_date'],
        confirmed_at=part[-1]['confirmed_at'], continuous_range_id=0,
        sequence_id=0, structure_sequence_id='',
    ) for i, part in enumerate((units[:3], units[3:6], units[6:]))]
    return units,movements,centers,points


def test_contact_without_three_legal_movements_never_gets_geometry():
    units,moves,centers,points = proof_input()
    assert not decomposition_proofs(moves[:2],units,{'p1','p7'},1,centers,points)
    invalid = deepcopy(moves)
    invalid[1]['status'] = 'provisional'
    assert not decomposition_proofs(invalid,units,{'p1','p7'},1,centers,points)
    invalid = deepcopy(moves)
    invalid[1]['source_unit_ids'].append('p2')
    assert not decomposition_proofs(invalid,units,{'p1','p7'},1,centers,points)


def test_third_segment_can_define_dynamic_geometry_and_later_fix_it():
    units,moves,centers,points = proof_input()
    proofs = decomposition_proofs(moves,units,{'p1','p7'},1,centers,points)
    assert proofs and all(p['boundary_status']=='dynamic' for p in proofs)
    assert (proofs[0]['zd'],proofs[0]['zg']) == (3,12)
    assert all(s['status']=='confirmed' for s in proofs[0]['segments'][:2])
    moves[2].update(status='confirmed',confirmed_at=units[-1]['confirmed_at'])
    fixed = decomposition_proofs(moves,units,{'p1','p7'},1,centers,points)
    assert fixed[:-1] == proofs[:-1]
    assert fixed[-1]['boundary_status']=='fixed'
    assert fixed[-1]['segments'][2]['completion_evidence_id']=='point2'


def test_future_center_evidence_cannot_define_earlier_dynamic_boundary():
    units,moves,centers,points = proof_input()
    centers[-1]['revision_at'] = units[-1]['confirmed_at']
    proofs = decomposition_proofs(moves,units,{'p1','p7'},1,centers,points)
    assert proofs
    assert all(p['available_at'] >= centers[-1]['revision_at'] for p in proofs)
    centers[-1]['revision_at'] = '2099-01-01'
    assert not decomposition_proofs(moves,units,{'p1','p7'},1,centers,points)


def test_nine_pens_or_pairwise_overlap_are_not_a_boundary_certificate():
    units,moves,centers,points = proof_input()
    moves[2]['center_revision_ids'] = []
    assert not decomposition_proofs(moves,units,set(),1,centers,points)
    # A/B and B/C intersection does not establish A/B/C intersection.
    units,moves,centers,points = proof_input()
    for part, bounds in zip((units[:3],units[3:6],units[6:]), ((0,4),(3,8),(7,12))):
        for u in part: u.update(low=bounds[0],high=bounds[1])
    assert not decomposition_proofs(moves,units,set(),1,centers,points)


def test_parent_proof_never_repartitions_units_without_canonical_movements():
    units, _, centers, points = proof_input()
    assert not decomposition_proofs([], units, {'p0', 'p7'}, 1, centers, points)


def test_dynamic_and_fixed_parent_revisions_keep_nullable_fixed_core_and_independent_family():
    units, moves, centers, points = proof_input()
    dynamic = decomposition_proofs(moves, units, {'p1', 'p7'}, 1, centers, points)
    moves[2].update(status='confirmed', confirmed_at=units[-1]['confirmed_at'])
    fixed = decomposition_proofs(moves, units, {'p1', 'p7'}, 1, centers, points)
    candidates = [
        dict(id='extension:r1', candidate_source='extension_decomposition',
             observed_at=dynamic[0]['evidence_available_at'], _proofs=dynamic),
        dict(id='expansion:r1', candidate_source='expansion_decomposition',
             observed_at=fixed[-1]['evidence_available_at'], _proofs=fixed),
    ]
    parents, _ = _commit_parent_centers(candidates)
    assert parents
    assert len({item['family_id'] for item in parents}) == 1
    assert parents[0]['family_id'] not in {center['id'] for center in centers}
    assert any(item['boundary_status'] == 'dynamic' and item['fixed_zd'] is None and not item['recursive_eligible'] for item in parents)
    assert parents[0]['formation_modes'] == ['extension_decomposition']
    assert parents[0]['candidate_source_ids'] == ['extension:r1']
    final = parents[-1]
    assert final['boundary_status'] == 'fixed'
    assert (final['fixed_zd'], final['fixed_zg']) == (final['zd'], final['zg'])
    assert final['recursive_eligible'] is True
    assert final['unit_kind'] == 'segment_proof'
    assert final['core_unit_ids'] == [part['id'] for part in final['decomposition_proof']['segments']]
    assert set(final['formation_modes']) == {'extension_decomposition', 'expansion_decomposition'}


def test_fixed_parent_core_merges_with_regular_core_before_level_analysis():
    units, moves, centers, points = proof_input()
    moves[2].update(status='confirmed', confirmed_at=units[-1]['confirmed_at'])
    proofs = decomposition_proofs(moves, units, {'p1', 'p7'}, 1, centers, points)
    parents, _ = _commit_parent_centers([
        dict(id='candidate:r1', candidate_source='extension_decomposition', _proofs=proofs),
    ])
    fixed = parents[-1]
    regular = deepcopy(fixed)
    regular.pop('decomposition_proof')
    regular.update(
        id=f"{fixed['family_id']}:r1", revision_no=1, previous_revision_id=None,
        boundary_status=None, promotion_confirmed_at=None,
        formation_modes=['directional_core'], candidate_source_ids=[],
    )
    regular['_history'] = [{key: value for key, value in regular.items() if key != '_history'}]
    merged, display_only = _merge_level_center_sources([regular], parents)
    assert display_only == []
    assert len(merged) == 1
    assert merged[0]['family_id'] == fixed['family_id']
    assert merged[0]['boundary_status'] == 'fixed'
    assert set(merged[0]['formation_modes']) == {'directional_core', 'extension_decomposition'}
    assert merged[0]['recursive_eligible'] is True


def pen_native_units():
    return atomic_pen_units(pens_from_prices([
        100, 120, 105, 125, 110, 123, 108, 130, 115, 128,
        112, 126, 109,
    ]))


def local_proofs(units, count, points=None):
    search = units[:count]
    return build_parent_center_proofs(
        child_level=1,
        units=units,
        child_centers=[],
        boundary_events=points or [],
        movements=[],
        required_unit_ids={unit['id'] for unit in units[:9]},
        search_unit_ids=[unit['id'] for unit in search],
        candidate_source='extension_decomposition',
        observed_at=search[-1]['confirmed_at'],
        include_rejected=False,
    )


def test_local_segment_revisions_are_append_only_across_dynamic_and_fixed_candidates():
    units = pen_native_units()
    dynamic = local_proofs(units, 9)
    fixed = local_proofs(units, 12)
    assert dynamic[0]['boundary_status'] == 'dynamic'
    assert fixed[0]['boundary_status'] == 'fixed'
    unit_by_id = {unit['id']: unit for unit in units}
    candidates = [
        _candidate_record(
            family_id='candidate-family', revision_no=index,
            source='extension_decomposition', child_level=1,
            source_entity_ids=['center:r1'],
            required_unit_ids=[unit['id'] for unit in units[:9]],
            search_unit_ids=[unit['id'] for unit in units[:count]],
            observed_at=units[count - 1]['confirmed_at'], proofs=proofs,
            unit_by_id=unit_by_id,
        )
        for index, (count, proofs) in enumerate(((9, dynamic), (12, fixed)), 1)
    ]
    _normalize_segment_proof_revisions(candidates)
    revisions = [
        segment for candidate in candidates
        for segment in candidate['segment_proof_revisions']
    ]
    states_by_id = {}
    for segment in revisions:
        state = (
            tuple(segment['source_unit_ids']), segment['status'],
            segment['completion_evidence_id'], segment['evidence_available_at'],
        )
        assert states_by_id.setdefault(segment['id'], state) == state
    for family_id in {segment['family_id'] for segment in revisions}:
        family = {
            segment['id']: segment for segment in revisions
            if segment['family_id'] == family_id
        }
        ordered = sorted(family.values(), key=lambda segment: segment['revision_no'])
        assert [segment['revision_no'] for segment in ordered] == list(range(1, len(ordered) + 1))
        assert [segment['previous_revision_id'] for segment in ordered] == [
            None, *[segment['id'] for segment in ordered[:-1]],
        ]


def test_confirmed_point_at_third_segment_end_fixes_pen_native_parent():
    units = pen_native_units()[:9]
    dynamic = local_proofs(units, 9)
    assert dynamic[0]['boundary_status'] == 'dynamic'
    endpoint = units[-1]
    point = {
        'id': 'point-family:r1', 'family_id': 'point-family', 'level': 1,
        'status': 'confirmed', 'source_unit_id': endpoint['id'],
        'point_date': endpoint['end_date'],
        'confirmed_at': endpoint['confirmed_at'],
    }
    fixed = local_proofs(units, 9, [point])
    assert fixed[0]['boundary_status'] == 'fixed'
    assert fixed[0]['segments'][2]['completion_evidence_id'] == point['id']


def test_fixed_parent_cannot_predate_the_candidate_observation():
    units = pen_native_units()
    proof = deepcopy(local_proofs(units, 12)[0])
    observed_at = '2099-01-01'
    parents, _ = _commit_parent_centers([{
        'id': 'candidate:r1',
        'candidate_source': 'expansion_decomposition',
        'observed_at': observed_at,
        '_proofs': [proof],
    }])
    assert parents[0]['formed_at'] == observed_at
    assert parents[0]['revision_at'] == observed_at
    assert parents[0]['promotion_confirmed_at'] == observed_at
