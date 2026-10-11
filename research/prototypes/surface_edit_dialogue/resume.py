"""Resume only a validated, ordered prefix; preserve every saved response."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from dataclasses import replace
import compare
from dialogue import INITIAL


def resume(public_path, predictions_path, backend=None):
    public_path, predictions_path = Path(public_path), Path(predictions_path)
    config=json.loads(predictions_path.with_suffix('.config.json').read_text(encoding='utf-8'))
    if (config['public_sha256']!=hashlib.sha256(public_path.read_bytes()).hexdigest() or
        config['source_sha256']!=hashlib.sha256(Path(compare.__file__).read_bytes()).hexdigest() or
        config['system']!=compare.SYSTEM):
        raise ValueError('[RESUME_MISMATCH] Public data, code and prompt must match original run.')
    tasks=json.loads(public_path.read_text(encoding='utf-8'))
    expected=[(d['dialogue_id'],m,t['turn']) for d in tasks for m in ('history','patch') for t in d['turns']]
    previous=[json.loads(line) for line in predictions_path.read_text(encoding='utf-8').splitlines()]
    keys=[(p['dialogue_id'],p['mode'],p['turn']) for p in previous]
    if keys!=expected[:len(keys)] or len(expected)>40:
        raise ValueError('[RESUME_PREFIX] Saved rows must be the exact ordered prefix, maximum 40 calls.')
    if len(keys)==len(expected):
        return 0
    if backend is None:
        backend, backend_hash=compare.existing_backend()
        if backend_hash!=config['backend_sha256']:
            raise ValueError('[BACKEND_CHANGED] Preserve the original backend.')
        backend=replace(backend,max_tokens=config['max_tokens'])
    if backend.model!=config['model_requested'] or backend.temperature!=config['temperature'] or backend.max_tokens!=config['max_tokens']:
        raise ValueError('[MODEL_CHANGED] Resume requires the same model settings.')
    count=0
    with predictions_path.open('a',encoding='utf-8') as handle:
        offset=0
        for d in tasks:
            for mode in ('history','patch'):
                state,history=dict(INITIAL),[]
                for turn in d['turns']:
                    history.append(turn['input_text'])
                    if offset<len(previous):
                        prior=previous[offset]
                        if prior['status']=='parsed':
                            state=prior['state']
                        offset+=1
                        continue
                    payload=dict(mode='完整历史模式' if mode=='history' else '状态更新模式',
                                 context=d['context'],geometry=d['geometry'],initial_state=INITIAL)
                    if mode=='history':payload['history']=history
                    else:payload.update(committed_state=state,latest_request=turn['input_text'])
                    row=dict(dialogue_id=d['dialogue_id'],mode=mode,turn=turn['turn'],resumed=True)
                    start=time.perf_counter()
                    try:
                        raw=backend.chat(json.dumps(payload,ensure_ascii=False),system=compare.SYSTEM,retries=0)
                        row['raw_response']=raw
                        state,pending=compare.decode(raw,mode,state)
                        row.update(status='parsed',state=state,needs_clarification=pending)
                    except Exception as error:
                        row.update(status='failed',error_type=type(error).__name__,committed_state_after_failure=state)
                    row['elapsed_seconds']=time.perf_counter()-start
                    handle.write(json.dumps(row,ensure_ascii=False)+'\n');handle.flush()
                    count+=1;offset+=1
                    print(d['dialogue_id'],mode,turn['turn'],row['status'],flush=True)
    return count


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--public',required=True);p.add_argument('--predictions',required=True)
    args=p.parse_args()
    print('Completed missing rows:',resume(args.public,args.predictions))
