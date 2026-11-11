"""Continuous geometric minimax repair with independently checkable bounds.

This is a restricted fixed-ball/graph problem, not a cutting mechanics model.
LP exchange, exact arithmetic and B-spline refinement are established methods.
The research candidate is their quality-gated use in design-preserving repair.
"""
from dataclasses import dataclass
from fractions import Fraction as F
from functools import lru_cache
from math import isqrt
import heapq

import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import linprog

from verification import (rational, bezier_spans, insert_exact, subdivide,
                          value_at_half, verify_ball)


@dataclass(frozen=True)
class Section:
    knots: tuple
    controls: tuple
    degree: int

    def __post_init__(self):
        object.__setattr__(self, 'knots', tuple(map(rational, self.knots)))
        object.__setattr__(self, 'controls', tuple(map(rational, self.controls)))
        bezier_spans(self.knots, self.controls, self.degree)

    @classmethod
    def from_curve(cls, curve):
        return cls(tuple(curve.t), tuple(curve.c), curve.k)

    def refine(self, positions):
        knots, controls = self.knots, self.controls
        for position in sorted(map(rational, positions)):
            knots, controls = insert_exact(knots, controls, self.degree, position)
        return Section(tuple(knots), tuple(controls), self.degree)

    def as_curve(self):
        return BSpline(np.array(self.knots, dtype=float),
                       np.array(self.controls, dtype=float), self.degree)

    def serialize(self):
        return dict(knots=list(map(str, self.knots)),
                    controls=list(map(str, self.controls)), degree=self.degree)


def basis_row(knots, degree, x, derivative=0):
    """Exact Cox-de Boor basis/derivative, using left limits at the right end."""
    knots, x = tuple(map(rational, knots)), rational(x)
    if derivative < 0 or derivative > degree or not knots[degree] <= x <= knots[-degree-1]:
        raise ValueError('[BASIS_DOMAIN] Invalid derivative or position. 建议操作：检查参数域。')
    right_end = x == knots[-degree-1]

    @lru_cache(None)
    def evaluate(i, p, d):
        if d > p:
            return F(0)
        if p == 0:
            return F(int(knots[i] < x <= knots[i+1] if right_end
                         else knots[i] <= x < knots[i+1]))
        left, right = knots[i+p]-knots[i], knots[i+p+1]-knots[i+1]
        if d:
            return ((p/left*evaluate(i, p-1, d-1) if left else F(0))
                    -(p/right*evaluate(i+1, p-1, d-1) if right else F(0)))
        return (((x-knots[i])/left*evaluate(i, p-1, 0) if left else F(0))
                +((knots[i+p+1]-x)/right*evaluate(i+1, p-1, 0) if right else F(0)))

    return [evaluate(i, degree, derivative) for i in range(len(knots)-degree-1)]


def reduced_rows(matrix, columns):
    """Rational RREF; columns excludes an optional augmented RHS column."""
    rows = [list(map(rational, row)) for row in matrix]
    pivot_columns = []
    for column in range(columns):
        pivot = next((i for i in range(len(pivot_columns), len(rows)) if rows[i][column]), None)
        if pivot is None:
            continue
        index = len(pivot_columns)
        rows[index], rows[pivot] = rows[pivot], rows[index]
        divisor = rows[index][column]
        rows[index] = [v/divisor for v in rows[index]]
        for i in range(len(rows)):
            if i != index and rows[i][column]:
                factor = rows[i][column]
                rows[i] = [a-factor*b for a, b in zip(rows[i], rows[index])]
        pivot_columns.append(column)
    return rows, pivot_columns


def lock_nullspace(section, request):
    """Columns span all coefficient changes preserving the explicit design locks."""
    rows = []
    for x in (section.knots[0], section.knots[-1]):
        rows.extend(basis_row(section.knots, section.degree, x, order)
                    for order in range(request.lock_order+1))
    rows.extend(basis_row(section.knots, section.degree, x, order)
                for x, order in [(0, 0), (0, 1)]+[(x, 0) for x in request.protected_x])
    reduced, pivots = reduced_rows(rows, len(section.controls))
    free = [j for j in range(len(section.controls)) if j not in pivots]
    nullspace = [[F(int(i == j)) for j in free] for i in range(len(section.controls))]
    for i, pivot in enumerate(pivots):
        nullspace[pivot] = [-reduced[i][j] for j in free]
    return nullspace


def sqrt_lower(value, bits=60):
    """Outward rational lower enclosure, without trusting a floating square root."""
    value = rational(value)
    if value < 0 or type(bits) is not int or bits < 1:
        raise ValueError('[SQRT_DOMAIN] Nonnegative value and positive bits required. 建议操作：检查球面定义域。')
    scale = 1 << bits
    return F(isqrt(value.numerator*scale*scale//value.denominator), scale)


def dot(a, b):
    return sum((x*y for x, y in zip(a, b)), F(0))


def sample_problem(section, request, points, nullspace, strict=False):
    """Relaxed rows certify a lower bound; strict rows are search-only."""
    dimension = len(nullspace[0])
    matrix, rhs = [], []
    r, eps = rational(request.radius), rational(request.tolerance)
    for x in points:
        row = basis_row(section.knots, section.degree, x)
        projected = [dot(row, column) for column in zip(*nullspace)]
        matrix.extend([projected+[F(-1)], [-v for v in projected]+[F(-1)],
                       projected+[F(0)]])
        # r - lower(sqrt) is an upper bound of the true obstacle height.
        height_upper = r-sqrt_lower(r*r-x*x)
        slack = eps/2 if strict else eps
        rhs.extend([F(0), F(0), height_upper+slack-dot(row, section.controls)])
    matrix.append([F(0)]*dimension+[F(-1)])
    rhs.append(F(0))
    return matrix, rhs


def reconstruct_dual(matrix, rhs, approximate):
    """Validate a dual witness exactly; numerical residuals cannot certify it.

    min c'x subject to Ax<=b, x free: lambda>=0, A'lambda=-c,
    hence -b'lambda is a lower bound, even if the solver was inaccurate.
    """
    if len(approximate) != len(matrix):
        return None
    support = [i for i, value in enumerate(approximate) if value > 1e-10]
    if not support:
        return None
    columns = len(matrix[0])
    target = [F(0)]*(columns-1)+[F(-1)]
    equations = [[matrix[i][j] for i in support]+[target[j]] for j in range(columns)]
    reduced, pivots = reduced_rows(equations, len(support))
    if any(not any(row[:-1]) and row[-1] for row in reduced):
        return None
    weights = [F(0)]*len(support)
    for i, pivot in enumerate(pivots):
        weights[pivot] = reduced[i][-1]
    if any(weight < 0 for weight in weights):
        return None
    if any(dot([matrix[i][j] for i in support], weights) != target[j] for j in range(columns)):
        return None
    lower = -dot([rhs[i] for i in support], weights)
    return dict(lower_rational=str(lower), lower_mm=float(lower),
                support=support, weights=list(map(str, weights)))


def norm_enclosure(knots, delta, degree, accuracy=1e-8, max_boxes=4096):
    """Whole-domain L-infinity bound and a point attaining the lower bound."""
    if rational(accuracy) <= 0 or type(max_boxes) is not int or max_boxes < 1:
        raise ValueError('[NORM_BUDGET] Positive accuracy and proof budget required. 建议操作：检查界定预算。')
    heap, lower, witness, counter = [], F(0), rational(knots[0]), 0

    def push(a, b, coefficients):
        nonlocal lower, witness, counter
        for x, value in [(a, coefficients[0]), (b, coefficients[-1]),
                         ((a+b)/2, value_at_half(coefficients))]:
            if abs(value) > lower:
                lower, witness = abs(value), x
        heapq.heappush(heap, (-max(map(abs, coefficients)), counter, a, b, coefficients))
        counter += 1

    for a, b, coefficients in bezier_spans(knots, delta, degree):
        push(a, b, coefficients)
    boxes = 0
    while -heap[0][0]-lower > rational(accuracy) and boxes < max_boxes:
        _, _, a, b, coefficients = heapq.heappop(heap)
        left, right = subdivide(coefficients)
        push(a, (a+b)/2, left)
        push((a+b)/2, b, right)
        boxes += 1
    upper = -heap[0][0]
    return dict(lower_mm=float(lower), upper_mm=float(upper),
                lower_rational=str(lower), upper_rational=str(upper),
                witness_rational=str(witness), boxes=boxes,
                status='bounded' if upper-lower <= rational(accuracy) else 'unknown')


def solve_fixed(section, request, gap=1e-5, max_iterations=30, max_boxes=4096):
    """Certify minimax quality only within this fixed spline representation.

    No feasible upper bound or exact dual witness => no optimality assertion.
    Resource exhaustion and numerical LP infeasibility do not prove that the
    editable CAD problem is impossible. The user's budget is never relaxed.
    """
    if (not np.isfinite(gap) or gap <= 0 or type(max_iterations) is not int or max_iterations < 1
            or type(max_boxes) is not int or max_boxes < 1):
        raise ValueError('[SOLVE_BUDGET] Invalid proof/search budget. 建议操作：检查停止条件。')
    if section.knots[0] < -request.radius or section.knots[-1] > request.radius:
        raise ValueError('[BALL_DOMAIN] Curve outside ball projection. 建议操作：统一球面和曲线定义域。')
    nullspace = lock_nullspace(section, request)
    a, b = section.knots[0], section.knots[-1]
    points = set(a+(b-a)*F(i, 8) for i in range(9)) | set(section.knots)
    points.update(map(rational, (0, *request.protected_x)))
    history, best_lower, dual_record, lp_calls = [], F(0), None, 0
    last_candidate, last_norm, last_check = None, None, None
    dimension = len(nullspace[0])
    for iteration in range(max_iterations):
        positions = sorted(points)
        relaxed_a, relaxed_b = sample_problem(section, request, positions, nullspace)
        objective = [0.]*dimension+[1.]
        relaxed = linprog(objective, A_ub=np.array(relaxed_a, dtype=float),
                          b_ub=np.array(relaxed_b, dtype=float),
                          bounds=[(None, None)]*(dimension+1), method='highs')
        lp_calls += 1
        if not relaxed.success:
            return dict(status='representation_unresolved', iterations=iteration+1,
                        history=history, lp_calls=lp_calls, solver_message=relaxed.message), None
        dual = reconstruct_dual(relaxed_a, relaxed_b, -relaxed.ineqlin.marginals)
        if dual and F(dual['lower_rational']) >= best_lower:
            best_lower = F(dual['lower_rational'])
            dual_record = dict(dual, points=list(map(str, positions)))
        strict_a, strict_b = sample_problem(section, request, positions, nullspace, strict=True)
        search = linprog(objective, A_ub=np.array(strict_a, dtype=float),
                         b_ub=np.array(strict_b, dtype=float),
                         bounds=[(None, None)]*(dimension+1), method='highs')
        lp_calls += 1
        if not search.success:
            return dict(status='search_unresolved', history=history, lp_calls=lp_calls, dual=dual_record), None
        # The minimax LP can have many equally good, highly oscillatory fits.
        # Break ties by change at a fixed canonical grid shared by every basis.
        # This is search-only; the final continuous quality gap is still checked.
        canonical = [a+(b-a)*F(i, 64) for i in range(65)]
        canonical_rows = []
        for x in canonical:
            row = basis_row(section.knots, section.degree, x)
            canonical_rows.append([dot(row, col) for col in zip(*nullspace)])
        cap = float(search.fun)+gap/16
        upper_a = np.array(strict_a, dtype=float)
        tertiary_a = np.c_[upper_a[:, :-1], np.zeros((len(strict_a), 65))]
        tertiary_b = np.array(strict_b, dtype=float)-upper_a[:, -1]*cap
        sampled = np.array(canonical_rows, dtype=float).reshape(65, dimension)
        tertiary_a = np.vstack([tertiary_a, np.c_[sampled, -np.eye(65)],
                                np.c_[-sampled, -np.eye(65)]])
        tertiary_b = np.r_[tertiary_b, np.zeros(130)]
        tie = linprog(np.r_[np.zeros(dimension), np.ones(65)/65], A_ub=tertiary_a,
                      b_ub=tertiary_b, bounds=[(None, None)]*dimension+[(0, None)]*65,
                      method='highs')
        lp_calls += 1
        y = list(map(rational, tie.x[:dimension] if tie.success else search.x[:-1]))
        delta = [dot(row, y) for row in nullspace]
        candidate = Section(section.knots, tuple(c+d for c, d in zip(section.controls, delta)), section.degree)
        check = verify_ball(candidate.knots, candidate.controls, candidate.degree,
                            request.radius, request.tolerance, max_boxes=max_boxes)
        norm = norm_enclosure(section.knots, delta, section.degree, gap/8, max_boxes)
        last_candidate, last_norm, last_check = candidate, norm, check
        upper = F(norm['upper_rational'])
        history.append(dict(iteration=iteration, points=len(points),
                            sample_objective_mm=float(search.fun), lower_mm=float(best_lower),
                            upper_mm=float(upper), contact=check.status,
                            norm_status=norm['status'], dual_certified=dual is not None))
        if (check.status == 'pass' and norm['status'] == 'bounded' and dual_record is not None
                and upper-best_lower <= rational(gap)):
            # This exact comparison is the acceptance gate; float display values are not used.
            budget = rational(request.displacement_budget)
            status = ('verified' if upper <= budget else
                      'fixed_basis_budget_conflict' if best_lower > budget else 'budget_boundary_unresolved')
            return dict(status=status, iterations=iteration+1, history=history,
                        lp_calls=lp_calls,
                        lower_mm=float(best_lower), upper_mm=float(upper),
                        gap_mm=float(upper-best_lower), dual=dual_record,
                        norm=norm, contact=vars(check)), candidate
        previous = len(points)
        if check.status == 'fail':
            points.add(F(check.witness_rational))
            # Add one certified counterexample per violating span. Taking only
            # the first failure can chase an active contact arc unnecessarily.
            for left, right, coefficients in bezier_spans(candidate.knots, candidate.controls, candidate.degree):
                local = verify_ball([left]*(candidate.degree+1)+[right]*(candidate.degree+1),
                                    coefficients, candidate.degree, request.radius,
                                    request.tolerance, max_boxes=max_boxes)
                if local.status == 'fail':
                    points.add(F(local.witness_rational))
        points.add(F(norm['witness_rational']))
        if len(points) == previous:
            break
    return dict(status='proof_unresolved', iterations=len(history), history=history,
                dual=dual_record, norm=last_norm, lp_calls=lp_calls,
                contact=vars(last_check) if last_check else None), last_candidate


def replay_certificate(section, request, record):
    """Replay exact lower evidence without trusting SciPy or its status flag."""
    if not record or len(record.get('support', [])) != len(record.get('weights', [])):
        return False
    points = list(map(F, record['points']))
    matrix, rhs = sample_problem(section, request, points, lock_nullspace(section, request))
    support, weights = record['support'], list(map(F, record['weights']))
    if (len(set(support)) != len(support) or any(type(i) is not int or i < 0 or i >= len(matrix) for i in support)
            or any(w < 0 for w in weights)):
        return False
    target = [F(0)]*(len(matrix[0])-1)+[F(-1)]
    balanced = all(dot([matrix[i][j] for i in support], weights) == target[j]
                   for j in range(len(target)))
    return balanced and -dot([rhs[i] for i in support], weights) == F(record['lower_rational'])


def replay_solution(basis, candidate, request, record, gap=1e-5):
    """Recompute both bounds, design locks and acceptance independently of LP."""
    if (candidate is None or candidate.knots != basis.knots or candidate.degree != basis.degree
            or not replay_certificate(basis, request, record.get('dual'))):
        return False
    delta = [a-b for a, b in zip(candidate.controls, basis.controls)]
    locks = [(x, order) for x in (basis.knots[0], basis.knots[-1])
             for order in range(request.lock_order+1)]
    locks += [(0, 0), (0, 1)]+[(x, 0) for x in request.protected_x]
    if any(dot(basis_row(basis.knots, basis.degree, x, order), delta) for x, order in locks):
        return False
    if verify_ball(candidate.knots, candidate.controls, candidate.degree,
                   request.radius, request.tolerance).status != 'pass':
        return False
    norm = norm_enclosure(candidate.knots, delta, candidate.degree, gap/8)
    upper, lower = F(norm['upper_rational']), F(record['dual']['lower_rational'])
    return (norm['status'] == 'bounded' and upper <= rational(request.displacement_budget)
            and F(0) <= upper-lower <= rational(gap))
