"""Bounded language-to-contract baseline; no geometry tools or oracle labels.

Reuses the project's existing DeepSeek backend and key loader, without copying
credentials. One request per task, no retries, at most 60 requests x 512 tokens.
Prediction and evaluation are separate commands and separate input artifacts.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

from geometry import Contract


SYSTEM_PROMPT = '''你是 CAD 编辑需求解析器。只返回一个 JSON 对象，不要解释或代码。
字段必须且只能是：amplitude_mm（中心沿 z 位移毫米数），continuity（0/1/2），
allow_refine（布尔，允许插节点/升阶/增加控制点），allow_expand（布尔，允许邻面随动），
pin_center（布尔，是否同时要求中心保持原位），priority（locality 或 complexity）。
“优先减少被修改面数”是 locality；“优先减少新增控制点”是 complexity。
发生冲突时保留双方约束，例如抬高中心同时保持原位，记录位移并令 pin_center=true。
不得擅自放松要求；没有明确许可时 allow_refine/allow_expand 为 false。
未规定优先级时 priority=locality。'''


def parse_contract(text):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    fields = json.loads(text)
    expected = {'amplitude_mm', 'continuity', 'allow_refine', 'allow_expand', 'pin_center', 'priority'}
    if not isinstance(fields, dict) or set(fields) != expected:
        raise ValueError('[INVALID_MODEL_SCHEMA] Missing or unexpected contract fields.')
    if type(fields['amplitude_mm']) not in (int, float) or type(fields['continuity']) is not int:
        raise ValueError('[INVALID_MODEL_TYPES] Numeric amplitude and integer continuity required.')
    return Contract(**fields)


def rule_contract(text):
    """Template-specific ceiling/control, not a general natural-language parser."""
    amplitude = re.search(r'沿 z 方向抬高 ([\d.]+) mm', text)
    continuity = re.search(r'匹配参数 C([012]) 验收', text)
    if not amplitude or not continuity:
        raise ValueError('[UNSUPPORTED_TEMPLATE] Rule parser cannot resolve this prompt.')
    return Contract(float(amplitude[1]), int(continuity[1]),
                    '允许增加控制点或升阶。' in text, '允许相邻曲面随动。' in text,
                    '同时要求中间曲面中心保持原位置。' in text,
                    'complexity' if '优先减少新增控制点' in text else 'locality')


def existing_backend():
    path = Path(__file__).resolve().parents[3] / 'engineering/python/app/benchmarks/machinability_pilot/llm_backends.py'
    spec = importlib.util.spec_from_file_location('surface_pilot_existing_backend', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    backend = replace(module.deepseek_backend(), temperature=0., max_tokens=512, timeout_seconds=60.)
    return backend, hashlib.sha256(path.read_bytes()).hexdigest()


def predict(public_path, output, limit=60):
    public_path, output = Path(public_path), Path(output)
    if not 1 <= limit <= 60:
        raise ValueError('[REQUEST_CAP] This baseline permits 1-60 tasks only.')
    if output.exists():
        raise FileExistsError('[PREDICTIONS_EXIST] Use a fresh prediction file.')
    tasks = [json.loads(line) for line in public_path.read_text(encoding='utf-8').splitlines() if line.strip()][:limit]
    if not tasks:
        raise ValueError('[EMPTY_TASKS] No public tasks to predict.')
    if any(set(t) != {'task_id', 'geometry_id', 'input_text', 'geometry_path'} for t in tasks):
        raise ValueError('[ORACLE_LEAKAGE] Prediction input must contain public task fields only.')
    backend, backend_hash = existing_backend()
    output.parent.mkdir(parents=True, exist_ok=True)
    config = dict(model_requested=backend.model, temperature=backend.temperature, max_tokens=backend.max_tokens,
                  request_cap=len(tasks), transport_retries=0, backend_source_sha256=backend_hash,
                  public_input_sha256=hashlib.sha256(public_path.read_bytes()).hexdigest(),
                  system_prompt=SYSTEM_PROMPT, note='Requested model alias, not an immutable model snapshot; usage unavailable in existing client.')
    output.with_suffix('.config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    with output.open('x', encoding='utf-8') as handle:
        for index, task in enumerate(tasks, 1):
            record = dict(task_id=task['task_id'])
            start = time.perf_counter()
            try:
                raw = backend.chat(task['input_text'], system=SYSTEM_PROMPT, retries=0)
                record['raw_response'] = raw
                record['predicted_contract'] = asdict(parse_contract(raw))
                record['status'] = 'parsed'
            except Exception as error:
                record.update(status='failed', error_type=type(error).__name__)
            record['elapsed_seconds'] = time.perf_counter() - start
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            print(f'{index}/{len(tasks)} {task["task_id"]} {record["status"]}', flush=True)


def evaluate(oracle_path, prediction_path, output):
    """Contract accuracy only; downstream geometry scores cannot be inferred on mismatch."""
    tasks = {t['task_id']: t for t in json.loads(Path(oracle_path).read_text(encoding='utf-8'))['tasks']}
    predictions = [json.loads(line) for line in Path(prediction_path).read_text(encoding='utf-8').splitlines()]
    if len({p['task_id'] for p in predictions}) != len(predictions):
        raise ValueError('[DUPLICATE_PREDICTION] Duplicate task ids cannot be scored.')
    rows, field_counts = [], dict.fromkeys(asdict(Contract(1, 2, False, False)), 0)
    for prediction in predictions:
        task = tasks[prediction['task_id']]
        gold = task['oracle_contract']
        predicted = prediction.get('predicted_contract', {})
        rule = asdict(rule_contract(task['input_text']))
        matches = {name: predicted.get(name) == value for name, value in gold.items()}
        for name, correct in matches.items():
            field_counts[name] += correct
        rows.append(dict(task_id=task['task_id'], geometry_id=task['geometry_id'],
                         llm_exact=all(matches.values()), rule_exact=rule == gold, field_matches=matches))
    paired = {}
    for row in rows:
        paired.setdefault(row['geometry_id'], []).append(row['llm_exact'])
    summary = dict(task_count=len(rows), expected_task_count=len(tasks),
                   prediction_coverage=len(rows)/len(tasks), llm_exact=sum(r['llm_exact'] for r in rows),
                   rule_exact=sum(r['rule_exact'] for r in rows), llm_field_correct=field_counts,
                   complete_pair_count=sum(len(pair) == 2 for pair in paired.values()),
                   llm_both_correct_pairs=sum(len(pair) == 2 and all(pair) for pair in paired.values()),
                   caveat='Generated template prompts, unreviewed oracle labels; measures parsing only, not LLM geometry or action-selection improvement.',
                   rows=rows)
    Path(output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return {k: v for k, v in summary.items() if k != 'rows'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('predict')
    p.add_argument('--public', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--limit', type=int, default=60)
    e = commands.add_parser('evaluate')
    e.add_argument('--oracle', required=True)
    e.add_argument('--predictions', required=True)
    e.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'predict':
        predict(args.public, args.output, args.limit)
    else:
        print(json.dumps(evaluate(args.oracle, args.predictions, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
