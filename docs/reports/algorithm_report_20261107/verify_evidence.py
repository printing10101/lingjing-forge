"""Read existing assets and independently check the report's analytic example."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
probe_path = ROOT / 'research/prototypes/tool_conditioned_surface_gate/probe.py'
spec = importlib.util.spec_from_file_location('existing_probe', probe_path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

paths = [
 'research/prototypes/surface_edit_pilot/geometry.py',
 'research/prototypes/surface_edit_pilot/kernel.py',
 'research/prototypes/surface_edit_pilot/artifacts/pilot_20261010_verified/summary.json',
 'research/prototypes/surface_edit_pilot/artifacts/pilot_20261010_verified/language_scores.json',
 'research/prototypes/surface_edit_challenge/artifacts/challenge_20261010/summary.json',
 'research/prototypes/surface_edit_challenge/artifacts/challenge_20261010/baseline_replay.json',
 'research/prototypes/surface_edit_dialogue/artifacts/history_20261011/scores.json',
 'research/prototypes/tool_conditioned_surface_gate/probe.py',
 'research/prototypes/tool_conditioned_surface_gate/artifacts/gate_20261011.json',
 'engineering/python/app/benchmarks/machinability_pilot/physics_chain.py',
 'engineering/python/app/benchmarks/machinability_pilot/generate_loop.py',
 'research/experiments/models.py',
 'research/experiments/data_generator.py',
]
hashes = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths if (ROOT/p).is_file()}

def dense_min(R, r, a):
    # Independent direct sphere expression, not the imported implementation.
    limit = min(r, a)
    return min(r - math.sqrt(max(0., r*r-x*x)) - x*x/(2*R)
               for x in (limit*i/20000 for i in range(20001)))

rows=[]
for scale in (.1, 1., 10.):
    R, a = 5*scale, 4*scale
    for r0 in (2., 5., 8.):
        r = r0*scale
        for b0 in (0., .02, .2, .65):
            budget = b0*scale
            initial = probe.analytic_contact(R,r,a)
            delta_k = max(0., 1/R - 1/r)
            required = .5*a*a*delta_k
            feasible = required <= budget + 1e-10*scale
            corrected_R = max(R,r) if feasible else None
            before_dense = dense_min(R,r,a)
            after_dense = dense_min(corrected_R,r,a) if feasible else None
            assert abs(before_dense-initial['min_clearance_mm']) < 1e-6*scale
            if feasible:
                assert after_dense >= -1e-10*scale
                assert abs(.5*a*a*(1/R-1/corrected_R)-required)<1e-10*scale
                assert required <= budget+1e-10*scale
            rows.append(dict(scale=scale,R_mm=R,r_mm=r,a_mm=a,
                budget_mm=budget,radius_ratio=r/R,normal_heuristic_pass=True,
                initial_gouge_mm=-min(0.,before_dense),required_change_mm=required,
                feasible_in_defined_family=feasible,corrected_R_mm=corrected_R,
                corrected_min_clearance_mm=after_dense,
                independent_error_mm=abs(before_dense-initial['min_clearance_mm'])))

load=lambda p:json.loads((ROOT/p).read_text(encoding='utf-8'))
pilot=load(paths[2]); lang=load(paths[3]); challenge=load(paths[4]); replay=load(paths[5])
summary=dict(date='2026-11-07',scope='Analytic 2D fixed-tangent parabolic section; not ANSYS or physical machining',
    case_count=len(rows),dimensionless_configurations=12,
    initial_unsafe=sum(r['initial_gouge_mm']>1e-9 for r in rows),
    accepted_unchanged=sum(r['feasible_in_defined_family'] and r['required_change_mm']==0 for r in rows),
    accepted_repaired=sum(r['feasible_in_defined_family'] and r['required_change_mm']>0 for r in rows),
    infeasible=sum(not r['feasible_in_defined_family'] for r in rows),
    max_independent_error_mm=max(r['independent_error_mm'] for r in rows),
    pilot=dict(task_count=pilot['task_count'],geometry_count=pilot['geometry_count'],
        status_counts=pilot['status_counts'],llm_exact=lang['llm_exact'],rule_exact=lang['rule_exact'],
        actual_edited_steps=len(list((ROOT/'research/prototypes/surface_edit_pilot/artifacts/pilot_20261010_verified/edited').glob('*.step')))),
    challenge=dict(task_count=challenge['task_count'],successful_edits=challenge['successful_edits'],
        baseline_matches=replay['grouped_matches_full_enumeration'],grouped_calls=replay['grouped_calls'],enumeration_calls=replay['full_enumeration_calls']),
    source_sha256=hashes,rows=rows)
target=OUT/'evidence.json'
target.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('rows','source_sha256')},ensure_ascii=False,indent=2))
