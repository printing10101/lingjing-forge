# 八轮约束对话挑战集：独立审核 pending

所有数值高度均以初始交付模型为参照；明确说“再提高”时对当前目标累加。未撤销的要求持续有效；只本轮的临时许可在下一条用户消息到来前自动失效，其他持久修改不回滚。“撤销”只撤销指定操作，不能删掉其他操作；提到轮号时从完整历史查找。被引用的建议或假设不执行，除非用户明确要求执行。信息性问题不修改状态。含糊指代需要澄清，保持当前有效状态。禁止加密时预算必须为0；恢复加密按用户引用的历史预算。外边界、孔口、光滑物理C2接缝与锐边保留规则不能取消；不得为解决几何冲突而自行放松要求。

## temporary_undo（c00）

### 第1轮 · initialize
目标中心提高1毫米，接口锁定，检测截面固定。允许其他片联动，但禁止加密，预算0；优先少加控制点。

有效状态：`{"amplitude": 1.0, "allow_refine": false, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 0}`
事务：`{"updates": {"amplitude": 1.0, "lock_interface": true, "preserve_datum": true, "allow_expand": true, "priority": "complexity"}, "needs_clarification": false}`
人工正确状态几何结果：no_candidate_in_authorized_space
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第2轮 · temporary_permission
接口仅在这一轮允许调整，下一轮自动重新锁定；高度改成2毫米。其他不变。

有效状态：`{"amplitude": 2.0, "allow_refine": false, "allow_expand": true, "lock_interface": false, "preserve_datum": true, "priority": "complexity", "max_added_poles": 0}`
事务：`{"updates": {"lock_interface": false, "amplitude": 2.0}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第3轮 · temporary_expiry
中心高度改为1.5毫米，其他要求沿用。

有效状态：`{"amplitude": 1.5, "allow_refine": false, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 0}`
事务：`{"updates": {"lock_interface": true, "amplitude": 1.5}, "needs_clarification": false}`
人工正确状态几何结果：no_candidate_in_authorized_space
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第4轮 · grant
重新允许加密，新增控制点最多105个。

有效状态：`{"amplitude": 1.5, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第5轮 · information_only
仅解释一下检测截面的位置，不要修改任何要求。

有效状态：`{"amplitude": 1.5, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第6轮 · selective_undo
撤销第4轮的加密许可，其他维持当前状态。

有效状态：`{"amplitude": 1.5, "allow_refine": false, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 0}`
事务：`{"updates": {"allow_refine": false, "max_added_poles": 0}, "needs_clarification": false}`
人工正确状态几何结果：no_candidate_in_authorized_space
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第7轮 · redo
恢复第4轮的加密许可与当时预算，接口仍锁定。

有效状态：`{"amplitude": 1.5, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第8轮 · ambiguous_authorization
那就把这些限制适当放宽一点。

有效状态：`{"amplitude": 1.5, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {}, "needs_clarification": true}`
人工正确状态几何结果：needs_clarification
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

## historical_reference（c04）

### 第1轮 · initialize
中心提高1毫米，接口锁定，检测截面不用固定；允许加密105点，也允许邻面随动，优先少改面。

有效状态：`{"amplitude": 1.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": false, "priority": "locality", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_refine": true, "max_added_poles": 105, "allow_expand": true}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第2轮 · add_constraint
检测截面改为固定，优先级改为先少增加控制点。

有效状态：`{"amplitude": 1.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"preserve_datum": true, "priority": "complexity"}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第3轮 · replace_target
中心改成提高2毫米，其他照旧。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 2.0}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第4轮 · revoke
禁止邻面随动，其他照旧。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": false, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"allow_expand": false}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第5轮 · selective_undo
撤销上一轮对邻面随动的禁令，其他要求别动。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"allow_expand": true}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第6轮 · partial_restore
接口和检测截面的要求恢复到第1轮，其他字段保留现在的值。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": false, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"preserve_datum": false}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第7轮 · relative_target
在现在的目标高度上再提高0.5毫米。

有效状态：`{"amplitude": 2.5, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": false, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 2.5}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第8轮 · hypothetical
仅讨论：如果重新固定检测截面会怎样？不要执行这个假设，也不改其他要求。

有效状态：`{"amplitude": 2.5, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": false, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

## conflict_preservation（c03）

### 第1轮 · initialize
中心提高1毫米，接口锁定，其他片可联动；允许加密105点，优先少增加控制点，检测截面暂不固定。

有效状态：`{"amplitude": 1.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": false, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_expand": true, "allow_refine": true, "max_added_poles": 105, "priority": "complexity"}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第2轮 · add_conflicting_constraint
加入检测截面保持交付轮廓的要求，其他要求不撤销。

有效状态：`{"amplitude": 1.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"preserve_datum": true}, "needs_clarification": false}`
人工正确状态几何结果：direct_constraint_conflict
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第3轮 · preserve_conflict
高度改成2毫米，检测截面仍固定，接口也仍锁定。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 2.0}, "needs_clarification": false}`
人工正确状态几何结果：direct_constraint_conflict
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第4轮 · information_only
先别删任何要求，只解释为什么现在可能无法同时满足。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {}, "needs_clarification": false}`
人工正确状态几何结果：direct_constraint_conflict
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第5轮 · ambiguous_referent
把那个限制放开吧。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {}, "needs_clarification": true}`
人工正确状态几何结果：needs_clarification
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第6轮 · clarification_resolution
我指的是检测截面，撤销它的固定要求；接口锁定和其他要求都不动。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": false, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"preserve_datum": false}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第7轮 · restore_conflict
恢复上一轮撤销的那项要求，高度别变。

有效状态：`{"amplitude": 2.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"preserve_datum": true}, "needs_clarification": false}`
人工正确状态几何结果：direct_constraint_conflict
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第8轮 · zero_target
中心目标改回初始交付位置，其他要求全部保留。

有效状态：`{"amplitude": 0.0, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "complexity", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 0.0}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

## quoted_advice（c07）

### 第1轮 · initialize
中心提高0.8毫米，接口和检测截面锁定；允许加密105点，但不允许邻面随动，优先少改面。

有效状态：`{"amplitude": 0.8, "allow_refine": true, "allow_expand": false, "lock_interface": true, "preserve_datum": true, "priority": "locality", "max_added_poles": 105}`
事务：`{"updates": {"amplitude": 0.8, "lock_interface": true, "preserve_datum": true, "allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第2轮 · mixed_temporary_persistent
仅这一轮允许邻面随动，下轮自动恢复禁令；新增控制点预算改为60并在以后沿用。

有效状态：`{"amplitude": 0.8, "allow_refine": true, "allow_expand": true, "lock_interface": true, "preserve_datum": true, "priority": "locality", "max_added_poles": 60}`
事务：`{"updates": {"allow_expand": true, "max_added_poles": 60}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第3轮 · temporary_expiry
中心高度改为1.2毫米，其余要求沿用。

有效状态：`{"amplitude": 1.2, "allow_refine": true, "allow_expand": false, "lock_interface": true, "preserve_datum": true, "priority": "locality", "max_added_poles": 60}`
事务：`{"updates": {"amplitude": 1.2, "allow_expand": false}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第4轮 · quoted_advice
同事建议“接口和检测截面都解除锁定”。这只是转述，暂时不要执行，也不要改其他设置。

有效状态：`{"amplitude": 1.2, "allow_refine": true, "allow_expand": false, "lock_interface": true, "preserve_datum": true, "priority": "locality", "max_added_poles": 60}`
事务：`{"updates": {}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第5轮 · authorize_quoted_advice
现在执行上一轮同事的那条建议，除此以外保持当前设置。

有效状态：`{"amplitude": 1.2, "allow_refine": true, "allow_expand": false, "lock_interface": false, "preserve_datum": false, "priority": "locality", "max_added_poles": 60}`
事务：`{"updates": {"lock_interface": false, "preserve_datum": false}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第6轮 · historical_budget
控制点预算恢复到第1轮的数值，其余沿用。

有效状态：`{"amplitude": 1.2, "allow_refine": true, "allow_expand": false, "lock_interface": false, "preserve_datum": false, "priority": "locality", "max_added_poles": 105}`
事务：`{"updates": {"max_added_poles": 105}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第7轮 · revoke
把接口锁回去，同时禁止加密。其余不变。

有效状态：`{"amplitude": 1.2, "allow_refine": false, "allow_expand": false, "lock_interface": true, "preserve_datum": false, "priority": "locality", "max_added_poles": 0}`
事务：`{"updates": {"lock_interface": true, "allow_refine": false, "max_added_poles": 0}, "needs_clarification": false}`
人工正确状态几何结果：no_candidate_in_authorized_space
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____

### 第8轮 · selective_undo
只撤销上一轮禁止加密这项，预算恢复成禁止前的数值；不要撤销接口锁定。

有效状态：`{"amplitude": 1.2, "allow_refine": true, "allow_expand": false, "lock_interface": true, "preserve_datum": false, "priority": "locality", "max_added_poles": 105}`
事务：`{"updates": {"allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
人工正确状态几何结果：feasible
审核人：_____；语义是否唯一：□；临时例外/引用是否合理：□；修正意见：_____
