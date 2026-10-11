"""Checks include independent polynomial witnesses and deliberate corruption."""
import numpy as np
import pytest

from probe import (bernstein, boundary_row, evaluate, gram, local_witness,
                   solve, system, sweep)


@pytest.mark.parametrize('order', [0, 1, 2])
def test_constructive_local_solution(order):
    degree = 2 * (order + 1)
    x = local_witness(order, 1.7)
    a, b = system(degree, order, (1,), 40, 1.7)
    assert np.max(np.abs(a @ x - b)) < 1e-12
    checked = evaluate(x, degree, order, 40)
    assert checked['center_mm'] == pytest.approx(1.7)
    assert max(checked['seam_derivative_errors']) < 1e-12
    assert checked['neighbor_control_bound_mm'] == 0


@pytest.mark.parametrize('order,feasible', [(0, True), (1, False), (2, False)])
def test_cubic_local_degree_obstruction(order, feasible):
    # C1 forces cubic rows 0,1,2,3 to zero when neighbors are frozen.
    # This algebraic fact is independent of the least-squares solver.
    a, b = system(3, order, (1,), 40, 1)
    _, result = solve(a, b, 3, 40, 20)
    assert result['feasible'] is feasible
    if not feasible:
        assert result['certificate_null_error'] < 1e-9
        assert abs(result['certificate_rhs']) > 1e-3


def test_global_solution_and_independent_corruption_detection():
    a, b = system(3, 2, (0, 1, 2), 40, 1)
    x, result = solve(a, b, 3, 40, 20)
    assert result['feasible']
    assert max(evaluate(x, 3, 2, 40)['seam_derivative_errors']) < 1e-10
    x[12] += .1  # Patch 0, seam endpoint u=1, v control index 0.
    assert evaluate(x, 3, 2, 40)['seam_derivative_errors'][1] > 1e-4


def test_contradiction_is_not_silently_relaxed():
    a, b = system(6, 2, (0, 1, 2), 40, 1, pin_center=True)
    _, result = solve(a, b, 6, 40, 20)
    assert not result['feasible']
    assert result['certificate_null_error'] < 1e-8
    assert abs(result['certificate_rhs']) > .1


def test_gram_matches_independent_quadrature():
    nodes, weights = np.polynomial.legendre.leggauss(12)
    basis = np.array([bernstein(6, (t + 1) / 2) for t in nodes])
    assert np.allclose(gram(6), basis.T @ np.diag(weights / 2) @ basis)
    assert np.allclose(boundary_row(3, 2, True, 2), [0, 1.5, -3, 1.5])


@pytest.mark.parametrize('kwargs', [{'degree': 1, 'continuity': 2},
                                   {'length': 0}, {'support': (0,)}])
def test_invalid_contract(kwargs):
    arguments = dict(degree=3, continuity=2, support=(1,), length=40, amplitude=1)
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        system(**arguments)


def test_invalid_width():
    a, b = system(3, 0, (1,), 40, 1)
    with pytest.raises(ValueError):
        solve(a, b, 3, 40, 0)


def test_full_sweep():
    report = sweep()
    assert len(report['results']) == 243
    assert report['summary']['cubic_local'] == {'C0': 27, 'C1': 0, 'C2': 0}
    for strategy in ('cubic_global', 'degree_adaptive_local'):
        assert report['summary'][strategy] == {'C0': 27, 'C1': 27, 'C2': 27}
    assert not report['incompatible_center_pin']['feasible']
