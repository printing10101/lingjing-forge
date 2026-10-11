from dataclasses import replace
import json

import numpy as np
import pytest
from scipy.interpolate import BSpline

from model import Requirement, audit, candidates, make_atlas, refined, solve
from runner import blind_choices, choose, layout_svg, run
from step_io import roundtrip
from tasks import public_geometry, task_set
from diagnostics import save as save_diagnostic
from baselines import priority_group_replay, save as save_baselines


@pytest.mark.parametrize('index', range(10))
def test_initial_atlas_seams_boundaries_and_sharp_jets(index):
    atlas = make_atlas(index)
    zero = Requirement(0., False, False, False, True, 'locality', 0)
    assert audit(atlas, atlas, zero, ())['passed']
    for i, j in atlas.sharp_seams:
        left = atlas.tiles[i].point(1, .37, 1, 0)[2]/atlas.tiles[i].patch.length
        right = atlas.tiles[j].point(0, .37, 1, 0)[2]/atlas.tiles[j].patch.length
        assert right-left == pytest.approx(.12, abs=1e-12)


def test_topology_families_and_actual_hole():
    atlases = [make_atlas(i) for i in range(10)]
    assert [sum(a.family==f for a in atlases) for f in ('strip','grid','ring_with_hole')] == [4,3,3]
    ring = atlases[-1]
    assert (1,1) not in {t.cell for t in ring.tiles}
    assert len(ring.seams()) == 8 and len(ring.outside_edges()) == 16
    assert len(atlases[6].tiles) == 9


@pytest.mark.parametrize('action', ['knots_u','knots_v','knots_uv','knots_uv_dense','degree_uv8'])
def test_native_refinement_preserves_all_geometry(action):
    tile = make_atlas(6).tiles[4]
    new = refined(tile, action)
    for u in np.linspace(0,1,13):
        for v in (.12,.5,.83):
            assert np.allclose(tile.point(u,v), new.point(u,v), atol=1e-11, rtol=0)
    assert tile.patch.poles.shape == (4,4,3)


def test_degree_obstruction_is_not_global_infeasibility():
    atlas = make_atlas(0)
    req = Requirement(1.,True,False,False,True,'locality',105)
    for action in ('local','knots_u','knots_v','knots_uv'):
        edited, record = solve(atlas,req,action,(atlas.target,))
        assert edited is None and not record['feasible']
        assert record['certificate_null_error'] < 1e-8
        assert abs(record['certificate_rhs']) > 1e-4
    edited, record = solve(atlas,req,'degree_uv8',(atlas.target,))
    assert record['feasible'] and audit(atlas,edited,req,(atlas.target,))['passed']


def test_datum_through_target_is_real_conflict():
    atlas = make_atlas(3)
    assert atlas.datum_parameter == .5
    req = Requirement(1.,True,False,False,True,'locality',105)
    for action,support in candidates(atlas,req):
        edited, record = solve(atlas,req,action,support)
        assert edited is None and not record['feasible']
        assert record['certificate_null_error'] < 1e-7


def test_independent_audit_detects_interior_section_corruption():
    atlas = make_atlas(0)
    req = Requirement(1.,True,False,False,True,'locality',105)
    edited, _ = solve(atlas,req,'degree_uv8',(atlas.target,))
    assert audit(atlas,edited,req,(atlas.target,))['passed']
    edited.tiles[atlas.target].patch.poles[4,4,2] += .1
    checked = audit(atlas,edited,req,(atlas.target,))
    assert not checked['passed'] and checked['datum_position_bound'] > 1e-4


def test_independent_audit_detects_frozen_face_and_sharp_corruption():
    atlas = make_atlas(1)
    req = Requirement(1.,True,False,False,False,'locality',105)
    edited, record = solve(atlas,req,'knots_u',(atlas.target,))
    assert record['feasible']
    edited.tiles[atlas.target].patch.poles[0,1,2] += .2
    checked = audit(atlas,edited,req,(atlas.target,))
    assert not checked['passed'] and checked['sharp_original_jet_bound'] > .01
    # A second copy avoids mutating the baseline through shared frozen Tile objects.
    edited = replace(atlas, tiles=[replace(t,patch=replace(t.patch,poles=t.patch.poles.copy())) for t in atlas.tiles])
    edited.tiles[atlas.interface].patch.poles[1,1,2] += .2
    assert audit(atlas,edited,req,(atlas.target,))['frozen_pole_bound'] > .1


def test_permissions_budgets_and_invalid_actions():
    atlas = make_atlas(0)
    req = Requirement(1.,False,False,False,False,'locality',0)
    assert list(candidates(atlas,req)) == [('local',(atlas.target,))]
    with pytest.raises(ValueError,match='UNAUTHORIZED'):
        solve(atlas,req,'knots_u',(atlas.target,))
    with pytest.raises(ValueError):
        solve(atlas,req,'anything',(atlas.target,))
    limited = replace(req,allow_refine=True)
    edited, record = solve(atlas,limited,'knots_u',(atlas.target,))
    assert edited is None and record['status']=='representation_budget_exceeded'
    locked = replace(req,allow_expand=True,lock_interface=True)
    with pytest.raises(ValueError,match='INVALID_SUPPORT'):
        solve(atlas,locked,'expand_0_1',(0,1))
    with pytest.raises(ValueError):
        refined(atlas.tiles[0],'knots_unknown')
    with pytest.raises(ValueError):
        make_atlas(10)
    with pytest.raises(ValueError):
        replace(req,max_added_poles=-1)


def test_three_task_intents_and_public_metadata_have_no_labels():
    tasks = task_set()
    assert len(tasks)==30 and len({t['input_text'] for t in tasks})==30
    for i in range(10):
        group = tasks[3*i:3*i+3]
        assert len({json.dumps(t['oracle_requirement'],sort_keys=True) for t in group})==3
        assert all(t['independent_human_review']=='pending' for t in group)
        public = public_geometry(make_atlas(i))
        assert 'oracle_requirement' not in public and 'expected_action' not in public
        assert public['datum']['parameter_value'] == make_atlas(i).datum_parameter


def test_ring_step_roundtrip_preserves_roles_and_seams(tmp_path):
    atlas = make_atlas(8)
    restored, check = roundtrip(atlas,tmp_path/'ring.step')
    assert check['passed'] and check['face_count']==8
    zero = Requirement(0.,False,False,False,False,'locality',0)
    assert audit(atlas,restored,zero,())['passed']


def test_full_challenge_run_is_frozen_and_audited(tmp_path):
    summary = run(tmp_path/'challenge')
    assert summary['completed'] and summary['task_count']==30
    assert summary['exact_datum_target_conflicts']==6
    assert summary['independent_human_review']=='pending' and not summary['llm_evaluated']
    records = json.loads((tmp_path/'challenge/results.json').read_text(encoding='utf-8'))
    assert all(r['readback_audit']['passed'] and r['step']['passed'] for r in records if r['status']=='edited')
    assert all(not any(c['feasible'] for c in r['candidates']) for r in records if r['exact_datum_target_conflict'])
    assert (tmp_path/'challenge/review_board.md').exists()
    public = [json.loads(line) for line in (tmp_path/'challenge/public_tasks.jsonl').read_text(encoding='utf-8').splitlines()]
    assert all('oracle_requirement' not in t for t in public)
    with pytest.raises(FileExistsError):
        run(tmp_path/'challenge')
    baseline=save_baselines(tmp_path/'challenge')
    assert baseline['grouped_matches_full_enumeration']==30
    assert baseline['grouped_calls']<baseline['full_enumeration_calls']
    with pytest.raises(FileExistsError):
        save_baselines(tmp_path/'challenge')


def test_independent_polynomial_witness_satisfies_seams_and_fixed_section():
    atlas=make_atlas(0)
    req=Requirement(1.,True,False,False,True,'locality',105)
    target=refined(atlas.tiles[atlas.target],'degree_uv8')
    nodes=np.linspace(0,1,9)
    basis=BSpline(np.array([0.]*9+[1.]*9),np.eye(9),8)(nodes)
    u,v=np.meshgrid(nodes,nodes,indexing='ij')
    datum=atlas.datum_parameter
    # Independent degree-7 x degree-2 polynomial; no equality solver is called.
    displacement=u**3*(1-u)**3*(u-datum)/( .5**6*(.5-datum))*4*v*(1-v)
    poles=target.patch.poles.copy()
    poles[:,:,2]+=np.linalg.solve(basis,np.linalg.solve(basis,displacement.T).T)
    target=replace(target,patch=replace(target.patch,poles=poles))
    tiles=list(atlas.tiles)
    tiles[atlas.target]=target
    edited=replace(atlas,tiles=tiles)
    assert audit(atlas,edited,req,(atlas.target,))['passed']


def test_representation_counterfactual_is_not_an_authorized_solution(tmp_path):
    result=save_diagnostic(tmp_path/'diagnostic.json')
    base,released,refined_case=result['experiments']
    assert not base['feasible_actions']
    assert refined_case['feasible_actions']
    assert any(action.startswith('knots_') for action in refined_case['feasible_actions'])
    assert 'NOT valid solutions' in result['warning']
    with pytest.raises(FileExistsError):
        save_diagnostic(tmp_path/'diagnostic.json')


def test_priority_groups_evaluate_ties_but_skip_dominated_costs():
    req=Requirement(1.,True,True,False,False,'complexity',105)
    records=[dict(action='cheap_failed',support=[0],added_poles=0,feasible=False),
             dict(action='first_tie',support=[0],added_poles=12,feasible=True,audit={'passed':True},reference_l2=10.),
             dict(action='better_tie',support=[0],added_poles=12,feasible=True,audit={'passed':True},reference_l2=2.),
             dict(action='expensive',support=[0],added_poles=33,feasible=True,audit={'passed':True},reference_l2=.1)]
    selected,calls=priority_group_replay(records,req)
    assert selected['action']=='better_tie' and calls==3
    selected,calls=priority_group_replay(records[:1],req)
    assert selected is None and calls==1
