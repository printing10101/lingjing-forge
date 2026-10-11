"""Versioned CAD intent transactions; synthetic pilot, not a novelty claim."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'surface_edit_challenge'))
from model import Requirement, make_atlas, candidates, solve, audit
from runner import choose
from tasks import public_geometry
from step_io import roundtrip

INITIAL = dict(amplitude=0., allow_refine=False, allow_expand=False,
               lock_interface=False, preserve_datum=False, priority='locality', max_added_poles=0)
CONTEXT = ('每轮高度都是相对初始交付模型的绝对目标，不是累加位移。几何角色不变。'
           '旧约束除非明确替换或撤销，否则持续有效。外边界、孔口、光滑 C2 接缝和锐边保留规则不可撤销。'
           '允许加密时预算默认105个新增控制点，禁止时预算为0。含糊请求需要澄清，当前状态保持不变。')


def validate_state(value):
    if not isinstance(value, dict) or set(value) != set(INITIAL):
        raise ValueError('[STATE_SCHEMA] Missing or unknown state fields.')
    if type(value['amplitude']) not in (float, int):
        raise ValueError('[STATE_TYPE] Height must be numeric.')
    req = Requirement(**value)
    if not req.allow_refine and req.max_added_poles != 0:
        raise ValueError('[BUDGET_CONFLICT] Disabled refinement requires zero budget.')
    return dict(value)


def apply_transaction(state, transaction):
    """Atomic validation: unresolved requests cannot mutate committed intent."""
    validate_state(state)
    if not isinstance(transaction, dict) or set(transaction) != {'updates', 'needs_clarification'}:
        raise ValueError('[TRANSACTION_SCHEMA] Expected updates and needs_clarification.')
    updates, pending = transaction['updates'], transaction['needs_clarification']
    if type(pending) is not bool or not isinstance(updates, dict) or not set(updates) <= set(INITIAL):
        raise ValueError('[TRANSACTION_TYPE] Unknown field or invalid transaction type.')
    if pending:
        if updates:
            raise ValueError('[AMBIGUOUS_MUTATION] Clarification cannot commit changes.')
        return dict(state)
    return validate_state(dict(state, **updates))


def dataset():
    output = []
    for index in (0, 1, 3, 4, 7):
        atlas = make_atlas(index)
        turns = [
            ('目标中心相对交付模型提高1毫米。接口整片不动，其他过渡片允许联动；允许加密，优先不增加控制点。',
             dict(amplitude=1., lock_interface=True, allow_expand=True, allow_refine=True,
                  priority='complexity', max_added_poles=105), False),
            ('把刚才的中心高度改成相对交付模型提高1.5毫米，其他要求继续执行。',
             dict(amplitude=1.5), False),
            ('现在控制网冻结，不许加控制点；检测截面也必须保留交付轮廓。仍允许过渡片联动，其他要求不变。',
             dict(allow_refine=False, max_added_poles=0, preserve_datum=True), False),
        ]
        if index == 7:
            turns.append(('那就把刚才的限制适当放宽一点。', {}, True))
        elif index == 3:
            turns.append(('撤销检测截面保持原样的要求，重新允许加密，预算恢复105；接口仍锁定，高度仍为1.5毫米。',
                          dict(preserve_datum=False, allow_refine=True, max_added_poles=105), False))
        else:
            turns.append(('检测截面继续固定，但解除接口整片锁定，重新允许加密，预算恢复105；高度和其他规则不变。',
                          dict(lock_interface=False, allow_refine=True, max_added_poles=105), False))
        state, rows = dict(INITIAL), []
        for number, (text, updates, pending) in enumerate(turns, 1):
            transaction = dict(updates=updates, needs_clarification=pending)
            previous = state
            state = apply_transaction(state, transaction)
            rows.append(dict(turn=number, input_text=text, oracle_transaction=transaction,
                             oracle_state=state,
                             changed_fields=[k for k in state if state[k] != previous[k]],
                             retained_fields=[k for k in state if state[k] == previous[k]]))
        output.append(dict(dialogue_id=atlas.case_id, case_index=index,
                           geometry=public_geometry(atlas), context=CONTEXT, turns=rows,
                           independent_human_review='pending', author='implementing_assistant'))
    return output


def public_dataset(dialogues):
    return [dict(dialogue_id=d['dialogue_id'], geometry=d['geometry'], context=d['context'],
                 turns=[dict(turn=t['turn'], input_text=t['input_text']) for t in d['turns']]) for d in dialogues]


def geometry_check(index, state, step_path=None):
    atlas, req = make_atlas(index), Requirement(**state)
    if req.preserve_datum and atlas.datum_parameter == .5 and req.amplitude != 0:
        return dict(status='direct_constraint_conflict', calls=0)
    records = []
    for action, support in candidates(atlas, req):
        edited, record = solve(atlas, req, action, support)
        if edited is not None:
            record['audit'] = audit(atlas, edited, req, support)
        records.append(record)
    selected = choose(records, req)
    result = dict(status='feasible' if selected else 'no_candidate_in_authorized_space',
                  calls=len(records), selected=selected)
    if selected and step_path:
        edited, _ = solve(atlas, req, selected['action'], tuple(selected['support']))
        restored, checks = roundtrip(edited, step_path)
        checked = audit(atlas, restored, req, selected['support'])
        if not checks['passed'] or not checked['passed']:
            raise RuntimeError('[ROUNDTRIP_FAILED] Edited STEP failed verification.')
        result.update(step=checks, readback_audit=checked)
    return result


def freeze(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    dialogues = dataset()
    for name, data in [('oracle.json', dialogues), ('public.json', public_dataset(dialogues))]:
        (output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    rows, review = [], ['# 多轮曲面约束维护：审核草案', '', '助手编写，独立人工审核 pending。高度始终相对初始交付模型。每轮重新求解原始模型；并非在上一轮控制网上累计编辑。', '']
    for d in dialogues:
        review += [f'## {d["dialogue_id"]}', '']
        for turn in d['turns']:
            pending = turn['oracle_transaction']['needs_clarification']
            path = output/f'{d["dialogue_id"]}_r{turn["turn"]}.step'
            result = dict(status='needs_clarification', calls=0) if pending else geometry_check(d['case_index'], turn['oracle_state'], path)
            rows.append(dict(dialogue_id=d['dialogue_id'], turn=turn['turn'], **result))
            review += [f'### 第{turn["turn"]}轮', turn['input_text'], '',
                       f'更新：`{json.dumps(turn["oracle_transaction"], ensure_ascii=False)}`',
                       f'几何判定：{result["status"]}', '审核人：______；语义标注：□；保留/撤销正确：□', '']
            print(d['dialogue_id'], turn['turn'], result['status'], flush=True)
    (output/'geometry_results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'review.md').write_text('\n'.join(review), encoding='utf-8')
    summary = dict(dialogues=5, turns=len(rows), llm_evaluated=False, independent_review='pending',
                   statuses={s:sum(r['status']==s for r in rows) for s in sorted({r['status'] for r in rows})},
                   source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    print(freeze(parser.parse_args().output))
