"""Analytic diagnostic, not an LLM benchmark or a general CAD solver.

Three equal-width, regular graph Bezier patches; only z displacements vary.
All constraints apply to displacement relative to an already C2 baseline.
Run directly with --output to save the deterministic parameter sweep.
"""
from __future__ import annotations

import argparse
import itertools
import json
from math import comb, factorial
from pathlib import Path

import numpy as np


def bernstein(degree: int, parameter: float) -> np.ndarray:
    return np.array([comb(degree, i) * parameter**i * (1 - parameter)**(degree - i)
                     for i in range(degree + 1)])


def boundary_row(degree: int, order: int, at_end: bool, length: float) -> np.ndarray:
    """Exact endpoint derivative coefficients in physical x units."""
    row = np.zeros(degree + 1)
    offset = degree - order if at_end else 0
    factor = factorial(degree) / factorial(degree - order) / length**order
    for i in range(order + 1):
        row[offset + i] = factor * (-1)**(order - i) * comb(order, i)
    return row


def system(degree: int, continuity: int, support: tuple[int, ...],
           length: float, amplitude: float, pin_center: bool = False):
    """Build A x=b; retain incompatible hard constraints, never relax them."""
    if degree < continuity or continuity not in (0, 1, 2):
        raise ValueError('degree must support continuity order 0, 1 or 2')
    if length <= 0 or 1 not in support or not set(support) <= {0, 1, 2}:
        raise ValueError('positive length and a valid support containing patch 1 required')
    shape = (3, degree + 1, 4)
    rows, values = [], []

    def append(row, value=0.0):
        rows.append(row.reshape(-1))
        values.append(value)

    for seam in (0, 1):
        for order in range(continuity + 1):
            for j in range(4):
                row = np.zeros(shape)
                row[seam, :, j] = boundary_row(degree, order, True, length)
                row[seam + 1, :, j] = -boundary_row(degree, order, False, length)
                append(row)
    # Only positions at the two outside boundaries are fixed.
    for patch, i in ((0, 0), (2, degree)):
        for j in range(4):
            row = np.zeros(shape)
            row[patch, i, j] = 1
            append(row)
    for patch in set(range(3)) - set(support):
        for i, j in itertools.product(range(degree + 1), range(4)):
            row = np.zeros(shape)
            row[patch, i, j] = 1
            append(row)
    center = np.zeros(shape)
    center[1] = np.outer(bernstein(degree, .5), bernstein(3, .5))
    append(center, amplitude)
    if pin_center:
        append(center.copy(), 0)
    return np.asarray(rows), np.asarray(values)


def gram(degree: int) -> np.ndarray:
    return np.array([[comb(degree, i) * comb(degree, j) /
                      ((2 * degree + 1) * comb(2 * degree, i + j))
                      for j in range(degree + 1)] for i in range(degree + 1)])


def solve(a: np.ndarray, b: np.ndarray, degree: int, length: float, width: float):
    """Minimum reference-domain integral of squared displacement.

    Consistency is numerical at 1e-8 mm-equivalent row-normalized tolerance.
    The residual also supplies an approximate left-nullspace certificate.
    """
    if width <= 0:
        raise ValueError('width must be positive')
    scale = np.linalg.norm(a, axis=1)
    an, bn = a / scale[:, None], b / scale
    metric = np.kron(np.eye(3), np.kron(gram(degree), gram(3))) * length * width
    transform = np.linalg.inv(np.linalg.cholesky(metric).T)
    whitened = an @ transform
    y = np.linalg.lstsq(whitened, bn, rcond=1e-12)[0]
    x = transform @ y
    residual = bn - an @ x
    feasible = bool(np.max(np.abs(residual)) <= 1e-8)
    certificate = residual / np.linalg.norm(residual) if not feasible else np.zeros(len(b))
    return x, {
        'feasible': feasible,
        'normalized_residual': float(np.max(np.abs(residual))),
        'reference_l2_mm4': float(x @ metric @ x) if feasible else None,
        'certificate_null_error': float(np.linalg.norm(an.T @ certificate)),
        'certificate_rhs': float(certificate @ bn),
    }


def evaluate(x: np.ndarray, degree: int, continuity: int, length: float):
    """Independent derivative evaluation by forward differences, not A x."""
    patches = x.reshape(3, degree + 1, 4)
    seam_errors = []
    for order in range(continuity + 1):
        derivatives = np.diff(patches, n=order, axis=1)
        derivatives *= factorial(degree) / factorial(degree - order) / length**order
        seam_errors.append(float(np.max(np.abs(derivatives[:-1, -1] - derivatives[1:, 0]))))
    center = bernstein(degree, .5) @ patches[1] @ bernstein(3, .5)
    return {'center_mm': float(center), 'seam_derivative_errors': seam_errors,
            'neighbor_control_bound_mm': float(np.max(np.abs(patches[[0, 2]])))}


def local_witness(continuity: int, amplitude: float) -> np.ndarray:
    """Independent constructive proof: normalized u^(k+1)(1-u)^(k+1)."""
    degree = 2 * (continuity + 1)
    patches = np.zeros((3, degree + 1, 4))
    patches[1, continuity + 1, :] = amplitude * 2**degree / comb(degree, continuity + 1)
    return patches.reshape(-1)


def sweep():
    results = []
    for continuity, length, width, amplitude in itertools.product(
            (0, 1, 2), (20., 40., 80.), (10., 20., 40.), (.25, 1., 3.)):
        # All patches elevated together for a simple, conforming representation.
        # Real knot insertion/degree elevation implementation is not claimed.
        strategies = [('cubic_local', 3, (1,)), ('cubic_global', 3, (0, 1, 2)),
                      ('degree_adaptive_local', max(3, 2 * (continuity + 1)), (1,))]
        for name, degree, support in strategies:
            a, b = system(degree, continuity, support, length, amplitude)
            x, result = solve(a, b, degree, length, width)
            result.update(strategy=name, degree=degree, continuity=continuity,
                          length_mm=length, width_mm=width, amplitude_mm=amplitude,
                          allowed_patch_count=len(support))
            if result['feasible']:
                result.update(evaluate(x, degree, continuity, length))
                result['edit_error_mm'] = abs(result['center_mm'] - amplitude)
            results.append(result)
    a, b = system(6, 2, (0, 1, 2), 40, 1, pin_center=True)
    _, conflict = solve(a, b, 6, 40, 20)
    summary = {}
    for name in ('cubic_local', 'cubic_global', 'degree_adaptive_local'):
        summary[name] = {f'C{k}': sum(r['feasible'] for r in results
                                    if r['strategy'] == name and r['continuity'] == k)
                         for k in (0, 1, 2)}
    return {'scope': 'Regular graph Bezier patches, fixed xy, unit weights; Ck sufficient for Gk here only.',
            'cases_per_continuity_per_strategy': 27, 'summary': summary,
            'incompatible_center_pin': conflict, 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = sweep()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, indent=2))


if __name__ == '__main__':
    main()
