from copy import deepcopy

from app.chan_expansion import decomposition_proofs
from app.chan_structure import atomic_pen_units
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
    return units,movements,centers


def test_contact_without_three_legal_movements_never_gets_geometry():
    units,moves,centers = proof_input()
    assert not decomposition_proofs(moves[:2],units,{'p1','p7'},1,centers)
    invalid = deepcopy(moves)
    invalid[1]['status'] = 'provisional'
    assert not decomposition_proofs(invalid,units,{'p1','p7'},1,centers)
    invalid = deepcopy(moves)
    invalid[1]['source_unit_ids'].append('p2')
    assert not decomposition_proofs(invalid,units,{'p1','p7'},1,centers)


def test_third_segment_can_define_dynamic_geometry_and_later_fix_it():
    units,moves,centers = proof_input()
    proofs = decomposition_proofs(moves,units,{'p1','p7'},1,centers)
    assert proofs and all(p['boundary_status']=='dynamic' for p in proofs)
    assert (proofs[0]['zd'],proofs[0]['zg']) == (3,12)
    assert all(s['status']=='confirmed' for s in proofs[0]['segments'][:2])
    moves[2].update(status='confirmed',confirmed_at=units[-1]['confirmed_at'])
    fixed = decomposition_proofs(moves,units,{'p1','p7'},1,centers)
    assert fixed[:-1] == proofs[:-1]
    assert fixed[-1]['boundary_status']=='fixed'
    assert fixed[-1]['segments'][2]['completion_evidence_id']=='point2'


def test_future_center_evidence_cannot_define_earlier_dynamic_boundary():
    units,moves,centers = proof_input()
    centers[-1]['revision_at'] = units[-1]['confirmed_at']
    proofs = decomposition_proofs(moves,units,{'p1','p7'},1,centers)
    assert proofs
    assert all(p['available_at'] >= centers[-1]['revision_at'] for p in proofs)
    centers[-1]['revision_at'] = '2099-01-01'
    assert not decomposition_proofs(moves,units,{'p1','p7'},1,centers)


def test_nine_pens_or_pairwise_overlap_are_not_a_boundary_certificate():
    units,moves,centers = proof_input()
    moves[2]['center_revision_ids'] = []
    assert not decomposition_proofs(moves,units,set(),1,centers)
    # A/B and B/C intersection does not establish A/B/C intersection.
    units,moves,centers = proof_input()
    for part, bounds in zip((units[:3],units[3:6],units[6:]), ((0,4),(3,8),(7,12))):
        for u in part: u.update(low=bounds[0],high=bounds[1])
    assert not decomposition_proofs(moves,units,set(),1,centers)


def test_internal_certificate_does_not_require_global_movement_completion():
    from app.chan_expansion import internal_decomposition_proofs
    units, _, centers = proof_input()
    for c in centers: c['family_id'] = c['id']
    points = [dict(id=f'point{i}',family_id=f'point{i}',level=1,status='confirmed',
                   point_type='consolidation_divergence_sell' if i%2==0 else 'consolidation_divergence_buy',
                   source_unit_id=units[end]['id'],point_date=units[end]['end_date'],confirmed_at=units[end]['confirmed_at'])
              for i,end in enumerate((2,5))]
    proofs = internal_decomposition_proofs(units,centers,points,{'p0','p7'},1)
    assert proofs and proofs[0]['boundary_status']=='dynamic'
    assert all(p['construction_scope']=='internal' for p in proofs[0]['segments'])
    assert proofs[0]['segments'][0]['source_unit_ids'][0]=='p0'
    # A third, independent completion point fixes geometry without recursion.
    points.append(dict(id='point2',family_id='point2',level=1,status='confirmed',
                       point_type='consolidation_divergence_sell',source_unit_id='p8',
                       point_date=units[8]['end_date'],confirmed_at=units[10]['confirmed_at']))
    fixed = internal_decomposition_proofs(units,centers,points,{'p0','p7'},1)
    assert fixed[-1]['boundary_status']=='fixed'
    assert fixed[-1]['segments'][-1]['source_unit_ids'] == ['p6','p7','p8']
