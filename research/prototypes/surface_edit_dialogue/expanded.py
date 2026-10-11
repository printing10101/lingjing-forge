"""Four synthetic eight-turn histories; review pending, no real-user claims."""
import json
from pathlib import Path
import hashlib
from dialogue import INITIAL, apply_transaction, public_dataset, geometry_check
from model import make_atlas
from tasks import public_geometry

CONTEXT = ('所有数值高度均以初始交付模型为参照；明确说“再提高”时对当前目标累加。'
           '未撤销的要求持续有效；只本轮的临时许可在下一条用户消息到来前自动失效，其他持久修改不回滚。'
           '“撤销”只撤销指定操作，不能删掉其他操作；提到轮号时从完整历史查找。'
           '被引用的建议或假设不执行，除非用户明确要求执行。信息性问题不修改状态。'
           '含糊指代需要澄清，保持当前有效状态。禁止加密时预算必须为0；恢复加密按用户引用的历史预算。'
           '外边界、孔口、光滑物理C2接缝与锐边保留规则不能取消；不得为解决几何冲突而自行放松要求。')


def specifications():
    """Texts and independent oracle edits are exported into separate artifacts."""
    return [
      ('temporary_undo',0,[
        ('目标中心提高1毫米，接口锁定，检测截面固定。允许其他片联动，但禁止加密，预算0；优先少加控制点。',dict(amplitude=1.,lock_interface=True,preserve_datum=True,allow_expand=True,priority='complexity'),False,'initialize'),
        ('接口仅在这一轮允许调整，下一轮自动重新锁定；高度改成2毫米。其他不变。',dict(lock_interface=False,amplitude=2.),False,'temporary_permission'),
        ('中心高度改为1.5毫米，其他要求沿用。',dict(lock_interface=True,amplitude=1.5),False,'temporary_expiry'),
        ('重新允许加密，新增控制点最多105个。',dict(allow_refine=True,max_added_poles=105),False,'grant'),
        ('仅解释一下检测截面的位置，不要修改任何要求。',{},False,'information_only'),
        ('撤销第4轮的加密许可，其他维持当前状态。',dict(allow_refine=False,max_added_poles=0),False,'selective_undo'),
        ('恢复第4轮的加密许可与当时预算，接口仍锁定。',dict(allow_refine=True,max_added_poles=105),False,'redo'),
        ('那就把这些限制适当放宽一点。',{},True,'ambiguous_authorization')]),
      ('historical_reference',4,[
        ('中心提高1毫米，接口锁定，检测截面不用固定；允许加密105点，也允许邻面随动，优先少改面。',dict(amplitude=1.,lock_interface=True,allow_refine=True,max_added_poles=105,allow_expand=True),False,'initialize'),
        ('检测截面改为固定，优先级改为先少增加控制点。',dict(preserve_datum=True,priority='complexity'),False,'add_constraint'),
        ('中心改成提高2毫米，其他照旧。',dict(amplitude=2.),False,'replace_target'),
        ('禁止邻面随动，其他照旧。',dict(allow_expand=False),False,'revoke'),
        ('撤销上一轮对邻面随动的禁令，其他要求别动。',dict(allow_expand=True),False,'selective_undo'),
        ('接口和检测截面的要求恢复到第1轮，其他字段保留现在的值。',dict(preserve_datum=False),False,'partial_restore'),
        ('在现在的目标高度上再提高0.5毫米。',dict(amplitude=2.5),False,'relative_target'),
        ('仅讨论：如果重新固定检测截面会怎样？不要执行这个假设，也不改其他要求。',{},False,'hypothetical')]),
      ('conflict_preservation',3,[
        ('中心提高1毫米，接口锁定，其他片可联动；允许加密105点，优先少增加控制点，检测截面暂不固定。',dict(amplitude=1.,lock_interface=True,allow_expand=True,allow_refine=True,max_added_poles=105,priority='complexity'),False,'initialize'),
        ('加入检测截面保持交付轮廓的要求，其他要求不撤销。',dict(preserve_datum=True),False,'add_conflicting_constraint'),
        ('高度改成2毫米，检测截面仍固定，接口也仍锁定。',dict(amplitude=2.),False,'preserve_conflict'),
        ('先别删任何要求，只解释为什么现在可能无法同时满足。',{},False,'information_only'),
        ('把那个限制放开吧。',{},True,'ambiguous_referent'),
        ('我指的是检测截面，撤销它的固定要求；接口锁定和其他要求都不动。',dict(preserve_datum=False),False,'clarification_resolution'),
        ('恢复上一轮撤销的那项要求，高度别变。',dict(preserve_datum=True),False,'restore_conflict'),
        ('中心目标改回初始交付位置，其他要求全部保留。',dict(amplitude=0.),False,'zero_target')]),
      ('quoted_advice',7,[
        ('中心提高0.8毫米，接口和检测截面锁定；允许加密105点，但不允许邻面随动，优先少改面。',dict(amplitude=.8,lock_interface=True,preserve_datum=True,allow_refine=True,max_added_poles=105),False,'initialize'),
        ('仅这一轮允许邻面随动，下轮自动恢复禁令；新增控制点预算改为60并在以后沿用。',dict(allow_expand=True,max_added_poles=60),False,'mixed_temporary_persistent'),
        ('中心高度改为1.2毫米，其余要求沿用。',dict(amplitude=1.2,allow_expand=False),False,'temporary_expiry'),
        ('同事建议“接口和检测截面都解除锁定”。这只是转述，暂时不要执行，也不要改其他设置。',{},False,'quoted_advice'),
        ('现在执行上一轮同事的那条建议，除此以外保持当前设置。',dict(lock_interface=False,preserve_datum=False),False,'authorize_quoted_advice'),
        ('控制点预算恢复到第1轮的数值，其余沿用。',dict(max_added_poles=105),False,'historical_budget'),
        ('把接口锁回去，同时禁止加密。其余不变。',dict(lock_interface=True,allow_refine=False,max_added_poles=0),False,'revoke'),
        ('只撤销上一轮禁止加密这项，预算恢复成禁止前的数值；不要撤销接口锁定。',dict(allow_refine=True,max_added_poles=105),False,'selective_undo')])]


def dataset():
    dialogues=[]
    for name,index,turns in specifications():
        atlas=make_atlas(index)
        state=dict(INITIAL);rows=[]
        for n,(text,updates,pending,category) in enumerate(turns,1):
            previous=state
            tx=dict(updates=updates,needs_clarification=pending)
            state=apply_transaction(state,tx)
            rows.append(dict(turn=n,input_text=text,oracle_transaction=tx,oracle_state=state,
                             category=category,changed_fields=[k for k in state if previous[k]!=state[k]],
                             retained_fields=[k for k in state if previous[k]==state[k]]))
        dialogues.append(dict(dialogue_id=name,case_index=index,geometry=public_geometry(atlas),
                              context=CONTEXT,turns=rows,author='implementing_assistant',independent_human_review='pending'))
    return dialogues


def freeze(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    dialogues=dataset();results=[]
    review=['# 八轮约束对话挑战集：独立审核 pending','',CONTEXT,'']
    for name,data in [('oracle.json',dialogues),('public.json',public_dataset(dialogues))]:
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for d in dialogues:
        review += [f'## {d["dialogue_id"]}（{d["geometry"]["case_id"]}）','']
        for t in d['turns']:
            result=(dict(status='needs_clarification',calls=0) if t['oracle_transaction']['needs_clarification'] else
                    geometry_check(d['case_index'],t['oracle_state'],output/f'{d["dialogue_id"]}_r{t["turn"]}.step'))
            results.append(dict(dialogue_id=d['dialogue_id'],turn=t['turn'],**result))
            review += [f'### 第{t["turn"]}轮 · {t["category"]}',t['input_text'],'',
                       f'有效状态：`{json.dumps(t["oracle_state"],ensure_ascii=False)}`',
                       f'事务：`{json.dumps(t["oracle_transaction"],ensure_ascii=False)}`',
                       f'人工正确状态几何结果：{result["status"]}',
                       '审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____','']
            print(d['dialogue_id'],t['turn'],result['status'],flush=True)
    (output/'geometry_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'review.md').write_text('\n'.join(review),encoding='utf-8')
    summary=dict(dialogues=len(dialogues),turns=len(results),independent_review='pending',llm_evaluated=False,
                 source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 statuses={s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})})
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary
