"""History-aware full-state and patch baselines; raw and committed scores differ."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import compare
from dialogue import INITIAL, apply_transaction, validate_state
from expanded import freeze

SYSTEM=compare.SYSTEM+'''\n本轮两个模式均给出完整用户历史和上一轮系统提交的状态。
临时例外到期时，应更新相应字段；撤销、恢复和引用建议都需查完整历史，不能仅猜最近一次状态。
完整历史模式需输出本轮完整有效状态；需要澄清时应输出未变化的有效状态。
状态更新模式需输出从上一轮提交状态到本轮有效状态的差异，包含临时许可到期的恢复。
只讨论、转述、问原因的消息不是修改授权。'''


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources():
    return {n:fingerprint(Path(__file__).with_name(n)) for n in ('history_experiment.py','expanded.py','dialogue.py','compare.py')}


def read_public(path):
    tasks=json.loads(Path(path).read_text(encoding='utf-8'))
    ids=[]
    for d in tasks:
        if set(d)!={'dialogue_id','geometry','context','turns'}:
            raise ValueError('[PUBLIC_SCHEMA] Unexpected public dialogue fields.')
        if not d['turns'] or any(set(t)!={'turn','input_text'} or t['turn']!=i for i,t in enumerate(d['turns'],1)):
            raise ValueError('[TURN_SCHEMA] Ordered public user turns required.')
        ids.append(d['dialogue_id'])
    count=sum(len(d['turns']) for d in tasks)*2
    if not 1<=count<=64 or len(set(ids))!=len(ids):
        raise ValueError('[REQUEST_CAP] Unique dialogues and 1-64 requests required.')
    return tasks


def decode_proposal(raw,mode,before):
    """Extract the raw proposed state before transaction acceptance."""
    value=json.loads(raw)
    key='state' if mode=='history' else 'updates'
    if not isinstance(value,dict) or set(value)!={key,'needs_clarification'} or type(value['needs_clarification']) is not bool:
        raise ValueError('[PROPOSAL_SCHEMA] State/updates and boolean clarification required.')
    if mode=='history':
        proposed=validate_state(value['state'])
    else:
        proposed=apply_transaction(before,dict(updates=value['updates'],needs_clarification=False))
    return proposed,value['needs_clarification']


def request_turn(backend,dialogue,mode,turn,history,before):
    payload=dict(mode='完整历史模式' if mode=='history' else '状态更新模式',
                 initial_state=INITIAL,context=dialogue['context'],geometry=dialogue['geometry'],
                 history=history,committed_state=before)
    row=dict(dialogue_id=dialogue['dialogue_id'],mode=mode,turn=turn['turn'],state_before=before)
    start=time.perf_counter()
    committed=dict(before)
    try:
        raw=backend.chat(json.dumps(payload,ensure_ascii=False),system=SYSTEM,retries=0)
        row['raw_response']=raw
        proposed,pending=decode_proposal(raw,mode,before)
        row.update(raw_proposed_state=proposed,raw_needs_clarification=pending)
        committed,accepted_pending=compare.decode(raw,mode,before)
        row.update(status='accepted',needs_clarification=accepted_pending)
    except Exception as error:
        row.update(status='rejected_or_failed',error_type=type(error).__name__)
    row.update(committed_state=committed,elapsed_seconds=time.perf_counter()-start)
    return row


def predict(public_path,output,backend=None,resume=False):
    public_path,output=Path(public_path),Path(output)
    tasks=read_public(public_path)
    expected=[(d['dialogue_id'],m,t['turn']) for d in tasks for m in ('history','patch') for t in d['turns']]
    config_path=output.with_suffix('.config.json')
    source_hashes=sources()
    previous=[]
    if resume:
        config=json.loads(config_path.read_text(encoding='utf-8'))
        if config['public_sha256']!=fingerprint(public_path) or config['source_sha256']!=source_hashes or config['system']!=SYSTEM:
            raise ValueError('[RESUME_MISMATCH] Public input, source and prompt must match.')
        previous=[json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
        keys=[(r['dialogue_id'],r['mode'],r['turn']) for r in previous]
        if keys!=expected[:len(keys)]:
            raise ValueError('[RESUME_PREFIX] Saved results must be the ordered prefix.')
        if len(keys)==len(expected):return 0
    elif output.exists() or config_path.exists():
        raise FileExistsError('[FROZEN_RUN] Choose a fresh prediction file or validated resume.')
    if backend is None:
        backend,backend_hash=compare.existing_backend()
        backend=replace(backend,max_tokens=768)
    else:backend_hash='injected_test_backend'
    settings=dict(model_requested=backend.model,max_tokens=backend.max_tokens,temperature=backend.temperature,backend_sha256=backend_hash)
    if resume and any(config[k]!=v for k,v in settings.items()):
        raise ValueError('[RESUME_BACKEND] Backend and settings must match.')
    if not resume:
        config=dict(settings,public_sha256=fingerprint(public_path),source_sha256=source_hashes,
                    system=SYSTEM,request_cap=len(expected),retries=0,
                    limitation='Requested alias; no immutable model snapshot or token usage. Both modes get full history and previous predicted committed state.')
        config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    offset,count=0,0
    with output.open('a' if resume else 'x',encoding='utf-8') as handle:
        for d in tasks:
            for mode in ('history','patch'):
                history=[];state=dict(INITIAL)
                for turn in d['turns']:
                    history.append(turn['input_text'])
                    if offset<len(previous):
                        state=previous[offset]['committed_state']
                    else:
                        row=request_turn(backend,d,mode,turn,history,state)
                        state=row['committed_state']
                        handle.write(json.dumps(row,ensure_ascii=False)+'\n');handle.flush()
                        count+=1
                        print(d['dialogue_id'],mode,turn['turn'],row['status'],flush=True)
                    offset+=1
    return count


def evaluate(oracle_path,predictions_path,output):
    output=Path(output)
    if output.exists():raise FileExistsError('[FROZEN_SCORE] Choose a fresh evaluation file.')
    tasks=json.loads(Path(oracle_path).read_text(encoding='utf-8'))
    oracle={(d['dialogue_id'],m,t['turn']):t for d in tasks for m in ('history','patch') for t in d['turns']}
    predictions=[json.loads(line) for line in Path(predictions_path).read_text(encoding='utf-8').splitlines()]
    found={}
    for r in predictions:
        key=r['dialogue_id'],r['mode'],r['turn']
        if key not in oracle or key in found:raise ValueError('[PREDICTION_ID] Unknown or duplicate row.')
        found[key]=r
    rows=[]
    for key,t in oracle.items():
        pred=found.get(key,{})
        gold=t['oracle_state'];raw=pred.get('raw_proposed_state');committed=pred.get('committed_state')
        raw_errors=[k for k in gold if raw is not None and (type(raw[k]) not in ((int,float) if k=='amplitude' else (type(gold[k]),)) or raw[k]!=gold[k])]
        raw_state_correct=raw is not None and not raw_errors
        raw_flag_correct=pred.get('raw_needs_clarification') is t['oracle_transaction']['needs_clarification']
        rows.append(dict(dialogue_id=key[0],mode=key[1],turn=key[2],category=t['category'],
            raw_available=raw is not None,raw_field_errors=raw_errors,raw_state_correct=raw_state_correct,
            raw_clarification_correct=raw_flag_correct,raw_exact=raw_state_correct and raw_flag_correct,
            committed_state_correct=committed==gold,accepted_exact=pred.get('status')=='accepted' and raw_state_correct and raw_flag_correct,
            ambiguous_mutation=bool(raw is not None and pred.get('raw_needs_clarification') and raw!=pred['state_before']),
            unauthorized_permission_grants=[k for k in ('allow_refine','allow_expand') if raw is not None and raw[k] and not gold[k]],
            unauthorized_lock_releases=[k for k in ('lock_interface','preserve_datum') if raw is not None and not raw[k] and gold[k]]))
    summaries={}
    for m in ('history','patch'):
        subset=[r for r in rows if r['mode']==m]
        summaries[m]=dict(turns=len(subset),raw_exact=sum(r['raw_exact'] for r in subset),
            raw_unavailable=sum(not r['raw_available'] for r in subset),
            raw_field_errors=sum(len(r['raw_field_errors']) for r in subset),
            raw_clarification_correct=sum(r['raw_clarification_correct'] for r in subset),
            committed_state_correct=sum(r['committed_state_correct'] for r in subset),
            accepted_exact=sum(r['accepted_exact'] for r in subset),ambiguous_mutations=sum(r['ambiguous_mutation'] for r in subset),
            unauthorized_permission_grants=sum(len(r['unauthorized_permission_grants']) for r in subset),
            unauthorized_lock_releases=sum(len(r['unauthorized_lock_releases']) for r in subset),
            complete_dialogues=sum(all(r['accepted_exact'] for r in subset if r['dialogue_id']==d['dialogue_id']) for d in tasks))
    report=dict(modes=summaries,rows=rows,expected=len(oracle),received=len(found),
                predictions_sha256=fingerprint(predictions_path),oracle_sha256=fingerprint(oracle_path),
                limitations='Four assistant-authored histories, unreviewed. Correlated turns. Raw state errors are not committed geometry damage. No end-to-end model STEP evaluation or novelty proof.')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['freeze','predict','evaluate'])
    p.add_argument('--public');p.add_argument('--oracle');p.add_argument('--predictions')
    p.add_argument('--output',required=True);p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    if args.command=='freeze':print(freeze(args.output))
    elif args.command=='predict':print('New responses:',predict(args.public,args.output,resume=args.resume))
    else:print(json.dumps(evaluate(args.oracle,args.predictions,args.output)['modes'],ensure_ascii=False))
