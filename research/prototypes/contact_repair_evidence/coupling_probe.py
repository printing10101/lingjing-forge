"""Post-hoc discrimination of a greedy plateau from ordinary solver failure."""
import argparse
from fractions import Fraction as F
import hashlib
from itertools import combinations
import json
from pathlib import Path

from envelope_experiment import profile, SOURCE_FILES
from envelope_solver import solve_fixed, replay_solution
from repair import Request


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('[FROZEN_PROBE] Refusing overwrite. 建议操作：选择新证据文件。')
    source = Path(__file__).parent
    basis = profile('original_quartic').refine([-2, 0, 2])
    request = Request(radius=6., displacement_budget=.15)
    positions = (-3, -1, 1, 3)
    selections = [()]+[(k,) for k in positions]+list(combinations(positions, 2))
    manifest = dict(phase='post_hoc_exploration_after_envelope_20261109_01',
        hypothesis='A one-knot immediate-gain rule may miss complementary contact regions.',
        alternative='The apparent plateau is merely unverified or inaccurate optimization.',
        design='Same locks/objective; base, every single midpoint, every pair; exact lower and upper replay.',
        novelty='Two-step lookahead and symmetric refinement are established ideas; not a novelty claim.',
        source_sha256={name:hashlib.sha256((source/name).read_bytes()).hexdigest()
                       for name in (*SOURCE_FILES, 'coupling_probe.py')})
    records = []
    for selected in selections:
        refined = basis.refine(selected)
        record, candidate = solve_fixed(refined, request)
        if record['status'] != 'verified' or not replay_solution(refined, candidate, request, record):
            raise RuntimeError('[PROBE_UNRESOLVED] Cannot certify the comparison. 建议操作：保留失败信息并检查预算。')
        records.append(dict(insertions=list(selected), controls=len(refined.controls),
            basis=refined.serialize(), candidate=candidate.serialize(), record=record))
    base = records[0]['record']
    singles = records[1:5]
    pairs = records[5:]
    best_pair = min(pairs, key=lambda row:F(row['record']['norm']['upper_rational']))
    single_gain_upper = F(base['norm']['upper_rational'])-min(F(row['record']['dual']['lower_rational']) for row in singles)
    pair_gain_lower = F(base['dual']['lower_rational'])-F(best_pair['record']['norm']['upper_rational'])
    conclusion = dict(single_gain_upper_mm=float(single_gain_upper),
        pair_gain_lower_mm=float(pair_gain_lower), best_pair=best_pair['insertions'],
        single_gain_upper_rational=str(single_gain_upper), pair_gain_lower_rational=str(pair_gain_lower),
        complementary_gain_certified=pair_gain_lower > single_gain_upper,
        scope='One related analytic section. Does not establish general refinement theory or patent inventiveness.')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(manifest=manifest, records=records, conclusion=conclusion), indent=2), encoding='utf-8')
    print(json.dumps(conclusion, indent=2), flush=True)
    return conclusion


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
