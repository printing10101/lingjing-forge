"""Freeze paired draft tasks, enumerate strong traditional actions, audit STEP.

No language model is invoked. Oracle contracts are kept separate from input text
in public_tasks.jsonl; these tasks have not been human reviewed.
"""
import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import time

import numpy as np

from geometry import Contract, audit_edit, base_geometry, fit_displacement, seam_residuals
from kernel import refine, roundtrip


def draft_tasks(count=30):
    if count < 1:
        raise ValueError('[INVALID_COUNT] At least one geometry is required.')
    tasks = []
    for i in range(count):
        k, a = (1 if i % 5 == 2 else 2), (.5, 1., 2.)[i % 3]
        common = dict(amplitude_mm=a, continuity=k)
        pairs = [
            [dict(allow_refine=True, allow_expand=False), dict(allow_refine=False, allow_expand=True)],
            [dict(allow_refine=True, allow_expand=True), dict(allow_refine=False, allow_expand=False)],
            [dict(allow_refine=True, allow_expand=False), dict(allow_refine=False, allow_expand=True)],
            [dict(allow_refine=True, allow_expand=True, pin_center=True), dict(allow_refine=True, allow_expand=True)],
            [dict(allow_refine=True, allow_expand=True, priority='locality'),
             dict(allow_refine=True, allow_expand=True, priority='complexity')],
        ][i % 5]
        for variant, permissions in enumerate(pairs):
            contract = Contract(**common, **permissions)
            continuity_name = '切向及曲率连续（按匹配参数 C2 验收）' if k == 2 else '切向连续（按匹配参数 C1 验收）'
            prompt = (f'将中间曲面中心沿 z 方向抬高 {a:g} mm，接缝保持{continuity_name}；'
                      '左右最外侧边的位置不变。' +
                      ('允许增加控制点或升阶。' if contract.allow_refine else '禁止增加控制点、插节点和升阶。') +
                      ('允许相邻曲面随动。' if contract.allow_expand else '两侧相邻曲面的几何完全不变。') +
                      ('同时要求中间曲面中心保持原位置。' if contract.pin_center else '') +
                      ('满足以上要求后优先减少被修改的面数。' if contract.priority == 'locality'
                       else '满足以上要求后优先减少新增控制点。'))
            tasks.append(dict(task_id=f'g{i:03d}_{variant}', geometry_id=f'g{i:03d}', seed=20261010 + i,
                              input_text=prompt, oracle_contract=asdict(contract),
                              review_status='generated_unreviewed',
                              expected_status='no_feasible_candidate' if (contract.pin_center or
                                  not contract.allow_refine and not contract.allow_expand) else 'edited'))
    return tasks


def candidate_actions(contract):
    actions = [('local', (1,))]
    if contract.allow_refine:
        actions += [('insert_knots', (1,)), ('elevate_degree', (1,))]
    if contract.allow_expand:
        actions += [('expand_left', (0, 1)), ('expand_right', (1, 2)), ('expand_both', (0, 1, 2))]
    return actions


def enumerate_candidates(original, contract):
    candidates = []
    for action, support in candidate_actions(contract):
        patches = list(original)
        refinement_error = 0.
        if action in ('insert_knots', 'elevate_degree'):
            patches[1] = refine(original[1], action)
            refinement_error = max(float(np.linalg.norm(original[1].point(u, v) - patches[1].point(u, v)))
                                   for u in np.linspace(0, 1, 15) for v in np.linspace(0, 1, 11))
            if refinement_error >= 1e-8:
                raise RuntimeError('[REFINEMENT_CHANGED_SHAPE] Cannot proceed with this candidate.')
        edited, evidence = fit_displacement(patches, contract, support)
        added = sum(p.poles.shape[0]*p.poles.shape[1] for p in patches) - 48
        candidate = dict(action=action, support=list(support), added_poles=added,
                         refinement_sampled_error_mm=refinement_error, solver=evidence)
        if edited is not None:
            candidate['audit'] = audit_edit(original, edited, contract, support)
            if not candidate['audit']['passed']:
                raise RuntimeError('[INDEPENDENT_AUDIT_FAILED] Solver candidate failed independent checks.')
        candidates.append((candidate, edited))
    return candidates


def choose_candidate(candidates, contract):
    feasible = [(record, shape) for record, shape in candidates if record['solver']['feasible']]
    if not feasible:
        return None

    def cost(item):
        record = item[0]
        locality, complexity = len(record['support']), record['added_poles']
        primary = (locality, complexity) if contract.priority == 'locality' else (complexity, locality)
        return (*primary, record['solver']['reference_l2_mm4'], record['action'])
    return min(feasible, key=cost)


def run(output, count=30):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'oracle_tasks.json'
    if manifest_path.exists():
        raise FileExistsError('[RUN_EXISTS] Use a new output directory to preserve frozen experiment artifacts.')
    tasks = draft_tasks(count)
    manifest = dict(schema_version=1, review_status='generated_unreviewed',
                    family='three_graph_bicubic_patches_fixed_topology', tasks=tasks)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    public = [dict(task_id=t['task_id'], geometry_id=t['geometry_id'], input_text=t['input_text'],
                   geometry_path=f'geometry/{t["geometry_id"]}.step') for t in tasks]
    (output / 'public_tasks.jsonl').write_text('\n'.join(json.dumps(t, ensure_ascii=False) for t in public) + '\n',
                                              encoding='utf-8')
    results, geometry_checks = [], []
    started = time.perf_counter()
    for i in range(count):
        original = base_geometry(20261010 + i)
        if max(seam_residuals(original, 2)) > 1e-8:
            raise RuntimeError('[INVALID_BASELINE] Input shape does not meet initial C2 constraints.')
        _, original_step = roundtrip(original, output / 'geometry' / f'g{i:03d}.step')
        if not original_step['passed']:
            raise RuntimeError('[INPUT_ROUNDTRIP_FAILED] Input STEP geometry failed validation.')
        geometry_checks.append(original_step)
        for task in tasks[2*i:2*i + 2]:
            contract = Contract(**task['oracle_contract'])
            candidates = enumerate_candidates(original, contract)
            chosen = choose_candidate(candidates, contract)
            record = dict(task_id=task['task_id'], geometry_id=task['geometry_id'],
                          candidate_count=len(candidates), candidates=[r for r, _ in candidates],
                          expected_status=task['expected_status'],
                          status='edited' if chosen else 'no_feasible_candidate')
            if chosen:
                selected, edited = chosen
                restored, step_check = roundtrip(edited, output / 'edited' / f'{task["task_id"]}.step')
                post_audit = audit_edit(original, restored, contract, selected['support'])
                if not step_check['passed'] or not post_audit['passed']:
                    raise RuntimeError('[OUTPUT_ROUNDTRIP_FAILED] Edited STEP failed validation.')
                record.update(selected_action=selected['action'], selected_support=selected['support'],
                              selected_added_poles=selected['added_poles'], step=step_check, readback_audit=post_audit)
            record['matches_draft_expectation'] = record['status'] == task['expected_status']
            results.append(record)
        # Incremental output preserves completed cases if a later native call fails.
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    edit_results = [r for r in results if r['status'] == 'edited']
    status_counts = {status: sum(r['status'] == status for r in results)
                     for status in ('edited', 'no_feasible_candidate')}
    summary = dict(completed=True, python=platform.python_version(), numpy=np.__version__,
                   scipy=version('scipy'), ocp=version('cadquery-ocp'),
                   source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ('experiment.py', 'geometry.py', 'kernel.py')},
                   elapsed_seconds=time.perf_counter() - started, geometry_count=count,
                   task_count=len(tasks), status_counts=status_counts,
                   expectation_matches=sum(r['matches_draft_expectation'] for r in results),
                   selected_actions={a: sum(r.get('selected_action') == a for r in results)
                                     for a, _ in candidate_actions(Contract(1, 2, True, True))},
                   solver_calls=sum(r['candidate_count'] for r in results),
                   max_readback_point_error_mm=max((r['step']['sampled_jet_errors'][0] for r in edit_results), default=0),
                   max_readback_edit_error_mm=max((r['readback_audit']['edit_error_mm'] for r in edit_results), default=0),
                   input_step_checks=geometry_checks,
                   manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                   caveat='Oracle contracts; one synthetic topology family; unreviewed; no LLM results or general infeasibility claim.')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--geometry-count', type=int, default=30)
    args = parser.parse_args()
    summary = run(args.output, args.geometry_count)
    print(json.dumps({k: v for k, v in summary.items() if k != 'input_step_checks'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
