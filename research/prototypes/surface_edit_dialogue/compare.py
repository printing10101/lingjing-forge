"""Two strong language baselines; prediction reads public data only."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
from dialogue import INITIAL, validate_state, apply_transaction

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'surface_edit_pilot'))
from language_baseline import existing_backend

SYSTEM = '''你是多轮CAD约束解析器。只返回JSON，不要代码块。字段语义：
amplitude=相对交付模型的目标高度毫米，allow_refine=允许新增控制点，allow_expand=允许其他片联动，
lock_interface=接口整片锁定，preserve_datum=检测截面固定，priority=complexity或locality，max_added_poles=新增控制点预算。
priority中complexity表示优先减少新增控制点数量，locality表示优先减少修改面数；“优先不增加控制点”应记录complexity，不是禁止加密。
保留未被明确撤销的约束。禁止加密时预算0，重新允许时按上下文预算。
几何冲突不能擅自删约束。含糊请求需要澄清，不修改当前状态。
完整历史模式返回 {"state":完整七字段状态,"needs_clarification":布尔}。
状态更新模式返回 {"updates":只包含本轮明确变动的字段,"needs_clarification":布尔}。
需要澄清时状态更新模式updates必须为空。'''


def decode(raw, mode, state):
    value = json.loads(raw)
    if mode == 'patch':
        return apply_transaction(state, value), value['needs_clarification']
    if not isinstance(value, dict) or set(value) != {'state', 'needs_clarification'} or type(value['needs_clarification']) is not bool:
        raise ValueError('[FULL_STATE_SCHEMA] Expected state and clarification flag.')
    updated = validate_state(value['state'])
    if value['needs_clarification'] and updated != state:
        raise ValueError('[AMBIGUOUS_MUTATION] Clarification cannot mutate state.')
    return updated, value['needs_clarification']


def predict(public_path, output, backend=None):
    public_path, output = Path(public_path), Path(output)
    if output.exists() or output.with_suffix('.config.json').exists():
        raise FileExistsError('[EXISTING_PREDICTIONS] Choose a fresh output.')
    tasks = json.loads(public_path.read_text(encoding='utf-8'))
    if not tasks or sum(len(d['turns']) for d in tasks)*2 > 40:
        raise ValueError('[REQUEST_CAP] At most 40 calls across both modes.')
    for d in tasks:
        if set(d) != {'dialogue_id','geometry','context','turns'} or any(set(t) != {'turn','input_text'} for t in d['turns']):
            raise ValueError('[ORACLE_LEAKAGE] Only public inputs accepted.')
    if backend is None:
        backend, backend_hash = existing_backend()
        backend = replace(backend, max_tokens=768)
    else:
        backend_hash = 'injected_test_backend'
    config = dict(model_requested=backend.model, max_tokens=backend.max_tokens, temperature=backend.temperature,
                  retries=0, request_cap=sum(len(d['turns']) for d in tasks)*2,
                  public_sha256=hashlib.sha256(public_path.read_bytes()).hexdigest(), backend_sha256=backend_hash,
                  system=SYSTEM, source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  note='Requested alias; immutable snapshot and token usage unavailable. No geometry feedback in either mode.')
    output.with_suffix('.config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    with output.open('x', encoding='utf-8') as handle:
        for d in tasks:
            for mode in ('history','patch'):
                state, history = dict(INITIAL), []
                for turn in d['turns']:
                    history.append(turn['input_text'])
                    payload = dict(mode='完整历史模式' if mode=='history' else '状态更新模式',
                                   context=d['context'], geometry=d['geometry'], initial_state=INITIAL)
                    if mode == 'history':
                        payload['history'] = history
                    else:
                        payload.update(committed_state=state, latest_request=turn['input_text'])
                    row = dict(dialogue_id=d['dialogue_id'], mode=mode, turn=turn['turn'])
                    started = time.perf_counter()
                    try:
                        raw = backend.chat(json.dumps(payload, ensure_ascii=False), system=SYSTEM, retries=0)
                        row['raw_response'] = raw
                        state, pending = decode(raw, mode, state)
                        row.update(status='parsed', state=state, needs_clarification=pending)
                    except Exception as error:
                        row.update(status='failed', error_type=type(error).__name__, committed_state_after_failure=state)
                    row['elapsed_seconds'] = time.perf_counter()-started
                    handle.write(json.dumps(row, ensure_ascii=False)+'\n')
                    handle.flush()
                    print(d['dialogue_id'], mode, turn['turn'], row['status'], flush=True)


def evaluate(oracle_path, predictions_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('[SCORE_EXISTS] Preserve frozen evaluation.')
    tasks = json.loads(Path(oracle_path).read_text(encoding='utf-8'))
    expected = {(d['dialogue_id'],m,t['turn']):t for d in tasks for m in ('history','patch') for t in d['turns']}
    predictions = [json.loads(line) for line in Path(predictions_path).read_text(encoding='utf-8').splitlines()]
    found = {}
    for p in predictions:
        key = p['dialogue_id'], p['mode'], p['turn']
        if key not in expected or key in found:
            raise ValueError('[PREDICTION_ID] Unknown or duplicate prediction.')
        found[key] = p
    rows = []
    for key, gold in expected.items():
        pred = found.get(key, {})
        state = pred.get('state', {})
        matches = {k:type(state.get(k)) in ((int,float) if k=='amplitude' else (type(v),)) and state.get(k)==v
                   for k,v in gold['oracle_state'].items()}
        flag = pred.get('needs_clarification') is gold['oracle_transaction']['needs_clarification']
        rows.append(dict(dialogue_id=key[0],mode=key[1],turn=key[2],
                         exact=all(matches.values()) and flag, field_matches=matches, clarification_correct=flag,
                         retained_field_errors=sum(not matches[k] for k in gold['retained_fields']),
                         updated_field_errors=sum(not matches[k] for k in gold['changed_fields'])))
    summary = dict(expected_predictions=len(expected), received_predictions=len(found),
                   caveat='Assistant-authored synthetic, independent review pending; state accuracy only, not model-driven STEP success or novelty.',
                   modes={m:dict(exact=sum(r['exact'] for r in rows if r['mode']==m), total=len(rows)//2,
                                 retained_field_errors=sum(r['retained_field_errors'] for r in rows if r['mode']==m),
                                 updated_field_errors=sum(r['updated_field_errors'] for r in rows if r['mode']==m),
                                 clarification_correct=sum(r['clarification_correct'] for r in rows if r['mode']==m))
                          for m in ('history','patch')}, rows=rows)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['predict','evaluate'])
    p.add_argument('--public')
    p.add_argument('--oracle')
    p.add_argument('--predictions')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if args.command=='predict':
        predict(args.public,args.output)
    else:
        print(json.dumps(evaluate(args.oracle,args.predictions,args.output)['modes'],ensure_ascii=False))
