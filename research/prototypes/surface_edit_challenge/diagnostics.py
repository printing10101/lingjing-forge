"""Counterfactual diagnosis, explicitly separate from authorized task solutions."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from model import Requirement, audit, candidates, make_atlas, solve
from tasks import task_set


def diagnose_representation_limit():
    atlas = make_atlas(7)
    task = next(t for t in task_set() if t['task_id']=='c07_B')
    original = Requirement(**task['oracle_requirement'])
    experiments = [
        ('original_permissions',original),
        ('diagnostic_only_release_datum',replace(original,preserve_datum=False)),
        ('diagnostic_only_allow_refinement',replace(original,allow_refine=True,max_added_poles=105)),
    ]
    result = dict(task_id=task['task_id'],
        warning='Relaxed requirements are diagnostic counterfactuals, NOT valid solutions to c07_B.',experiments=[])
    for name,req in experiments:
        records=[]
        for action,support in candidates(atlas,req):
            edited,record=solve(atlas,req,action,support)
            if edited is not None:
                record['audit']=audit(atlas,edited,req,support)
                if not record['audit']['passed']:
                    raise RuntimeError('[DIAGNOSTIC_AUDIT_FAILED] Counterfactual solution failed checks.')
            records.append(record)
        result['experiments'].append(dict(name=name,requirement=asdict(req),
            feasible_actions=[r['action'] for r in records if r['feasible']],candidates=records))
    result['interpretation']=('At least one refinement can satisfy the datum and target, while the complete original '
        'z-control space (expand_all) is numerically inconsistent. This is representation-dependent infeasibility, '
        'not geometry-independent impossibility. Original permissions prohibit the successful refinement.')
    return result


def save(path):
    path=Path(path)
    if path.exists():
        raise FileExistsError('[DIAGNOSTIC_EXISTS] Preserve the existing record.')
    result=diagnose_representation_limit()
    result['source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
