"""Replay deterministic stopping policies against frozen candidate outcomes.

Counts are counterfactual solver calls, not new timing measurements. Static
primary cost is available before solving; feasibility and L2 arrive as feedback.
"""
from itertools import groupby
import hashlib
import json
from pathlib import Path

from model import Requirement
from runner import choose, priority_cost


def priority_group_replay(records, requirement):
    ordered=sorted(records,key=lambda r:(*priority_cost(r,requirement),r['action']))
    calls=0
    for _,iterator in groupby(ordered,key=lambda r:priority_cost(r,requirement)):
        group=list(iterator)
        calls+=len(group)
        selected=choose(group,requirement)
        if selected is not None:
            return selected,calls
    return None,calls


def analyze(results, tasks):
    lookup={t['task_id']:Requirement(**t['oracle_requirement']) for t in tasks}
    rows=[]
    for record in results:
        req=lookup[record['task_id']]
        chosen,calls=priority_group_replay(record['candidates'],req)
        full=choose(record['candidates'],req)
        first=next((r for r in record['candidates'] if r['feasible']),None)
        rows.append(dict(task_id=record['task_id'],grouped_calls=calls,
            full_calls=len(record['candidates']),
            grouped_action=chosen['action'] if chosen else None,
            matches_full_enumeration=chosen==full,
            first_feasible_priority_optimal=bool(first and full and priority_cost(first,req)==priority_cost(full,req)),
            solvable=full is not None,
            fixed_order_calls=record['fixed_order_calls_to_first_feasible_or_exhaustion']))
    return dict(task_count=len(rows),solvable=sum(r['solvable'] for r in rows),
        grouped_matches_full_enumeration=sum(r['matches_full_enumeration'] for r in rows),
        grouped_calls=sum(r['grouped_calls'] for r in rows),
        full_enumeration_calls=sum(r['full_calls'] for r in rows),
        fixed_order_calls=sum(r['fixed_order_calls'] for r in rows),
        fixed_order_priority_optimal=sum(r['first_feasible_priority_optimal'] for r in rows),
        caveat='Replay on frozen outcomes, no new API calls or native solver timings. Oracle requirements; no language comprehension evaluation.',
        rows=rows)


def save(directory):
    directory=Path(directory)
    output=directory/'baseline_replay.json'
    if output.exists():
        raise FileExistsError('[BASELINE_EXISTS] Preserve previous analysis.')
    results=directory/'results.json'
    tasks=directory/'oracle_tasks.json'
    report=analyze(json.loads(results.read_text(encoding='utf-8')),json.loads(tasks.read_text(encoding='utf-8')))
    report['results_sha256']=hashlib.sha256(results.read_bytes()).hexdigest()
    report['source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
