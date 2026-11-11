from fractions import Fraction as F
import math
import numpy as np
import pytest
from scipy.interpolate import BSpline
from verification import (verify_ball,bezier_spans,insert_exact,elevate,multiply,
                          subdivide,rational,contact_budget_bounds,displacement_bounds)
from repair import initial_curve, Request, repair, solve_samples, choose_knots


def quadratic(radius=8.):
    # z=x²/(2r) on [-4,4]; exact degree two Bernstein controls.
    return np.array([-4.]*3+[4.]*3),np.array([8/radius,-8/radius,8/radius]),2


def test_safe_contact_is_proved_on_continuous_domain():
    t,c,p=quadratic()
    checked=verify_ball(t,c,p,8)
    assert checked.status=='pass'
    assert checked.boxes>1


def test_unsafe_contact_returns_independently_checked_witness():
    t,c,p=quadratic(5)
    checked=verify_ball(t,c,p,8)
    assert checked.status=='fail'
    x=float(F(checked.witness_rational));z=BSpline(t,c,p)(x)
    assert 8-math.sqrt(64-x*x)-z < -1e-6


def test_budget_exhaustion_is_unknown_not_pass():
    t,c,p=quadratic()
    assert verify_ball(t,c,p,8,max_boxes=1).status=='unknown'
    assert verify_ball(t,c,p,8,max_depth=0).status=='unknown'


def test_tolerance_is_explicit_and_not_a_safety_margin():
    t,c,p=quadratic()
    assert verify_ball(t,c+5e-7,p,8,tolerance=1e-6).status=='pass'
    assert verify_ball(t,c+2e-6,p,8,tolerance=1e-6).status=='fail'


def test_exact_insertion_and_extraction_match_independent_scipy():
    curve=initial_curve().insert_knot(-1).insert_knot(.5)
    spans=bezier_spans(curve.t,curve.c,curve.k)
    for a,b,c in spans:
        for u in np.linspace(0,1,11):
            from math import comb
            direct=sum(float(v)*comb(curve.k,i)*u**i*(1-u)**(curve.k-i)
                       for i,v in enumerate(c))
            assert direct==pytest.approx(curve(float(a)+(float(b)-float(a))*u),abs=1e-12)
    with pytest.raises(ValueError):
        insert_exact([F(0)]*3+[F(1)]*3,[F(0)]*3,2,F(0))


def test_polynomial_operations():
    assert multiply([F(0),F(1)],[F(0),F(1)])==[0,0,1]
    assert elevate([F(0),F(1)],2)==[0,F(1,2),1]
    assert subdivide([F(0),F(0),F(1)])==([0,0,F(1,4)],[F(1,4),F(1,2),1])
    with pytest.raises(ValueError):elevate([F(0),F(1)],0)
    with pytest.raises(ValueError):rational(math.nan)


@pytest.mark.parametrize('args',[
    ([0,0,1,1],[0,1],0),([0,0,1,1],[0,1],11),
    ([0,0,1,1],[0,math.inf],1),([0,1,0,1],[0,1],1),
    ([0,0,0,1,1,1],[0,1],2),
    ([0,0,.5,.5,1,1],[0,0,0,0],1)])
def test_invalid_splines_rejected(args):
    with pytest.raises(ValueError):bezier_spans(*args)


@pytest.mark.parametrize('kwargs',[{'radius':0},{'tolerance':-1},{'max_boxes':0},{'max_depth':-1}])
def test_invalid_verifier_configuration(kwargs):
    t,c,p=quadratic();args=dict(radius=8);args.update(kwargs)
    with pytest.raises(ValueError):verify_ball(t,c,p,**args)


def test_ball_domain_is_not_silently_clipped():
    t,c,p=quadratic()
    with pytest.raises(ValueError):verify_ball(t,c,p,2)


def test_baseline_profile_matches_direct_quartic():
    for scale in (.1,1.,10.):
        curve=initial_curve(scale);x=np.linspace(-4,4,31)
        assert np.allclose(curve(x*scale),scale*(.1*x*x-.003*x**4),atol=1e-12)
    with pytest.raises(ValueError):initial_curve(0)


def test_protected_point_conflict_does_not_get_relaxed():
    outcome,curve=repair(initial_curve(),Request(protected_x=(2.,)))
    assert curve is None and outcome['status']=='protected_point_conflict'
    assert outcome['gap_mm']<-.09 and outcome['history']==[]


def test_coarse_representation_failure_is_not_global_infeasibility():
    outcome,curve=repair(initial_curve(),Request(),policy='fixed')
    assert curve is None and outcome['status']=='representation_unresolved'
    assert outcome['history'][0]['slack_mm']>.01


def test_no_edit_case_and_budget_status():
    curve=initial_curve()
    result,edited=repair(curve,Request(radius=4.1))
    assert result['status']=='verified' and edited is not None
    unresolved,_=repair(curve,Request(max_controls=6))
    assert unresolved['status']=='budget_unknown'


@pytest.mark.parametrize('kwargs',[{'radius':0},{'lock_order':3},{'max_controls':5},
                                  {'max_solves':0},{'protected_x':(math.nan,)}])
def test_invalid_requests(kwargs):
    with pytest.raises(ValueError):Request(**kwargs)


def test_invalid_search_domains_and_policy():
    with pytest.raises(ValueError):repair(initial_curve(),Request(radius=2))
    with pytest.raises(ValueError):repair(initial_curve(),Request(protected_x=(10.,)))
    with pytest.raises(ValueError):repair(initial_curve(),Request(),policy='invalid')


def test_refinement_policies():
    curve=initial_curve();points=np.linspace(-4,4,9)
    assert choose_knots(curve,points,{},'uniform')==[0.]
    assert choose_knots(curve,points,{},'targeted')==[0.]


def test_refinement_finds_solution_without_unlocking_c2():
    original=initial_curve()
    fixed,_=repair(original,Request(),policy='fixed')
    adaptive,candidate=repair(original,Request(),policy='targeted')
    assert fixed['status']=='representation_unresolved'
    assert adaptive['status']=='verified'
    assert len(candidate.c)==10
    for x in (-4.,4.):
        for order in (0,1,2):
            assert candidate(x,nu=order)==pytest.approx(original(x,nu=order),abs=1e-9)
    assert any(h.get('verification',{}).get('status')=='fail' for h in adaptive['history'])


def test_budget_impossibility_uses_continuous_lower_envelope():
    result,candidate=repair(initial_curve(),Request(displacement_budget=.03))
    assert candidate is None and result['status']=='displacement_budget_conflict'
    x=float(F(result['certificate']['witness_rational']))
    # Independent necessary condition at the exact witness, without any spline optimization.
    lowest=.1*x*x-.003*x**4-.03
    assert lowest>8-math.sqrt(64-x*x)+1e-6
    assert result['history']==[]


def test_contact_lower_bound_matches_independent_dense_scan():
    from repair import ball_height
    curve=initial_curve();bound=contact_budget_bounds(curve.t,curve.c,curve.k,8.,.6)
    x=np.linspace(-4,4,200001);sampled=float(np.max(curve(x)-ball_height(x,8.)))-1e-6
    assert bound['status']=='bounded'
    assert bound['lower_mm']-1e-8 <= sampled <= bound['upper_mm']+1e-8
    assert bound['upper_mm']-bound['lower_mm']<=1e-7


def test_displacement_bound_detects_between_sample_extrema():
    curve=initial_curve();refined=curve.insert_knot(0)
    changed=refined.c.copy();changed[2]+=.1
    result=displacement_bounds(curve.t,curve.c,curve.k,refined.t,changed)
    sample=np.linspace(-4,4,100001)
    dense=float(np.max(np.abs(BSpline(refined.t,changed,refined.k)(sample)-curve(sample))))
    assert result['status']=='bounded'
    assert result['lower_mm']-1e-10<=dense<=result['upper_mm']+1e-10
    assert result['upper_mm']<.1
    exhausted=displacement_bounds(curve.t,curve.c,curve.k,refined.t,changed,max_boxes=1,accuracy=1e-20)
    assert exhausted['status']=='unknown' and exhausted['upper_mm']>=dense


def test_invalid_geometric_bound_inputs():
    c=initial_curve()
    with pytest.raises(ValueError):contact_budget_bounds(c.t,c.c,c.k,8,0.)
    with pytest.raises(ValueError):contact_budget_bounds(c.t,c.c,c.k,8,.6,accuracy=0)
    with pytest.raises(ValueError):displacement_bounds(c.t,c.c,c.k,c.t+1,c.c)
    with pytest.raises(ValueError):displacement_bounds(c.t,c.c,c.k,c.t,c.c,max_boxes=0)
    refined=c.insert_knot(0)
    with pytest.raises(ValueError):displacement_bounds(refined.t,refined.c,refined.k,c.t,c.c)


def test_coarse_sample_acceptance_is_not_a_continuous_proof():
    from repair import ball_height
    original=initial_curve()
    for k in (0,-2,2,-3,3):original=original.insert_knot(k)
    points=np.linspace(-4,4,9)
    candidate,_=solve_samples(original,Request(),points)
    assert candidate is not None
    assert np.min(ball_height(points,8)-candidate(points))>=-1e-6
    assert verify_ball(candidate.t,candidate.c,candidate.k,8).status=='fail'


def test_readback_contact_is_checked_even_when_design_audit_passes(tmp_path,monkeypatch):
    import cad_adapter
    request=Request();_,candidate=repair(initial_curve(),request)
    def lost_edit(edited,path):
        return [edited[0],cad_adapter.graph_patch(initial_curve()),edited[2]],{'passed':True}
    monkeypatch.setattr(cad_adapter,'roundtrip',lost_edit)
    with pytest.raises(RuntimeError,match='READBACK_REJECTED'):
        cad_adapter.export_and_reverify(candidate,request,tmp_path/'lost_edit.step')


def test_complete_frozen_experiment_and_no_overwrite(tmp_path):
    from experiment import run
    from pathlib import Path
    import experiment
    assert Path(experiment.__file__).parent==Path(__file__).parent
    output=tmp_path/'run'
    summary=run(output)
    assert summary['runs']==60 and summary['step_exported']
    assert summary['by_policy']['targeted']['verified']==16
    assert summary['sparse_sampling_ablation']['independent_continuous_check']['status']=='fail'
    assert (output/'manifest.json').exists() and (output/'results.json').exists()
    with pytest.raises(FileExistsError):run(output)
    from analyze_bounds import analyze
    bounds=analyze(output,tmp_path/'bounds.json')
    assert bounds['contact_necessary_change']['status']=='bounded'
    assert all(v['geometric_change_bound']['status']=='bounded' for v in bounds['candidate_comparison'])
    with pytest.raises(FileExistsError):analyze(output,tmp_path/'bounds.json')


def test_real_step_roundtrip_reuses_existing_adapter(tmp_path):
    from cad_adapter import export_and_reverify
    request=Request();_,candidate=repair(initial_curve(),request)
    result=export_and_reverify(candidate,request,tmp_path/'shape.step')
    assert result['step']['face_count']==3
    assert result['design_audit']['passed']
    assert all(v['status']=='pass' for v in result['contact_after_readback'])
    with pytest.raises(FileExistsError):export_and_reverify(candidate,request,tmp_path/'shape.step')
    with pytest.raises(ValueError):export_and_reverify(candidate,Request(lock_order=0),tmp_path/'other.step')
