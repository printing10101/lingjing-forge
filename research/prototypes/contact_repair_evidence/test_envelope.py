"""Independent numerical oracles, exact witness replay, and fail-closed guards."""
from dataclasses import replace
from fractions import Fraction as F
import copy
import numpy as np
import pytest
from scipy.interpolate import BSpline

from repair import initial_curve, Request
from verification import verify_ball
from envelope_solver import (Section, basis_row, lock_nullspace, dot, sqrt_lower,
    reduced_rows, reconstruct_dual, sample_problem, solve_fixed, replay_certificate,
    replay_solution, norm_enclosure)
from envelope_policy import preflight, repair_quality


def section():
    return Section.from_curve(initial_curve())


def rich():
    return section().refine([-3, -2, -1, 0, 1, 2, 3])


def test_exact_basis_matches_independent_scipy_and_endpoint_derivatives():
    s = rich()
    oracle = BSpline(np.array(s.knots, float), np.eye(len(s.controls)), s.degree)
    for x in np.linspace(-4, 4, 33):
        for order in range(3):
            values = basis_row(s.knots, s.degree, x, order)
            assert np.allclose(np.array(values, float), oracle(x, nu=order), atol=1e-12)
            assert sum(values) == (1 if order == 0 else 0)
    for x, derivative in [(5, 0), (0, -1), (0, 6)]:
        with pytest.raises(ValueError):basis_row(s.knots, 5, x, derivative)


def test_exact_nullspace_locks_hold_for_arbitrary_free_changes():
    s = rich()
    nullspace = lock_nullspace(s, Request(protected_x=(.5,)))
    delta = [dot(row, [F(j-2, 7) for j in range(len(row))]) for row in nullspace]
    assert any(delta)
    for x in (-4, 4):
        for order in range(3):assert dot(basis_row(s.knots, 5, x, order), delta) == 0
    for x, order in [(0, 0), (0, 1), (F(1, 2), 0)]:
        assert dot(basis_row(s.knots, 5, x, order), delta) == 0


def test_shape_preserving_insertion_and_serialization():
    s = section(); t = s.refine([-2, 0, 2])
    for x in (F(-7, 2), F(1, 7), F(15, 4)):
        assert dot(basis_row(s.knots, 5, x), s.controls) == dot(basis_row(t.knots, 5, x), t.controls)
    assert np.allclose(s.as_curve()(np.linspace(-4, 4)), t.as_curve()(np.linspace(-4, 4)))
    encoded = t.serialize()
    assert Section(tuple(map(F, encoded['knots'])), tuple(map(F, encoded['controls'])), 5) == t
    with pytest.raises(ValueError):Section((0, 1), (0,), 5)


def test_outward_square_root_is_exactly_enclosed():
    for v in (F(0), F(2), F(47, 13), F(64)):
        lower = sqrt_lower(v, 40)
        assert lower*lower <= v < (lower+F(1, 2**40))**2
    with pytest.raises(ValueError):sqrt_lower(-1)
    with pytest.raises(ValueError):sqrt_lower(1, 0)


def test_dual_witness_is_exact_not_a_numerical_residual():
    # min t subject to -t<=-2, -t<=0: exact optimum 2.
    matrix, rhs = [[F(-1)], [F(-1)]], [F(-2), F(0)]
    witness = reconstruct_dual(matrix, rhs, [1.0000001, 0])
    assert witness['lower_rational'] == '2' and witness['weights'] == ['1']
    assert reconstruct_dual(matrix, rhs, [0, 0]) is None
    assert reconstruct_dual(matrix, rhs, []) is None
    assert reconstruct_dual([[F(0)]], [F(0)], [1]) is None
    assert reconstruct_dual([[F(1)]], [F(0)], [1]) is None
    rows, pivots = reduced_rows([[0, 1], [0, 2]], 2)
    assert pivots == [1]


def test_whole_domain_norm_encloses_independent_dense_oracle():
    s = rich(); delta = [F((i % 3)-1, 5) for i in range(len(s.controls))]
    result = norm_enclosure(s.knots, delta, 5, accuracy=1e-9)
    curve = BSpline(np.array(s.knots, float), np.array(delta, float), 5)
    dense = np.max(np.abs(curve(np.linspace(-4, 4, 200001))))
    assert result['status'] == 'bounded'
    assert result['lower_mm']-1e-10 <= dense <= result['upper_mm']+1e-10
    x = F(result['witness_rational'])
    assert abs(dot(basis_row(s.knots, 5, x), delta)) == F(result['lower_rational'])
    difficult = [F(0)]*len(s.controls); difficult[2] = F(1, 5)
    unresolved = norm_enclosure(s.knots, difficult, 5, accuracy=1e-20, max_boxes=1)
    assert unresolved['status'] == 'unknown'
    with pytest.raises(ValueError):norm_enclosure(s.knots, delta, 5, accuracy=0)


def test_fixed_solution_continuous_contact_locks_and_exact_dual_replay():
    s, q = rich(), Request()
    record, candidate = solve_fixed(s, q)
    assert record['status'] == 'verified'
    assert record['gap_mm'] < 1e-5
    assert replay_certificate(s, q, record['dual'])
    assert replay_solution(s, candidate, q, record)
    assert verify_ball(candidate.knots, candidate.controls, 5, 8).status == 'pass'
    assert record['upper_mm'] < .1081
    for x in (-4, 4):
        for order in range(3):
            assert dot(basis_row(s.knots, 5, x, order), candidate.controls) == dot(basis_row(s.knots, 5, x, order), s.controls)
    assert any(h['contact'] == 'fail' for h in record['history'])


def test_tampered_certificates_and_geometry_are_rejected():
    s, q = rich(), Request(); record, candidate = solve_fixed(s, q)
    dual = record['dual']
    for field, value in [('lower_rational', '900'), ('weights', ['-1']*len(dual['weights'])),
                         ('support', [9999]*len(dual['support']))]:
        bad = {**dual, field:value}
        assert not replay_certificate(s, q, bad)
    assert not replay_certificate(s, q, None)
    assert not replay_certificate(s, q, {**dual, 'weights':[]})
    assert not replay_solution(s, None, q, record)
    assert not replay_solution(s, s.refine([F(1, 2)]), q, record)
    moved = Section(s.knots, tuple(c+F(1, 100) for c in candidate.controls), 5)
    assert not replay_solution(s, moved, q, record)
    tiny_drift = Section(s.knots, tuple(c-F(1, 10**8) for c in candidate.controls), 5)
    assert verify_ball(tiny_drift.knots, tiny_drift.controls, 5, 8).status == 'pass'
    assert not replay_solution(s, tiny_drift, q, record)
    assert not replay_solution(s, candidate, replace(q, displacement_budget=.01), record)
    assert not replay_solution(s, s, q, record)


def test_unresolved_proof_and_lp_status_never_pass(monkeypatch):
    import envelope_solver as module
    s, q = rich(), Request()
    record, _ = solve_fixed(s, q, max_iterations=1)
    assert record['status'] == 'proof_unresolved'
    record, _ = solve_fixed(s, q, max_boxes=1)
    assert record['status'] == 'proof_unresolved'
    monkeypatch.setattr(module, 'reconstruct_dual', lambda *args:None)
    record, _ = solve_fixed(s, q)
    assert record['status'] == 'proof_unresolved'
    for args in [dict(gap=0), dict(max_iterations=0), dict(max_boxes=0)]:
        with pytest.raises(ValueError):solve_fixed(s, q, **args)
    with pytest.raises(ValueError):solve_fixed(s, Request(radius=3))


def test_small_representation_is_not_global_impossibility():
    q = Request()
    record, candidate = solve_fixed(section(), q)
    assert record['status'] == 'representation_unresolved' and candidate is None
    refined, candidate = solve_fixed(rich(), q)
    assert refined['status'] == 'verified'
    record, _ = solve_fixed(rich(), replace(q, displacement_budget=.05))
    assert record['status'] == 'fixed_basis_budget_conflict' and record['lower_mm'] > .05
    boundary = (refined['lower_mm']+refined['upper_mm'])/2
    record, _ = solve_fixed(rich(), replace(q, displacement_budget=boundary))
    assert record['status'] == 'budget_boundary_unresolved'


def test_preflight_conflicts_are_exact_and_basis_independent():
    for s in (section(), rich()):
        assert preflight(s, Request(displacement_budget=.03))['status'] == 'displacement_budget_conflict'
        assert preflight(s, Request(protected_x=(2.,)))['status'] == 'protected_point_conflict'
        assert preflight(s, Request()) is None
    with pytest.raises(ValueError):preflight(section(), Request(radius=3))


def test_policy_preserves_feasible_incumbent_and_counts_all_trials():
    q = Request(max_controls=16)
    for policy in ('uniform', 'dual_local', 'evidence'):
        record, candidate = repair_quality(section(), q, policy, max_basis_solves=20)
        assert record['status'] in ('verified_quality', 'verified_budget_only')
        assert record['basis_solves'] == len(record['history']) <= 20
        if record['status'] == 'verified_quality':
            assert record['global_gap_upper_mm'] <= 1e-4
        assert candidate is not None
        certified = [row['upper_mm'] for row in record['history'] if row['certified']]
        assert record['best']['upper_mm'] == min(certified)
    conflict, candidate = repair_quality(section(), Request(displacement_budget=.03))
    assert conflict['status'] == 'displacement_budget_conflict' and candidate is None
    unresolved, candidate = repair_quality(section(), q, max_basis_solves=1)
    assert unresolved['status'] == 'unresolved' and candidate is None
    partial, candidate = repair_quality(rich(), q, quality_gap=1e-8, max_basis_solves=1)
    assert partial['status'] == 'verified_budget_only' and candidate is not None
    with pytest.raises(ValueError):repair_quality(section(), q, 'other')


def test_missing_dual_or_invalid_replay_cannot_authorize_policy(monkeypatch):
    import envelope_policy as module
    monkeypatch.setattr(module, 'replay_solution', lambda *args:False)
    record, candidate = repair_quality(rich(), Request(), max_basis_solves=1)
    assert record['status'] == 'unresolved' and candidate is None


def test_profiles_have_independent_analytic_values_and_frozen_matrix():
    from envelope_experiment import profile, default_configurations
    x = np.linspace(-4, 4, 101)
    assert np.allclose(profile('asymmetric_quintic').as_curve()(x),
                       .1*x*x+.002*x**3-.003*x**4-.000125*x**5)
    assert np.allclose(profile('shallow_quartic').as_curve()(x), .09*x*x-.0025*x**4)
    assert len(default_configurations()) == 14
    with pytest.raises(ValueError):profile('unknown')


def test_complete_small_frozen_comparison_replays_and_preserves_failures(tmp_path):
    import json
    from envelope_experiment import run, default_configurations
    output = tmp_path/'run'
    # One feasible and one impossible request exercise all seven strategies.
    summary = run(output, [default_configurations()[3], default_configurations()[-1]])
    assert summary['runs'] == 14 and summary['exact_replayed'] == 5
    rows = json.loads((output/'results.json').read_text(encoding='utf-8'))
    assert sum(r['status'] == 'protected_point_conflict' for r in rows) == 7
    assert all(r['lp_calls'] >= 0 for r in rows)
    assert (output/'proof_0_direct_13.json').exists()
    with pytest.raises(FileExistsError):run(output)


def test_readback_budget_checked_even_if_contact_and_design_pass(tmp_path, monkeypatch):
    import cad_adapter
    from envelope_experiment import export_checked
    original, q = section(), Request(displacement_budget=.11)
    _, candidate = solve_fixed(rich(), q)
    real_roundtrip = cad_adapter.roundtrip
    def too_much_removal(*args, **kwargs):
        restored, step = real_roundtrip(*args, **kwargs)
        # Lower one interior control row: avoids the ball, preserves endpoint C2
        # and origin locks, yet makes the edit exceed the authorized 0.11 mm.
        restored[1].poles[3, :, 2] -= .5
        return restored, step
    monkeypatch.setattr(cad_adapter, 'roundtrip', too_much_removal)
    with pytest.raises(RuntimeError, match='READBACK_BUDGET'):
        export_checked(original, candidate, q, tmp_path/'excess_removal.step')


def test_greedy_plateau_probe_has_disjoint_certified_gain_ranges(tmp_path):
    from coupling_probe import run
    output = tmp_path/'probe.json'
    conclusion = run(output)
    assert conclusion['complementary_gain_certified']
    assert conclusion['single_gain_upper_mm'] < 1e-5
    assert conclusion['pair_gain_lower_mm'] > .004
    with pytest.raises(FileExistsError):run(output)
