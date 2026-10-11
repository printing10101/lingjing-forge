import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import dialogue as d
import compare as c
import resume as r
import hashlib
import expanded as expanded
import history_experiment as experiment


def test_replacement_retention_and_public_separation():
    tasks=d.dataset()
    assert len(tasks)==5 and sum(len(t['turns']) for t in tasks)==20
    for task in tasks:
        a,b=task['turns'][:2]
        assert b['oracle_state']['amplitude']==1.5
        assert b['changed_fields']==['amplitude']
        assert a['oracle_state']['lock_interface'] and b['oracle_state']['lock_interface']
    public=json.dumps(d.public_dataset(tasks))
    assert 'oracle' not in public and 'changed_fields' not in public
    last=tasks[-1]['turns']
    assert last[-1]['oracle_state']==last[-2]['oracle_state']


@pytest.mark.parametrize('bad',[{},dict(d.INITIAL,amplitude=True),dict(d.INITIAL,amplitude=float('nan')),
                             dict(d.INITIAL,max_added_poles=5),dict(d.INITIAL,priority='whatever')])
def test_invalid_state(bad):
    with pytest.raises(ValueError): d.validate_state(bad)


@pytest.mark.parametrize('tx',[{},{'updates':{'x':1},'needs_clarification':False},
    {'updates':{},'needs_clarification':1}, {'updates':{'amplitude':2},'needs_clarification':True},
    {'updates':{'allow_refine':False,'max_added_poles':10},'needs_clarification':False}])
def test_atomic_rejection(tx):
    before=dict(d.INITIAL)
    with pytest.raises(ValueError):d.apply_transaction(before,tx)
    assert before==d.INITIAL


def test_model_decode():
    state=dict(d.INITIAL)
    assert c.decode(json.dumps({'state':state,'needs_clarification':False}),'history',state)==(state,False)
    assert c.decode(json.dumps({'updates':{'amplitude':2},'needs_clarification':False}),'patch',state)[0]['amplitude']==2
    for obj in [{},{'state':dict(state,amplitude=2),'needs_clarification':True}]:
        with pytest.raises(ValueError):c.decode(json.dumps(obj),'history',state)


def test_freeze_and_geometry(tmp_path):
    result=d.freeze(tmp_path/'run')
    assert result['turns']==20 and result['statuses']['needs_clarification']==1
    assert result['statuses']['direct_constraint_conflict']==1
    rows=json.loads((tmp_path/'run/geometry_results.json').read_text(encoding='utf-8'))
    assert all(r['readback_audit']['passed'] for r in rows if r['status']=='feasible')
    with pytest.raises(FileExistsError):d.freeze(tmp_path/'run')


def test_prediction_and_scoring(tmp_path):
    tasks=d.dataset()[:1]
    public=tmp_path/'public.json';oracle=tmp_path/'oracle.json'
    public.write_text(json.dumps(d.public_dataset(tasks)),encoding='utf-8')
    oracle.write_text(json.dumps(tasks),encoding='utf-8')
    def chat(payload,**kwargs):
        prompt=json.loads(payload)
        turn=len(prompt['history']) if 'history' in prompt else next(t['turn'] for t in tasks[0]['turns'] if t['input_text']==prompt['latest_request'])
        gold=tasks[0]['turns'][turn-1]
        return json.dumps({'state':gold['oracle_state'],'needs_clarification':False} if 'history' in prompt else gold['oracle_transaction'])
    backend=SimpleNamespace(model='test',max_tokens=768,temperature=0,chat=chat)
    predictions=tmp_path/'predictions.jsonl'
    c.predict(public,predictions,backend)
    scored=c.evaluate(oracle,predictions,tmp_path/'score.json')
    assert all(v['exact']==4 for v in scored['modes'].values())
    with pytest.raises(FileExistsError): c.predict(public,predictions,backend)
    with pytest.raises(FileExistsError): c.evaluate(oracle,predictions,tmp_path/'score.json')
    public.write_text(json.dumps(tasks),encoding='utf-8')
    with pytest.raises(ValueError):c.predict(public,tmp_path/'leak.jsonl',backend)
    public.write_text('[]',encoding='utf-8')
    with pytest.raises(ValueError):c.predict(public,tmp_path/'empty.jsonl',backend)
    line=predictions.read_text(encoding='utf-8').splitlines()[0]
    predictions.write_text(line+'\n'+line+'\n',encoding='utf-8')
    with pytest.raises(ValueError):c.evaluate(oracle,predictions,tmp_path/'duplicate.json')


def test_failed_predictions_are_counted(tmp_path):
    tasks=d.dataset()[:1]
    public=tmp_path/'public.json'; oracle=tmp_path/'oracle.json'
    public.write_text(json.dumps(d.public_dataset(tasks)),encoding='utf-8')
    oracle.write_text(json.dumps(tasks),encoding='utf-8')
    backend=SimpleNamespace(model='test',max_tokens=768,temperature=0,chat=lambda *a,**kw:'not json')
    c.predict(public,tmp_path/'failed.jsonl',backend)
    scored=c.evaluate(oracle,tmp_path/'failed.jsonl',tmp_path/'score.json')
    assert all(v['exact']==0 for v in scored['modes'].values())


def test_resume_preserves_prefix(tmp_path):
    tasks=d.public_dataset(d.dataset()[:1]); public=tmp_path/'public.json'
    public.write_text(json.dumps(tasks),encoding='utf-8')
    pred=tmp_path/'pred.jsonl'
    first=dict(dialogue_id='c00',mode='history',turn=1,status='parsed',state=d.INITIAL)
    prefix=json.dumps(first)+'\n';pred.write_text(prefix,encoding='utf-8')
    config=dict(public_sha256=hashlib.sha256(public.read_bytes()).hexdigest(),
                source_sha256=hashlib.sha256(Path(c.__file__).read_bytes()).hexdigest(),
                system=c.SYSTEM,model_requested='test',temperature=0,max_tokens=768)
    cfg=pred.with_suffix('.config.json');cfg.write_text(json.dumps(config),encoding='utf-8')
    backend=SimpleNamespace(model='test',temperature=0,max_tokens=768,
        chat=lambda payload,**kw:json.dumps({'state':d.INITIAL,'needs_clarification':False})
        if 'history' in json.loads(payload) else 'invalid json')
    assert r.resume(public,pred,backend)==7
    assert pred.read_text(encoding='utf-8').startswith(prefix)
    assert r.resume(public,pred,backend)==0
    pred.write_text(prefix,encoding='utf-8');backend.model='different'
    with pytest.raises(ValueError):r.resume(public,pred,backend)
    pred.write_text(prefix+prefix,encoding='utf-8')
    with pytest.raises(ValueError):r.resume(public,pred,backend)
    cfg.write_text(json.dumps(dict(config,system='changed')),encoding='utf-8')
    with pytest.raises(ValueError):r.resume(public,pred,backend)


def test_expanded_intent_and_geometry(tmp_path):
    tasks=expanded.dataset()
    assert len(tasks)==4 and all(len(t['turns'])==8 for t in tasks)
    a,b,cases,last=tasks
    assert a['turns'][1]['oracle_state']['lock_interface'] is False
    assert a['turns'][2]['oracle_state']['lock_interface'] is True
    assert last['turns'][2]['oracle_state']['max_added_poles']==60
    assert not last['turns'][2]['oracle_state']['allow_expand']
    assert b['turns'][6]['oracle_state']['amplitude']==2.5
    assert cases['turns'][4]['oracle_transaction']['needs_clarification']
    result=expanded.freeze(tmp_path/'expanded')
    assert result['turns']==32 and result['statuses']['needs_clarification']==2
    assert result['statuses']['direct_constraint_conflict']==4
    with pytest.raises(FileExistsError):expanded.freeze(tmp_path/'expanded')


@pytest.mark.parametrize('obj',[[],[dict(dialogue_id='x',geometry={},context='',turns=[])],
    [dict(dialogue_id='x',geometry={},context='',turns=[dict(turn=2,input_text='x')])],
    [dict(dialogue_id='x',oracle=True)]])
def test_expanded_public_rejection(tmp_path,obj):
    path=tmp_path/'public.json';path.write_text(json.dumps(obj),encoding='utf-8')
    with pytest.raises(ValueError):experiment.read_public(path)


def test_proposal_distinct_from_commit():
    state=dict(d.INITIAL)
    raw=json.dumps(dict(state=dict(state,amplitude=2.),needs_clarification=True))
    assert experiment.decode_proposal(raw,'history',state)==(dict(state,amplitude=2.),True)
    with pytest.raises(ValueError):c.decode(raw,'history',state)
    raw=json.dumps(dict(updates={'amplitude':2.},needs_clarification=True))
    assert experiment.decode_proposal(raw,'patch',state)[0]['amplitude']==2.
    for obj in ({},{'updates':{},'needs_clarification':'yes'}):
        with pytest.raises(ValueError):experiment.decode_proposal(json.dumps(obj),'patch',state)


def test_expanded_prediction_resume_and_score(tmp_path):
    tasks=expanded.dataset()[:1]
    public=tmp_path/'public.json';oracle=tmp_path/'oracle.json';pred=tmp_path/'pred.jsonl'
    public.write_text(json.dumps(d.public_dataset(tasks)),encoding='utf-8')
    oracle.write_text(json.dumps(tasks),encoding='utf-8')
    def chat(payload,**kwargs):
        payload=json.loads(payload);gold=tasks[0]['turns'][len(payload['history'])-1]
        pending=gold['oracle_transaction']['needs_clarification']
        if payload['mode']=='完整历史模式':return json.dumps(dict(state=gold['oracle_state'],needs_clarification=pending))
        updates={} if pending else {k:v for k,v in gold['oracle_state'].items() if v!=payload['committed_state'][k]}
        return json.dumps(dict(updates=updates,needs_clarification=pending))
    backend=SimpleNamespace(model='test',temperature=0,max_tokens=768,chat=chat)
    assert experiment.predict(public,pred,backend)==16
    report=experiment.evaluate(oracle,pred,tmp_path/'scores.json')
    assert all(v['raw_exact']==8 and v['complete_dialogues']==1 for v in report['modes'].values())
    with pytest.raises(FileExistsError):experiment.predict(public,pred,backend)
    with pytest.raises(FileExistsError):experiment.evaluate(oracle,pred,tmp_path/'scores.json')
    assert experiment.predict(public,pred,backend,resume=True)==0
    lines=pred.read_text(encoding='utf-8').splitlines()
    prefix='\n'.join(lines[:10])+'\n';pred.write_text(prefix,encoding='utf-8')
    assert experiment.predict(public,pred,backend,resume=True)==6
    assert pred.read_text(encoding='utf-8').startswith(prefix)
    pred.write_text(lines[1]+'\n',encoding='utf-8')
    with pytest.raises(ValueError):experiment.predict(public,pred,backend,resume=True)
    pred.write_text(lines[0]+'\n',encoding='utf-8');backend.model='changed'
    with pytest.raises(ValueError):experiment.predict(public,pred,backend,resume=True)
    public.write_text('[]',encoding='utf-8')
    with pytest.raises(ValueError):experiment.predict(public,pred,backend,resume=True)


def test_raw_misgrant_does_not_commit(tmp_path):
    task=expanded.dataset()[0];turn=task['turns'][-1]
    before=turn['oracle_state']
    backend=SimpleNamespace(chat=lambda *a,**kw:json.dumps(dict(state=dict(before,lock_interface=False),needs_clarification=True)))
    row=experiment.request_turn(backend,task,'history',turn,[t['input_text'] for t in task['turns']],before)
    assert row['status']=='rejected_or_failed' and row['committed_state']==before
    oracle=tmp_path/'oracle.json';pred=tmp_path/'pred.jsonl'
    oracle.write_text(json.dumps([task]),encoding='utf-8');pred.write_text(json.dumps(row)+'\n',encoding='utf-8')
    score=experiment.evaluate(oracle,pred,tmp_path/'score.json')
    match=next(r for r in score['rows'] if r['mode']=='history' and r['turn']==8)
    assert match['raw_field_errors']==['lock_interface']
    assert match['raw_clarification_correct'] and match['committed_state_correct']
    assert match['ambiguous_mutation'] and match['unauthorized_lock_releases']==['lock_interface']
    pred.write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n',encoding='utf-8')
    with pytest.raises(ValueError):experiment.evaluate(oracle,pred,tmp_path/'duplicates.json')
