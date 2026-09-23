from copy import deepcopy

import pytest

from app.chan_expansion import build_parent_center_proofs
from app.chan_structure import (
    _candidate_record,
    _commit_parent_centers,
    _merge_level_center_sources,
    _normalize_segment_proof_revisions,
    atomic_pen_units,
)
from tests.chan_fixtures import pens_from_prices


def pen_native_units():
    return atomic_pen_units(pens_from_prices([
        100, 120, 105, 125, 110, 123, 108, 130, 115, 128,
        112, 126, 109,
    ]))


def local_proofs(units, count, *, observed_at=None):
    search = units[:count]
    return build_parent_center_proofs(
        child_level=1,
        units=units,
        required_unit_ids={unit['id'] for unit in units[:9]},
        search_unit_ids=[unit['id'] for unit in search],
        candidate_source='extension_decomposition',
        observed_at=observed_at or search[-1]['confirmed_at'],
        include_rejected=False,
    )


def test_nine_owned_pens_are_required_for_parent_proof():
    units = pen_native_units()
    assert local_proofs(units, 8) == []
    proof, = local_proofs(units, 9)
    assert proof['source_kind'] == 'local_pen_group'
    assert proof['boundary_status'] == 'dynamic'


def test_third_pen_segment_is_dynamic_until_local_reversal_completes_it():
    units = pen_native_units()
    dynamic, = local_proofs(units, 9)
    fixed, = local_proofs(units, 12)
    assert (dynamic['zd'], dynamic['zg']) == (fixed['zd'], fixed['zg']) == (108, 125)
    assert [segment['status'] for segment in dynamic['segments']] == [
        'confirmed', 'confirmed', 'provisional',
    ]
    assert dynamic['segments'][2]['completion_evidence_id'] is None
    assert fixed['boundary_status'] == 'fixed'
    assert all(segment['status'] == 'confirmed' for segment in fixed['segments'])
    assert fixed['segments'][2]['completion_evidence_id'].startswith('local-boundary-')
    assert fixed['evidence_available_at'] >= dynamic['evidence_available_at']


def test_future_pen_evidence_cannot_define_earlier_boundary():
    units = pen_native_units()
    assert local_proofs(units, 12, observed_at=units[8]['confirmed_at']) == []
    assert local_proofs(units, 12)[0]['boundary_status'] == 'fixed'


def test_pairwise_overlap_is_not_a_parent_core():
    units = pen_native_units()
    for part, bounds in zip((units[:3], units[3:6], units[6:9]), ((0, 4), (3, 8), (7, 12))):
        for unit in part:
            unit.update(low=bounds[0], high=bounds[1])
    assert local_proofs(units, 9) == []


def test_movement_or_point_cannot_complete_l1_parent_proof():
    units = pen_native_units()
    arguments = dict(
        child_level=1, units=units,
        required_unit_ids={unit['id'] for unit in units[:9]},
        search_unit_ids=[unit['id'] for unit in units[:12]],
        candidate_source='extension_decomposition',
        observed_at=units[11]['confirmed_at'],
    )
    for obsolete in ('movements', 'boundary_events'):
        with pytest.raises(TypeError):
            build_parent_center_proofs(**arguments, **{obsolete: [{'id': 'obsolete'}]})
    non_pen_units = deepcopy(units)
    non_pen_units[0]['kind'] = 'movement'
    assert local_proofs(non_pen_units, 12) == []


def test_dynamic_and_fixed_parent_revisions_keep_family_and_child_centers():
    units = pen_native_units()
    dynamic = local_proofs(units, 9)
    fixed = local_proofs(units, 12)
    candidates = [
        dict(id='extension:r1', candidate_source='extension_decomposition',
             observed_at=dynamic[0]['evidence_available_at'], child_center_ids=['child-a'], _proofs=dynamic),
        dict(id='expansion:r1', candidate_source='expansion_decomposition',
             observed_at=fixed[0]['evidence_available_at'], child_center_ids=['child-b'], _proofs=fixed),
    ]
    parents, _ = _commit_parent_centers(candidates)
    assert len(parents) == 2
    assert len({item['family_id'] for item in parents}) == 1
    assert parents[0]['boundary_status'] == 'dynamic'
    assert parents[0]['fixed_zd'] is None and not parents[0]['recursive_eligible']
    assert parents[0]['child_center_ids'] == ['child-a']
    assert parents[0]['candidate_source_ids'] == ['extension:r1']
    final = parents[-1]
    assert final['boundary_status'] == 'fixed'
    assert final['child_center_ids'] == ['child-a', 'child-b']
    assert (final['fixed_zd'], final['fixed_zg']) == (final['zd'], final['zg'])
    assert final['recursive_eligible'] is True
    assert final['unit_kind'] == 'segment_proof'
    assert final['core_unit_ids'] == [part['id'] for part in final['decomposition_proof']['segments']]
    assert set(final['formation_modes']) == {'extension_decomposition', 'expansion_decomposition'}


def test_fixed_parent_core_merges_with_regular_core_before_level_analysis():
    units = pen_native_units()
    proofs = local_proofs(units, 12)
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


def test_local_segment_revisions_are_append_only_across_dynamic_and_fixed_candidates():
    units = pen_native_units()
    dynamic = local_proofs(units, 9)
    fixed = local_proofs(units, 12)
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


def test_point_at_third_segment_end_cannot_fix_pen_native_parent():
    units = pen_native_units()[:9]
    assert local_proofs(units, 9)[0]['boundary_status'] == 'dynamic'
    with pytest.raises(TypeError):
        build_parent_center_proofs(
            child_level=1, units=units,
            required_unit_ids={unit['id'] for unit in units},
            search_unit_ids=[unit['id'] for unit in units],
            candidate_source='extension_decomposition',
            observed_at=units[-1]['confirmed_at'],
            boundary_events=[{'id': 'point-family:r1'}],
        )


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
