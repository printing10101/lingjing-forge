"""Post-hoc geometric bounds on frozen candidates; does not change run artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
from repair import initial_curve
from verification import contact_budget_bounds,displacement_bounds


def analyze(run_path,output):
    output=Path(output);run_path=Path(run_path)
    if output.exists():raise FileExistsError('Preserve previous analysis; choose another path.')
    original=initial_curve()
    necessary=contact_budget_bounds(original.t,original.c,original.k,8.,.6)
    rows=[]
    for policy in ('uniform','targeted'):
        path=run_path/f'curve_17_{policy}.json'
        saved=json.loads(path.read_text())
        bound=displacement_bounds(original.t,original.c,original.k,saved['knots'],saved['controls'])
        # Lower bound ignores locks; upper bound is for a separately verified locked candidate.
        rows.append(dict(policy=policy,geometric_change_bound=bound,
            optimality_gap_upper_mm=bound['upper_mm']-necessary['lower_mm'],
            candidate_source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    report=dict(analysis='post_hoc_not_preregistered',contact_necessary_change=necessary,
        candidate_comparison=rows,scope='vertical_Linf_change_fixed_ball_pose_one_quartic_family',
        caveat='Known bounding and subdivision methods; not a novelty or patentability certificate.')
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    analyze(args.run,args.output)
