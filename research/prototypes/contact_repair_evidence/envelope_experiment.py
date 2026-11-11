"""Frozen exploratory comparisons; retains unfavorable and unresolved results."""
import argparse
from collections import Counter
from dataclasses import asdict
from fractions import Fraction as F
import hashlib
import json
from math import comb
from pathlib import Path
import platform
import time
from unittest.mock import patch

import numpy as np
import scipy
import repair as legacy
from envelope_solver import (Section, norm_enclosure, solve_fixed, replay_solution)
from envelope_policy import preflight, repair_quality
from verification import contact_budget_bounds


POLICIES = ('legacy_uniform', 'legacy_targeted', 'uniform', 'dual_local',
            'evidence', 'direct_13', 'direct_21')
SOURCE_FILES = ('envelope_solver.py', 'envelope_policy.py', 'envelope_experiment.py',
                'repair.py', 'verification.py', 'cad_adapter.py')


def profile(name):
    if name == 'original_quartic':
        return Section.from_curve(legacy.initial_curve())
    powers = {
        'asymmetric_quintic': [F(0), F(0), F(1, 10), F(1, 500), F(-3, 1000), F(-1, 8000)],
        'shallow_quartic': [F(0), F(0), F(9, 100), F(0), F(-1, 400)]}
    if name not in powers:
        raise ValueError('[PROFILE] Unknown analytic profile. 建议操作：选择已冻结的曲线。')
    physical = powers[name]
    unit = [sum((physical[n]*comb(n, j)*(-4)**(n-j)*8**j
                 for n in range(j, len(physical))), F(0)) for j in range(len(physical))]
    controls = [sum((unit[j]*F(comb(i, j), comb(5, j))
                     for j in range(min(i, len(unit)-1)+1)), F(0)) for i in range(6)]
    return Section(tuple([-4]*6+[4]*6), tuple(controls), 5)


def default_configurations():
    configurations = [dict(profile=name, radius=radius, displacement_budget=budget,
                           lock_order=2, protected_x=())
        for name in ('original_quartic', 'asymmetric_quintic', 'shallow_quartic')
        for radius, budgets in ((6., (.015, .15)), (8., (.11, .15))) for budget in budgets]
    configurations += [dict(profile='original_quartic', radius=8., displacement_budget=.03,
                            lock_order=2, protected_x=()),
                       dict(profile='original_quartic', radius=8., displacement_budget=.15,
                            lock_order=2, protected_x=(2.,))]
    return configurations


def original_in_basis(original, candidate):
    positions = [k for k in sorted(set(candidate.knots))[1:-1]
                 for _ in range(candidate.knots.count(k)-original.knots.count(k))]
    return original.refine(positions)


def evaluate(original, request, policy):
    if policy.startswith('legacy_'):
        calls, real_lp = 0, legacy.linprog
        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_lp(*args, **kwargs)
        with patch.object(legacy, 'linprog', counted):
            record, curve = legacy.repair(original.as_curve(), request, policy.removeprefix('legacy_'))
        candidate = Section.from_curve(curve) if curve is not None else None
        record['lp_calls'] = calls
        record['basis_solves'] = None  # The older method has a different outer loop.
        return record, candidate
    if policy.startswith('direct_'):
        conflict = preflight(original, request)
        if conflict:
            return dict(**conflict, lp_calls=0, basis_solves=0), None
        intervals = 8 if policy == 'direct_13' else 16
        basis = original.refine([-4+F(8*i, intervals) for i in range(1, intervals)])
        result, candidate = solve_fixed(basis, request)
        certified = result['status'] == 'verified' and replay_solution(basis, candidate, request, result)
        return dict(status='verified_budget_only' if certified else result['status'],
                    best=result if certified else None, fixed_result=result,
                    lp_calls=result['lp_calls'], basis_solves=1), candidate if certified else None
    record, candidate = repair_quality(original, request, policy, max_basis_solves=36)
    record['lp_calls'] = sum(row['lp_calls'] for row in record['history'])
    return record, candidate


def export_checked(original, candidate, request, output):
    """Reuse STEP machinery, then check actual geometric budget after readback."""
    import cad_adapter
    captured = []
    real_roundtrip = cad_adapter.roundtrip
    def capture(*args, **kwargs):
        restored, step = real_roundtrip(*args, **kwargs)
        captured.extend(restored)
        return restored, step
    with patch.object(cad_adapter, 'roundtrip', capture):
        record = cad_adapter.export_and_reverify(candidate.as_curve(), request, output)
    middle = captured[1]
    bounds = []
    for j in range(4):
        restored = Section(tuple(middle.u_knots*8-4), tuple(middle.poles[:, j, 2]), middle.u_degree)
        basis = original_in_basis(original, restored)
        delta = [a-b for a, b in zip(restored.controls, basis.controls)]
        norm = norm_enclosure(restored.knots, delta, restored.degree)
        if norm['status'] != 'bounded' or F(norm['upper_rational']) > F(request.displacement_budget):
            raise RuntimeError('[READBACK_BUDGET] Actual geometric budget rejected. 建议操作：检查导出几何偏差。')
        bounds.append(norm)
    record['actual_displacement_after_readback'] = bounds
    record['locks_after_readback'] = 'numeric audit at existing adapter tolerance, not exact rational equality'
    return record


def run(output, configurations=None, policies=POLICIES):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError('[FROZEN_EXPERIMENT] Output must be empty. 建议操作：使用新目录。')
    configurations = default_configurations() if configurations is None else configurations
    root = Path(__file__).parent
    source = {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    manifest = dict(date='2026-11-09', phase='exploratory_not_confirmatory',
        prior_observation='Original quartic minimax and policy pilot already inspected before this matrix.',
        configurations=configurations, policies=policies, max_controls=22, max_basis_solves=36,
        solver_gap_mm=1e-5, global_quality_gap_mm=1e-4, intrusion_tolerance_mm=1e-6,
        primary='Certified actual vertical Linf change and exact replay; failure type without overclaim.',
        competitor='Same minimax objective with uniform refinement or direct rich basis may explain all gains.',
        falsifier='Evidence lookahead needs more LP work or no fewer controls at matched geometric quality.',
        units='mm; three related synthetic analytic profiles, not independent industrial components',
        independent_industrial_components=0, source_sha256=source,
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    records, exported = [], False
    for case_id, configuration in enumerate(configurations):
        original = profile(configuration['profile'])
        request = legacy.Request(**{k:v for k, v in configuration.items() if k != 'profile'}, max_controls=22)
        necessary = contact_budget_bounds(original.knots, original.controls, original.degree,
                                         request.radius, 2., request.tolerance, accuracy=1e-7)
        for policy in policies:
            start = time.perf_counter()
            record, candidate = evaluate(original, request, policy)
            record.update(case_id=case_id, policy=policy, configuration=configuration,
                          elapsed_s=time.perf_counter()-start, necessary_contact=necessary)
            if candidate is not None:
                basis = original_in_basis(original, candidate)
                delta = [a-b for a, b in zip(candidate.controls, basis.controls)]
                norm = norm_enclosure(candidate.knots, delta, candidate.degree, accuracy=1e-8)
                record['geometry_bound'] = norm
                record['controls'] = len(candidate.controls)
                record['global_gap_upper_mm'] = norm['upper_mm']-necessary['lower_mm']
                if policy.startswith('legacy_'):
                    record['exact_solution_replayed'] = False
                    record['lock_evidence'] = 'legacy numeric residual check; no exact lock assertion'
                else:
                    record['exact_solution_replayed'] = replay_solution(basis, candidate, request, record['best'])
                    if not record['exact_solution_replayed']:
                        raise RuntimeError('[REPLAY_FAILED] Candidate did not replay. 建议操作：检查冻结证据。')
                proof = dict(original=original.serialize(), basis=basis.serialize(),
                             candidate=candidate.serialize(), request=asdict(request),
                             solver_record=record.get('best'), geometry_bound=norm)
                (output/f'proof_{case_id}_{policy}.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
                if (not exported and policy == 'uniform' and configuration['profile'] == 'original_quartic'
                        and request.radius == 8 and request.displacement_budget == .15):
                    record['step_readback'] = export_checked(original, candidate, request, output/'minimax_surface.step')
                    exported = True
            records.append(record)
            (output/'results.json').write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'case {case_id+1}/{len(configurations)} complete', flush=True)
    summary = dict(configurations=len(configurations), runs=len(records),
        by_policy={p:dict(Counter(r['status'] for r in records if r['policy'] == p)) for p in policies},
        exact_replayed=sum(bool(r.get('exact_solution_replayed')) for r in records),
        step_exported=exported,
        caveat='Exploratory synthetic geometry only; no ANSYS, NN, full machining or established patent novelty.')
    (output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
