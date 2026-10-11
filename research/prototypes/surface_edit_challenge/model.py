"""Three synthetic graph-surface atlas families and trace-preserving editing.

Restricted affine xy, unit weights, z-only motion. Smooth seams use physical C2;
sharp seams retain both original first jets. No arbitrary G2/trimming claims.
"""
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path
import sys

import numpy as np
from scipy.interpolate import BSpline

PILOT_ROOT = Path(__file__).resolve().parents[1] / 'surface_edit_pilot'
if str(PILOT_ROOT) not in sys.path:
    sys.path.insert(0, str(PILOT_ROOT))
from geometry import Patch, _gram
from kernel import from_surface, to_surface


@dataclass
class Tile:
    face_id: str
    cell: tuple[int, int]
    patch: Patch
    y_origin: float

    def point(self, u, v, du=0, dv=0):
        point = self.patch.point(u, v, du, dv)
        if du == dv == 0:
            point = point + [0, self.y_origin, 0]
        return point


@dataclass
class Atlas:
    case_id: str
    family: str
    tiles: list[Tile]
    target: int
    interface: int
    datum_axis: int
    datum_parameter: float
    sharp_seams: set[tuple[int, int]]

    def seams(self):
        lookup = {tile.cell: i for i, tile in enumerate(self.tiles)}
        return [(i, lookup[cell], axis) for i, tile in enumerate(self.tiles)
                for axis, cell in ((0, (tile.cell[0] + 1, tile.cell[1])),
                                   (1, (tile.cell[0], tile.cell[1] + 1))) if cell in lookup]

    def outside_edges(self):
        paired = {(i, axis, 1) for i, j, axis in self.seams()} | {(j, axis, 0) for i, j, axis in self.seams()}
        return [(i, axis, end) for i in range(len(self.tiles)) for axis, end in product((0, 1), (0, 1))
                if (i, axis, end) not in paired]


@dataclass(frozen=True)
class Requirement:
    amplitude: float
    allow_refine: bool
    allow_expand: bool
    lock_interface: bool
    preserve_datum: bool
    priority: str
    max_added_poles: int

    def __post_init__(self):
        if (not np.isfinite(self.amplitude) or type(self.max_added_poles) is not int or self.max_added_poles < 0 or
                self.priority not in ('locality', 'complexity') or
                any(type(value) is not bool for value in (self.allow_refine, self.allow_expand,
                                                         self.lock_interface, self.preserve_datum))):
            raise ValueError('[INVALID_REQUIREMENT] Explicit permissions, finite amplitude and nonnegative budget required.')


def make_atlas(index):
    if type(index) is not int or not 0 <= index < 10:
        raise ValueError('[INVALID_CASE] This frozen challenge defines case indices 0-9.')
    rng = np.random.default_rng(20261020 + index)
    h, w = float(rng.uniform(20, 45)), float(rng.uniform(15, 35))
    if index < 4:
        columns = 3 if index < 2 else 5
        cells, target_cell, family = [(x, 0) for x in range(columns)], (columns//2, 0), 'strip'
    elif index < 7:
        columns, rows = ((2, 2), (3, 2), (3, 3))[index - 4]
        cells, target_cell, family = list(product(range(columns), range(rows))), (columns//2, rows//2), 'grid'
    else:
        columns = 3
        cells, target_cell, family = [c for c in product(range(3), repeat=2) if c != (1, 1)], (1, 0), 'ring_with_hole'
    coefficients = rng.normal(0, 1, (4, 4))
    knots, grid = np.array([0.] * 4 + [1.] * 4), np.linspace(0, 1, 4)
    collocation = BSpline(knots, np.eye(4), 3)(grid)
    # Alternate real creases and smooth atlases; do not smooth away a tagged edge.
    crease_x = target_cell[0] if index % 2 else None
    tiles = []
    for i, (x, y) in enumerate(cells):
        xx, yy = np.meshgrid((x + grid)/columns, (y + grid)/3, indexing='ij')
        z = np.polynomial.polynomial.polyval2d(xx, yy, coefficients)
        if crease_x is not None:
            z += .12 * np.maximum((x + grid[:, None] - crease_x) * h, 0)
        poles = np.empty((4, 4, 3))
        poles[:, :, 0] = ((x + grid) * h)[:, None]
        poles[:, :, 1] = (grid * w)[None, :]
        poles[:, :, 2] = np.linalg.solve(collocation, np.linalg.solve(collocation, z.T).T)
        tiles.append(Tile(f'F{i:02d}', (x, y), Patch(poles, knots.copy(), knots.copy(), 3, 3, h, w), y*w))
    target = cells.index(target_cell)
    # Interface alternates between the geometrically left and right neighbor.
    interface_cell = (target_cell[0] + (-1 if index % 2 == 0 else 1), target_cell[1])
    if interface_cell not in cells:
        interface_cell = (target_cell[0] - 1, target_cell[1])
    atlas = Atlas(f'c{index:02d}', family, tiles, target, cells.index(interface_cell), 0,
                  (.2, .25, .75, .5, .25, .5, .75, .2, .25, .5)[index], set())
    atlas.sharp_seams = {(i, j) for i, j, axis in atlas.seams()
                         if axis == 0 and crease_x is not None and tiles[j].cell[0] == crease_x}
    return atlas


def refined(tile, action):
    if action not in ('knots_u', 'knots_v', 'knots_uv', 'knots_uv_dense', 'degree_uv8'):
        raise ValueError('[UNKNOWN_ACTION] Unsupported refinement action.')
    surface = to_surface(tile.patch)
    if action.startswith('knots_'):
        axes = action.removeprefix('knots_').removesuffix('_dense')
        knots = np.linspace(.125, .875, 7) if action.endswith('_dense') else (.25, .5, .75)
        for axis in axes:
            method = surface.InsertUKnot if axis == 'u' else surface.InsertVKnot
            for knot in knots:
                method(float(knot), 1, 1e-12, False)
    elif action == 'degree_uv8':
        surface.IncreaseDegree(8, 8)
    else:
        raise ValueError('[UNKNOWN_ACTION] Unsupported refinement action.')
    patch = from_surface(surface, tile.patch.length, tile.patch.width)
    return replace(tile, patch=patch)


def trace_row(tile, axis, parameter, tangent, order=0):
    patch = tile.patch
    if axis == 0:
        return np.outer(patch.basis(0, parameter, order), patch.basis(1, tangent)) / patch.length**order
    return np.outer(patch.basis(0, tangent), patch.basis(1, parameter, order)) / patch.width**order


def trace_nodes(tiles, axis):
    """Unisolvent nodes on every common polynomial span, not arbitrary sampling."""
    tangent = 1 - axis
    knots = np.unique(np.concatenate([t.patch.u_knots if tangent == 0 else t.patch.v_knots for t in tiles]))
    degree = max(t.patch.u_degree if tangent == 0 else t.patch.v_degree for t in tiles)
    return np.unique(np.concatenate([a + (b-a)*np.linspace(0, 1, degree + 1)
                                     for a, b in zip(knots[:-1], knots[1:])]))


def candidates(atlas, requirement):
    target = atlas.target
    yield 'local', (target,)
    if requirement.allow_refine:
        for action in ('knots_u', 'knots_v', 'knots_uv', 'knots_uv_dense', 'degree_uv8'):
            yield action, (target,)
    if requirement.allow_expand:
        neighbors = sorted({j if i == target else i for i, j, _ in atlas.seams() if target in (i, j)})
        locked = {atlas.interface} if requirement.lock_interface else set()
        supports = [(target, n) for n in neighbors if n not in locked]
        supports += [tuple(sorted({target, *neighbors} - locked)),
                     tuple(i for i in range(len(atlas.tiles)) if i not in locked)]
        for support in sorted(set(tuple(sorted(s)) for s in supports if len(s) > 1), key=lambda s: (len(s), s)):
            yield 'expand_' + '_'.join(map(str, support)), support


def solve(atlas, requirement, action, support):
    refinement = action in ('knots_u', 'knots_v', 'knots_uv', 'knots_uv_dense', 'degree_uv8')
    if (refinement and not requirement.allow_refine or len(support) > 1 and not requirement.allow_expand or
            action == 'local' and tuple(support) != (atlas.target,) or
            refinement and tuple(support) != (atlas.target,) or
            not refinement and action not in ('local', 'expand_' + '_'.join(map(str, support)))):
        raise ValueError('[UNAUTHORIZED_ACTION] Action does not match editing permissions or scope.')
    tiles = list(atlas.tiles)
    if action.startswith(('knots_', 'degree_')):
        tiles[atlas.target] = refined(tiles[atlas.target], action)
    added = sum(t.patch.poles.shape[0]*t.patch.poles.shape[1] for t in tiles) - 16*len(tiles)
    record = dict(action=action, support=list(support), added_poles=added)
    if added > requirement.max_added_poles:
        return None, dict(record, feasible=False, status='representation_budget_exceeded')
    if (not support or atlas.target not in support or len(set(support)) != len(support) or
            any(i < 0 or i >= len(tiles) for i in support) or
            requirement.lock_interface and atlas.interface in support):
        raise ValueError('[INVALID_SUPPORT] Target and authorized face set required.')
    sizes = [tiles[i].patch.poles.shape[0]*tiles[i].patch.poles.shape[1] for i in support]
    offsets = np.cumsum([0] + sizes)
    slots = {i: slice(offsets[j], offsets[j+1]) for j, i in enumerate(support)}
    rows, rhs = [], []

    def add(parts, value=0.):
        row = np.zeros(offsets[-1])
        for i, coefficient in parts:
            if i in slots:
                row[slots[i]] += coefficient.reshape(-1)
        norm = np.linalg.norm(row)
        if norm > 1e-14:
            rows.append(row/norm)
            rhs.append(value/norm)
        elif abs(value) > 1e-12:
            raise ValueError('[EMPTY_CONSTRAINT] Nonzero target on a frozen variable set.')

    for i, j, axis in atlas.seams():
        sharp = (i, j) in atlas.sharp_seams
        for t in trace_nodes([tiles[i], tiles[j]], axis):
            if sharp:
                for k, order in product((i, j), (0, 1)):
                    add([(k, trace_row(tiles[k], axis, 1 if k == i else 0, t, order))])
            else:
                for order in (0, 1, 2):
                    add([(i, trace_row(tiles[i], axis, 1, t, order)),
                         (j, -trace_row(tiles[j], axis, 0, t, order))])
    for i, axis, end in atlas.outside_edges():
        for t in trace_nodes([tiles[i]], axis):
            add([(i, trace_row(tiles[i], axis, end, t))])
    if requirement.preserve_datum:
        for t in trace_nodes([tiles[atlas.target]], atlas.datum_axis):
            add([(atlas.target, trace_row(tiles[atlas.target], atlas.datum_axis, atlas.datum_parameter, t))])
    target_tile = tiles[atlas.target]
    add([(atlas.target, np.outer(target_tile.patch.basis(0, .5), target_tile.patch.basis(1, .5)))],
        requirement.amplitude)
    matrix, target = np.array(rows), np.array(rhs)
    metric = np.zeros((offsets[-1], offsets[-1]))
    for i in support:
        patch = tiles[i].patch
        metric[slots[i], slots[i]] = np.kron(_gram(patch, 0), _gram(patch, 1))*patch.length*patch.width
    transform = np.linalg.inv(np.linalg.cholesky(metric).T)
    displacement = transform @ np.linalg.lstsq(matrix @ transform, target, rcond=1e-11)[0]
    residual = target - matrix @ displacement
    feasible = bool(np.max(np.abs(residual)) < 1e-8)
    certificate = residual/np.linalg.norm(residual) if not feasible else residual*0
    record.update(feasible=feasible, status='solved' if feasible else 'linear_constraints_inconsistent',
                  residual=float(np.max(np.abs(residual))),
                  certificate_null_error=float(np.linalg.norm(matrix.T @ certificate)),
                  certificate_rhs=float(certificate @ target),
                  reference_l2=float(displacement @ metric @ displacement) if feasible else None)
    if not feasible:
        return None, record
    for i in support:
        poles = tiles[i].patch.poles.copy()
        poles[:, :, 2] += displacement[slots[i]].reshape(poles.shape[:2])
        tiles[i] = replace(tiles[i], patch=replace(tiles[i].patch, poles=poles))
    return replace(atlas, tiles=tiles), record


def audit(original, edited, requirement, support):
    """Independent derivative API with Bernstein coefficient bounds per span."""
    max_bound, sharp_jet, outside, datum, frozen = 0., 0., 0., 0., 0.

    def derivative(tile, axis, end, tangent, order):
        p = tile.patch
        if axis == 0:
            curve = BSpline(p.u_knots, p.poles, p.u_degree).derivative(order)(end)/p.length**order
            value = BSpline(p.v_knots, curve, p.v_degree)(tangent)
        else:
            curve = BSpline(p.v_knots, p.poles.transpose(1, 0, 2), p.v_degree).derivative(order)(end)/p.width**order
            value = BSpline(p.u_knots, curve, p.u_degree)(tangent)
        return value + ([0, tile.y_origin, 0] if order == 0 else [0, 0, 0])

    def bound(tiles, axis, function):
        tangent = 1-axis
        knots = np.unique(np.concatenate([t.patch.u_knots if tangent == 0 else t.patch.v_knots for t in tiles]))
        degree = max(t.patch.u_degree if tangent == 0 else t.patch.v_degree for t in tiles)
        knots_bernstein = np.array([0.]*(degree+1) + [1.]*(degree+1))
        nodes = np.linspace(0, 1, degree+1)
        basis = BSpline(knots_bernstein, np.eye(degree+1), degree)(nodes)
        return max(float(np.max(np.linalg.norm(np.linalg.solve(basis, np.array([function(a+(b-a)*t) for t in nodes])), axis=1)))
                   for a, b in zip(knots[:-1], knots[1:]))

    for i, j, axis in original.seams():
        if (i, j) in original.sharp_seams:
            for k, end in ((i, 1), (j, 0)):
                for order in (0, 1):
                    sharp_jet = max(sharp_jet, bound([original.tiles[k], edited.tiles[k]], axis,
                        lambda t: derivative(edited.tiles[k], axis, end, t, order)-derivative(original.tiles[k], axis, end, t, order)))
        else:
            for order in (0, 1, 2):
                max_bound = max(max_bound, bound([edited.tiles[i], edited.tiles[j]], axis,
                    lambda t: derivative(edited.tiles[i], axis, 1, t, order)-derivative(edited.tiles[j], axis, 0, t, order)))
    for i, axis, end in original.outside_edges():
        outside = max(outside, bound([original.tiles[i], edited.tiles[i]], axis,
            lambda t: derivative(edited.tiles[i], axis, end, t, 0)-derivative(original.tiles[i], axis, end, t, 0)))
    if requirement.preserve_datum:
        i, axis, position = original.target, original.datum_axis, original.datum_parameter
        datum = bound([original.tiles[i], edited.tiles[i]], axis,
                      lambda t: derivative(edited.tiles[i], axis, position, t, 0)-derivative(original.tiles[i], axis, position, t, 0))
    for i, (old, new) in enumerate(zip(original.tiles, edited.tiles)):
        if i not in support or requirement.lock_interface and i == original.interface:
            if old.patch.poles.shape != new.patch.poles.shape:
                raise ValueError('[FROZEN_REPRESENTATION] Locked faces must keep their representation.')
            frozen = max(frozen, float(np.max(np.linalg.norm(old.patch.poles-new.patch.poles, axis=2))))
    center = edited.tiles[edited.target].point(.5, .5)-original.tiles[original.target].point(.5, .5)
    error = abs(center[2]-requirement.amplitude)
    result = dict(smooth_trace_bound=max_bound, sharp_original_jet_bound=sharp_jet,
                  outside_position_bound=outside, datum_position_bound=datum, frozen_pole_bound=frozen,
                  center_error_mm=float(error))
    result['passed'] = bool(max(result.values()) < 1e-7)
    return result
