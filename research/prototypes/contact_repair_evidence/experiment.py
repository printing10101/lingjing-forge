"""Freeze all configurations and record successes and failures, without network calls."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import time
import numpy as np
import scipy
from repair import initial_curve,Request,repair,ball_height,solve_samples
from cad_adapter import export_and_reverify
from verification import verify_ball


def run(output):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError('[FROZEN_EXPERIMENT] Output must be empty. 建议操作：使用新目录。')
    configurations=[dict(radius=r,displacement_budget=b,lock_order=k)
                    for r in (4.1,6.,8.) for b in (.03,.15,.6) for k in (0,2)]
    configurations += [dict(radius=r,displacement_budget=.6,lock_order=2,protected_x=(2.,))
                       for r in (6.,8.)]
    policies=('fixed','uniform','targeted')
    source={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    manifest=dict(date='2026-11-08',phase='exploratory_prototype',configurations=configurations,
        policies=policies,max_controls=30,max_solves=40,tolerance_mm=1e-6,
        primary='continuous contact verification and failure classification',
        secondary='solver attempts, parameter count, actual geometry change and elapsed time',
        independent_geometry_families=1,source_sha256=source,
        python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    records=[];exported=False
    for i,config in enumerate(configurations):
        for policy in policies:
            request=Request(**config)
            t=time.perf_counter();record,candidate=repair(initial_curve(),request,policy)
            record.update(case_id=i,policy=policy,request=asdict(request),elapsed_s=time.perf_counter()-t)
            if candidate is not None:
                x=np.linspace(-4,4,20001)
                change=candidate(x)-initial_curve()(x)
                record.update(controls=len(candidate.c),max_sampled_change_mm=float(np.max(np.abs(change))),
                              sampled_l2_mm3=float(np.trapezoid(change**2,x)),
                              dense_min_clearance_mm=float(np.min(ball_height(x,request.radius)-candidate(x))))
                (output/f'curve_{i}_{policy}.json').write_text(json.dumps(dict(knots=candidate.t.tolist(),
                    controls=candidate.c.tolist(),degree=candidate.k),indent=2),encoding='utf-8')
                if not exported and policy=='targeted' and config==dict(radius=8.,displacement_budget=.6,lock_order=2):
                    record['step_reverification']=export_and_reverify(candidate,request,output/'verified_surface.step')
                    exported=True
            records.append(record)
            (output/'results.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
        print(f'case {i+1}/{len(configurations)} complete',flush=True)
    # Explicit sparse-sampling ablation on the SAME representation, without refinement.
    original=initial_curve().insert_knot(0).insert_knot(-2).insert_knot(2).insert_knot(-3).insert_knot(3)
    request=Request(lock_order=2)
    sparse,info=solve_samples(original,request,np.linspace(-4,4,9))
    ablation=dict(sample_count=9,fit_available=sparse is not None)
    if sparse is not None:
        ablation['independent_continuous_check']=vars(verify_ball(sparse.t,sparse.c,sparse.k,8.))
    summary=dict(configurations=len(configurations),runs=len(records),
        by_policy={p:dict(Counter(r['status'] for r in records if r['policy']==p)) for p in policies},
        sparse_sampling_ablation=ablation,step_exported=exported,
        caveat='One synthetic quartic family; no neural network, ANSYS or complete machining validation; no patent novelty established.')
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
