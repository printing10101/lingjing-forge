"""Restricted non-rational graph B-splines and a traditional equality solver.

Only z displacement is editable. Continuity is Ck with matched physical x/y
parameters, a sufficient special case of Gk, not a general G2 algorithm.
"""
from dataclasses import dataclass
from itertools import product

import numpy as np
from scipy.interpolate import BSpline


@dataclass
class Patch:
    poles: np.ndarray
    u_knots: np.ndarray
    v_knots: np.ndarray
    u_degree: int
    v_degree: int
    length: float
    width: float

    def __post_init__(self):
        if (self.poles.ndim != 3 or self.poles.shape[-1] != 3 or
                not np.all(np.isfinite(self.poles)) or not np.isfinite([self.length, self.width]).all() or
                self.length <= 0 or self.width <= 0):
            raise ValueError('[INVALID_PATCH] Finite graph poles and positive sizes required.')
        for knots, degree, count in ((self.u_knots, self.u_degree, self.poles.shape[0]),
                                     (self.v_knots, self.v_degree, self.poles.shape[1])):
            if (degree < 1 or len(knots) != count + degree + 1 or
                    not np.all(np.isfinite(knots)) or np.any(np.diff(knots) < 0) or
                    not np.all(knots[:degree + 1] == 0) or not np.all(knots[-degree - 1:] == 1)):
                raise ValueError('[INVALID_SPLINE] Expected clamped unit-domain B-spline.')
        gu = np.array([np.mean(self.u_knots[i + 1:i + self.u_degree + 1])
                       for i in range(self.poles.shape[0])])
        gv = np.array([np.mean(self.v_knots[i + 1:i + self.v_degree + 1])
                       for i in range(self.poles.shape[1])])
        if (not np.allclose(self.poles[:, :, 0], self.poles[0, 0, 0] + self.length * gu[:, None], atol=1e-8, rtol=0) or
                not np.allclose(self.poles[:, :, 1], self.width * gv[None, :], atol=1e-8, rtol=0)):
            raise ValueError('[UNSUPPORTED_PARAMETERIZATION] Fixed affine xy graph coordinates required.')

    def basis(self, axis, parameter, order=0):
        knots = self.u_knots if axis == 0 else self.v_knots
        degree = self.u_degree if axis == 0 else self.v_degree
        count = self.poles.shape[axis]
        return BSpline(knots, np.eye(count), degree)(parameter, nu=order)

    def point(self, u, v, du=0, dv=0):
        return np.einsum('i,ijc,j->c', self.basis(0, u, du), self.poles,
                         self.basis(1, v, dv))


@dataclass(frozen=True)
class Contract:
    amplitude_mm: float
    continuity: int
    allow_refine: bool
    allow_expand: bool
    pin_center: bool = False
    priority: str = 'locality'

    def __post_init__(self):
        if (not np.isfinite(self.amplitude_mm) or self.continuity not in (0, 1, 2) or
                self.priority not in ('locality', 'complexity') or any(type(x) is not bool for x in
                    (self.allow_refine, self.allow_expand, self.pin_center))):
            raise ValueError('[INVALID_CONTRACT] Finite target, C0-C2 and explicit boolean permissions required.')


def base_geometry(seed):
    """Different control nets of one graph family; not independent topologies."""
    rng = np.random.default_rng(seed)
    length, width = rng.uniform(20, 80), rng.uniform(15, 55)
    coefficients = rng.normal(0, 2, (4, 4))
    grid = np.linspace(0, 1, 4)
    knots = np.array([0.] * 4 + [1.] * 4)
    basis = BSpline(knots, np.eye(4), 3)(grid)
    patches = []
    for p in range(3):
        z = np.polynomial.polynomial.polyval2d(*np.meshgrid((p + grid) / 3, grid, indexing='ij'),
                                              coefficients)
        z_poles = np.linalg.solve(basis, np.linalg.solve(basis, z.T).T)
        poles = np.empty((4, 4, 3))
        poles[:, :, 0] = ((p + grid) * length)[:, None]
        poles[:, :, 1] = (grid * width)[None, :]
        poles[:, :, 2] = z_poles
        patches.append(Patch(poles, knots.copy(), knots.copy(), 3, 3, length, width))
    return patches


def _gram(patch, axis):
    knots = patch.u_knots if axis == 0 else patch.v_knots
    degree = patch.u_degree if axis == 0 else patch.v_degree
    count = patch.poles.shape[axis]
    matrix = np.zeros((count, count))
    nodes, weights = np.polynomial.legendre.leggauss(degree + 1)
    unique = np.unique(knots)
    for start, end in zip(unique[:-1], unique[1:]):
        points = start + (nodes + 1) * (end - start) / 2
        basis = np.array([patch.basis(axis, t) for t in points])
        matrix += basis.T @ np.diag(weights * (end - start) / 2) @ basis
    return matrix


def fit_displacement(patches, contract, support):
    """Return a candidate plus approximate infeasibility evidence, without relaxation."""
    if len(patches) != 3 or 1 not in support or not set(support) <= {0, 1, 2}:
        raise ValueError('[INVALID_SCOPE] Three patches and a support containing patch 1 required.')
    if any(p.v_degree != 3 or p.poles.shape[1] != 4 for p in patches):
        raise ValueError('[UNSUPPORTED_SEAM] Matching cubic v bases required.')
    sizes = [p.poles.shape[0] * p.poles.shape[1] for p in patches]
    offsets = np.cumsum([0] + sizes)
    rows, rhs = [], []

    def add(parts, target=0.):
        row = np.zeros(offsets[-1])
        for index, values in parts:
            row[offsets[index]:offsets[index + 1]] = values.reshape(-1)
        rows.append(row)
        rhs.append(target)

    for seam, order, j in product((0, 1), range(contract.continuity + 1), range(4)):
        left, right = patches[seam:seam + 2]
        vl, vr = np.zeros(left.poles.shape[:2]), np.zeros(right.poles.shape[:2])
        vl[:, j] = left.basis(0, 1, order) / left.length**order
        vr[:, j] = -right.basis(0, 0, order) / right.length**order
        add([(seam, vl), (seam + 1, vr)])
    for index, endpoint in ((0, 0), (2, 1)):
        for j in range(4):
            values = np.zeros(patches[index].poles.shape[:2])
            values[:, j] = patches[index].basis(0, endpoint)
            add([(index, values)])
    for index in set(range(3)) - set(support):
        for i in range(sizes[index]):
            values = np.eye(sizes[index])[i]
            add([(index, values)])
    center = np.outer(patches[1].basis(0, .5), patches[1].basis(1, .5))
    add([(1, center)], contract.amplitude_mm)
    if contract.pin_center:
        add([(1, center)], 0)
    matrix, target = np.asarray(rows), np.asarray(rhs)
    scales = np.linalg.norm(matrix, axis=1)
    matrix, target = matrix / scales[:, None], target / scales
    metric = np.zeros((offsets[-1], offsets[-1]))
    for i, patch in enumerate(patches):
        metric[offsets[i]:offsets[i + 1], offsets[i]:offsets[i + 1]] = (
            np.kron(_gram(patch, 0), _gram(patch, 1)) * patch.length * patch.width)
    transform = np.linalg.inv(np.linalg.cholesky(metric).T)
    solution = transform @ np.linalg.lstsq(matrix @ transform, target, rcond=1e-12)[0]
    residual = target - matrix @ solution
    feasible = bool(np.max(np.abs(residual)) <= 1e-8)
    certificate = residual / np.linalg.norm(residual) if not feasible else residual * 0
    evidence = dict(feasible=feasible, normalized_residual=float(np.max(np.abs(residual))),
                    certificate_null_error=float(np.linalg.norm(matrix.T @ certificate)),
                    certificate_rhs=float(certificate @ target),
                    reference_l2_mm4=float(solution @ metric @ solution) if feasible else None)
    if not feasible:
        return None, evidence
    edited = []
    for i, patch in enumerate(patches):
        poles = patch.poles.copy()
        poles[:, :, 2] += solution[offsets[i]:offsets[i + 1]].reshape(poles.shape[:2])
        edited.append(Patch(poles, patch.u_knots.copy(), patch.v_knots.copy(),
                            patch.u_degree, patch.v_degree, patch.length, patch.width))
    return edited, evidence


def seam_residuals(patches, continuity):
    """Independent coefficient-level check, separate from the constraint matrix."""
    errors = []
    for order in range(continuity + 1):
        worst = 0.
        for left, right in zip(patches[:-1], patches[1:]):
            # Evaluate derivative curves through SciPy's spline derivative API.
            dl = BSpline(left.u_knots, left.poles, left.u_degree).derivative(order)(1) / left.length**order
            dr = BSpline(right.u_knots, right.poles, right.u_degree).derivative(order)(0) / right.length**order
            worst = max(worst, float(np.max(np.linalg.norm(dl - dr, axis=1))))
        errors.append(worst)
    return errors


def geometric_jets(patch, u, v):
    """Graph normal and mean/Gaussian curvature in physical units."""
    zx = patch.point(u, v, 1, 0)[2] / patch.length
    zy = patch.point(u, v, 0, 1)[2] / patch.width
    zxx = patch.point(u, v, 2, 0)[2] / patch.length**2
    zxy = patch.point(u, v, 1, 1)[2] / (patch.length * patch.width)
    zyy = patch.point(u, v, 0, 2)[2] / patch.width**2
    norm = np.sqrt(1 + zx*zx + zy*zy)
    normal = np.array([-zx, -zy, 1]) / norm
    mean = ((1 + zy*zy)*zxx - 2*zx*zy*zxy + (1 + zx*zx)*zyy) / (2*norm**3)
    gaussian = (zxx*zyy - zxy*zxy) / norm**4
    return normal, np.array([mean, gaussian])


def audit_edit(original, edited, contract, support):
    """Exact seam coefficient bounds + sampled shape checks (not full CAD proof)."""
    seams = seam_residuals(edited, contract.continuity)
    center_delta = (edited[1].point(.5, .5) - original[1].point(.5, .5))[2]
    edit_error = abs(center_delta - contract.amplitude_mm)
    pin_error = abs(center_delta) if contract.pin_center else 0.
    locked_error, outer_error, normal_error, curvature_error = 0., 0., 0., 0.
    for i, u, v in product(range(3), np.linspace(0, 1, 13), np.linspace(0, 1, 9)):
        delta = np.linalg.norm(edited[i].point(u, v) - original[i].point(u, v))
        if i not in support:
            locked_error = max(locked_error, float(delta))
        if (i == 0 and u == 0) or (i == 2 and u == 1):
            outer_error = max(outer_error, float(delta))
    for i, v in product(range(2), np.linspace(0, 1, 17)):
        nl, kl = geometric_jets(edited[i], 1, v)
        nr, kr = geometric_jets(edited[i + 1], 0, v)
        normal_error = max(normal_error, float(np.linalg.norm(nl - nr)))
        curvature_error = max(curvature_error, float(np.max(np.abs(kl - kr))))
    # Locked neighbor coefficient bounds are exact for this unit-weight graph family.
    locked_bounds = [float(np.max(np.linalg.norm(e.poles - o.poles, axis=2)))
                     for i, (o, e) in enumerate(zip(original, edited)) if i not in support
                     and o.poles.shape == e.poles.shape
                     and np.array_equal(o.u_knots, e.u_knots)]
    result = dict(edit_error_mm=float(edit_error), center_pin_error_mm=float(pin_error), seam_derivative_bounds=seams,
                  sampled_locked_error_mm=locked_error, outer_boundary_error_mm=outer_error,
                  locked_control_bound_mm=max(locked_bounds, default=0.),
                  sampled_normal_vector_error=normal_error, sampled_curvature_error=curvature_error)
    result['passed'] = bool(edit_error < 1e-7 and max(seams) < 1e-7 and
                            locked_error < 1e-7 and outer_error < 1e-7 and
                            result['locked_control_bound_mm'] < 1e-7 and pin_error < 1e-7)
    return result
