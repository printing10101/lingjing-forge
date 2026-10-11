"""Freeze 10 atlases / 30 review tasks and enumerate their authorized solutions."""
import argparse
import csv
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import time

import numpy as np

from model import Requirement, audit, candidates, make_atlas, refined, solve
from step_io import roundtrip
from tasks import public_geometry, task_set


def priority_cost(record, requirement):
    size, added = len(record['support']), record['added_poles']
    return (size, added) if requirement.priority == 'locality' else (added, size)


def choose(records, requirement):
    feasible = [r for r in records if r['feasible'] and r['audit']['passed']]
    if not feasible:
        return None
    return min(feasible, key=lambda r: (*priority_cost(r, requirement), r['reference_l2'], r['action']))


def blind_choices(records):
    expansions = [r for r in records if r['action'].startswith('expand_')]
    refinements = [r for r in records if r['action'].startswith(('knots_', 'degree_'))]
    first = refinements[0] if refinements else (expansions[0] if expansions else records[0])
    global_first = expansions[-1] if expansions else (refinements[0] if refinements else records[0])
    return {'refine_first_without_geometry': first, 'expand_first_without_geometry': global_first}


def layout_svg(atlas, path):
    """Schematic of actual tile adjacency, not a 3D shape rendering."""
    cells = [t.cell for t in atlas.tiles]
    columns, rows = max(x for x, _ in cells)+1, max(y for _, y in cells)+1
    items = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{columns*140+40}" height="{rows*110+75}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="20" y="25" font-family="sans-serif">{atlas.case_id}: {atlas.family} (xy schematic)</text>']
    for i, tile in enumerate(atlas.tiles):
        x, y = 20+tile.cell[0]*140, 45+(rows-1-tile.cell[1])*110
        fill = '#ffe2aa' if i == atlas.target else '#cce5ff' if i == atlas.interface else '#edf2f7'
        items += [f'<rect x="{x}" y="{y}" width="140" height="110" fill="{fill}" stroke="#334155"/>',
                  f'<text x="{x+48}" y="{y+58}" font-family="sans-serif">{tile.face_id}</text>']
        if i == atlas.target:
            position = x + 140*atlas.datum_parameter
            items += [f'<line x1="{position}" y1="{y}" x2="{position}" y2="{y+110}" stroke="#16a34a" stroke-dasharray="5 4"/>',
                      f'<circle cx="{x+70}" cy="{y+55}" r="4" fill="#dc2626"/>']
    for i, j in atlas.sharp_seams:
        left = atlas.tiles[i]
        x, y = 20+(left.cell[0]+1)*140, 45+(rows-1-left.cell[1])*110
        items.append(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y+110}" stroke="#9333ea" stroke-width="5"/>')
    items += ['</svg>']
    Path(path).write_text('\n'.join(items), encoding='utf-8')


def write_review(output, tasks, results):
    by_id = {r['task_id']: r for r in results}
    lines = ['# 10 个几何案例 / 30 条需求审核板', '',
             '状态：实施助手撰写并自动核查；独立人工审核全部 pending。', '',
             '每个几何的橙色面为目标，蓝色面为安装接口，绿色虚线为检测截面，红点为目标点，紫色边为真实锐边。SVG 是 xy 邻接示意，实际形状见 STEP。', '',
             '公共约定：边界含孔口位置固定；光滑接缝为匹配物理参数 C2；锐边保留位置及原一阶切向。', '']
    for index in range(10):
        atlas = make_atlas(index)
        lines += [f'## {atlas.case_id} · {atlas.family}', '', f'![结构图](geometry/{atlas.case_id}.svg)', '',
                  f'目标面 {atlas.tiles[atlas.target].face_id}；接口面 {atlas.tiles[atlas.interface].face_id}；'
                  f'检测截面 u={atlas.datum_parameter:g}。', '']
        for task in tasks[3*index:3*index+3]:
            result = by_id[task['task_id']]
            selected = result.get('selected_action', '有限动作集合内无可行候选')
            feasible = ', '.join(r['action'] for r in result['candidates'] if r['feasible']) or '无'
            lines += [f'### {task["task_id"]}', '', task['input_text'], '',
                      f'- 自动枚举选择：{selected}', f'- 可行候选：{feasible}',
                      f'- 诊断：{result["diagnosis"]}',
                      '- 独立审核人：______；文本与契约一致：□；几何角色正确：□；可行性复核：□；结论：______', '']
    (output/'review_board.md').write_text('\n'.join(lines), encoding='utf-8')
    with (output/'decision_table.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        fields = ['task_id', 'family', 'status', 'selected_action', 'feasible_candidates', 'diagnosis', 'independent_human_review']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for task in tasks:
            result = by_id[task['task_id']]
            writer.writerow(dict(task_id=task['task_id'], family=result['family'], status=result['status'],
                selected_action=result.get('selected_action', ''),
                feasible_candidates='|'.join(r['action'] for r in result['candidates'] if r['feasible']),
                diagnosis=result['diagnosis'], independent_human_review='pending'))


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('[RUN_EXISTS] Choose a new directory; frozen artifacts cannot be overwritten.')
    output.mkdir(parents=True)
    (output/'geometry').mkdir()
    tasks = task_set()
    (output/'oracle_tasks.json').write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding='utf-8')
    public = []
    for task in tasks:
        atlas = make_atlas(int(task['case_id'][1:]))
        public.append(dict(task_id=task['task_id'], case_id=task['case_id'], input_text=task['input_text'],
                           geometry_path=f'geometry/{atlas.case_id}.step', geometry=public_geometry(atlas)))
    (output/'public_tasks.jsonl').write_text('\n'.join(json.dumps(t, ensure_ascii=False) for t in public)+'\n', encoding='utf-8')
    started, results, input_checks = time.perf_counter(), [], []
    for index in range(10):
        atlas = make_atlas(index)
        original, check = roundtrip(atlas, output/'geometry'/f'{atlas.case_id}.step')
        if not check['passed']:
            raise RuntimeError('[ORIGINAL_ROUNDTRIP_FAILED] Input STEP did not preserve shape.')
        # Initial C2 and sharp-jet validation is evaluated with zero displacement.
        zero = Requirement(0., False, False, False, False, 'locality', 0)
        baseline_audit = audit(atlas, original, zero, ())
        if not baseline_audit['passed']:
            raise RuntimeError('[BASELINE_INVALID] Input seams or boundaries are not valid.')
        input_checks.append(dict(case_id=atlas.case_id, step=check, audit=baseline_audit))
        layout_svg(atlas, output/'geometry'/f'{atlas.case_id}.svg')
        (output/'geometry'/f'{atlas.case_id}.json').write_text(json.dumps(dict(public_geometry(atlas),
            patches=[dict(face_id=t.face_id, poles=t.patch.poles.tolist(), u_knots=t.patch.u_knots.tolist(),
                          v_knots=t.patch.v_knots.tolist(), y_origin=t.y_origin) for t in atlas.tiles]),
                          ensure_ascii=False, indent=2), encoding='utf-8')
        for task in tasks[3*index:3*index+3]:
            req = Requirement(**task['oracle_requirement'])
            records = []
            for action, support in candidates(atlas, req):
                if action.startswith(('knots_', 'degree_')):
                    before, after = atlas.tiles[atlas.target], refined(atlas.tiles[atlas.target], action)
                    shape_error = max(float(np.linalg.norm(before.point(u,v)-after.point(u,v)))
                                      for u,v in product_grid())
                    if shape_error > 1e-8:
                        raise RuntimeError('[REFINEMENT_CHANGED_INPUT] Actual refinement must preserve geometry.')
                else:
                    shape_error = 0.
                edited, record = solve(atlas, req, action, support)
                record['refinement_sampled_error_mm'] = shape_error
                if edited is not None:
                    record['audit'] = audit(atlas, edited, req, support)
                    if not record['audit']['passed']:
                        raise RuntimeError(f'[CANDIDATE_AUDIT_FAILED] {task["task_id"]} {action}')
                records.append(record)
            selected = choose(records, req)
            conflict = req.preserve_datum and atlas.datum_parameter == .5
            diagnosis = ('固定截面穿过目标点，要求原位与非零位移直接冲突。' if conflict else
                         '按几何与约束枚举可行方案；弱候选失败不等于任务无解。' if selected else
                         '给定动作和控制点预算内未找到可行方案；不作一般曲面全局无解判断。')
            record = dict(task_id=task['task_id'], family=atlas.family,
                          status='edited' if selected else 'no_feasible_candidate', diagnosis=diagnosis,
                          candidates=records, exact_datum_target_conflict=conflict)
            if selected:
                edited, _ = solve(atlas, req, selected['action'], tuple(selected['support']))
                restored, step_check = roundtrip(edited, output/'edited'/f'{task["task_id"]}.step')
                restored_audit = audit(atlas, restored, req, selected['support'])
                if not step_check['passed'] or not restored_audit['passed']:
                    raise RuntimeError('[EDITED_ROUNDTRIP_FAILED] Exported result failed checks.')
                record.update(selected_action=selected['action'], selected_primary_cost=priority_cost(selected,req),
                              step=step_check, readback_audit=restored_audit)
            record['blind_baselines'] = {name: dict(action=r['action'], feasible=r['feasible'],
                priority_optimal=bool(selected and r['feasible'] and priority_cost(r,req)==priority_cost(selected,req)))
                for name,r in blind_choices(records).items()}
            first = next((i+1 for i,r in enumerate(records) if r['feasible']), len(records))
            record['fixed_order_calls_to_first_feasible_or_exhaustion'] = first
            results.append(record)
            (output/'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{len(results)}/30 {task["task_id"]} {record["status"]} {record.get("selected_action", "-")}', flush=True)
    feasible = [r for r in results if r['status']=='edited']
    summary = dict(completed=True, geometry_count=10, task_count=30,
        families={family: sum(make_atlas(i).family==family for i in range(10)) for family in ('strip','grid','ring_with_hole')},
        successful_edits=len(feasible), no_feasible_candidate=30-len(feasible),
        exact_datum_target_conflicts=sum(r['exact_datum_target_conflict'] for r in results),
        enumeration_calls=sum(len(r['candidates']) for r in results),
        blind_baselines={name:dict(feasible_on_solvable=sum(r['blind_baselines'][name]['feasible'] for r in feasible),
                                  priority_optimal_on_solvable=sum(r['blind_baselines'][name]['priority_optimal'] for r in feasible),
                                  denominator=len(feasible)) for name in results[0]['blind_baselines']},
        max_step_position_error_mm=max(r['step']['sampled_jet_errors'][0] for r in feasible),
        elapsed_seconds=time.perf_counter()-started, input_checks=input_checks,
        python=platform.python_version(), versions={n:version(n) for n in ('numpy','scipy','cadquery-ocp')},
        source_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                       for name in ('model.py','tasks.py','step_io.py','runner.py')},
        inherited_source_sha256={name:hashlib.sha256((Path(__file__).parents[1]/'surface_edit_pilot'/name).read_bytes()).hexdigest()
                                 for name in ('geometry.py','kernel.py')},
        oracle_sha256=hashlib.sha256((output/'oracle_tasks.json').read_bytes()).hexdigest(),
        independent_human_review='pending', llm_evaluated=False,
        caveat='Assistant-authored synthetic challenge; finite action set, graph C2 special case; not an industrial benchmark or evidence of LLM gain.')
    write_review(output, tasks, results)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def product_grid():
    return ((u,v) for u in np.linspace(0,1,9) for v in np.linspace(0,1,9))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    summary = run(args.output)
    print(json.dumps({k:v for k,v in summary.items() if k!='input_checks'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
