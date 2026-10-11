# 多轮曲面约束维护：审核草案

助手编写，独立人工审核 pending。高度始终相对初始交付模型。每轮重新求解原始模型；并非在上一轮控制网上累计编辑。

## c00

### 第1轮
目标中心相对交付模型提高1毫米。接口整片不动，其他过渡片允许联动；允许加密，优先不增加控制点。

更新：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_expand": true, "allow_refine": true, "priority": "complexity", "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第2轮
把刚才的中心高度改成相对交付模型提高1.5毫米，其他要求继续执行。

更新：`{"updates": {"amplitude": 1.5}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第3轮
现在控制网冻结，不许加控制点；检测截面也必须保留交付轮廓。仍允许过渡片联动，其他要求不变。

更新：`{"updates": {"allow_refine": false, "max_added_poles": 0, "preserve_datum": true}, "needs_clarification": false}`
几何判定：no_candidate_in_authorized_space
审核人：______；语义标注：□；保留/撤销正确：□

### 第4轮
检测截面继续固定，但解除接口整片锁定，重新允许加密，预算恢复105；高度和其他规则不变。

更新：`{"updates": {"lock_interface": false, "allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

## c01

### 第1轮
目标中心相对交付模型提高1毫米。接口整片不动，其他过渡片允许联动；允许加密，优先不增加控制点。

更新：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_expand": true, "allow_refine": true, "priority": "complexity", "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第2轮
把刚才的中心高度改成相对交付模型提高1.5毫米，其他要求继续执行。

更新：`{"updates": {"amplitude": 1.5}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第3轮
现在控制网冻结，不许加控制点；检测截面也必须保留交付轮廓。仍允许过渡片联动，其他要求不变。

更新：`{"updates": {"allow_refine": false, "max_added_poles": 0, "preserve_datum": true}, "needs_clarification": false}`
几何判定：no_candidate_in_authorized_space
审核人：______；语义标注：□；保留/撤销正确：□

### 第4轮
检测截面继续固定，但解除接口整片锁定，重新允许加密，预算恢复105；高度和其他规则不变。

更新：`{"updates": {"lock_interface": false, "allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

## c03

### 第1轮
目标中心相对交付模型提高1毫米。接口整片不动，其他过渡片允许联动；允许加密，优先不增加控制点。

更新：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_expand": true, "allow_refine": true, "priority": "complexity", "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第2轮
把刚才的中心高度改成相对交付模型提高1.5毫米，其他要求继续执行。

更新：`{"updates": {"amplitude": 1.5}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第3轮
现在控制网冻结，不许加控制点；检测截面也必须保留交付轮廓。仍允许过渡片联动，其他要求不变。

更新：`{"updates": {"allow_refine": false, "max_added_poles": 0, "preserve_datum": true}, "needs_clarification": false}`
几何判定：direct_constraint_conflict
审核人：______；语义标注：□；保留/撤销正确：□

### 第4轮
撤销检测截面保持原样的要求，重新允许加密，预算恢复105；接口仍锁定，高度仍为1.5毫米。

更新：`{"updates": {"preserve_datum": false, "allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

## c04

### 第1轮
目标中心相对交付模型提高1毫米。接口整片不动，其他过渡片允许联动；允许加密，优先不增加控制点。

更新：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_expand": true, "allow_refine": true, "priority": "complexity", "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第2轮
把刚才的中心高度改成相对交付模型提高1.5毫米，其他要求继续执行。

更新：`{"updates": {"amplitude": 1.5}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第3轮
现在控制网冻结，不许加控制点；检测截面也必须保留交付轮廓。仍允许过渡片联动，其他要求不变。

更新：`{"updates": {"allow_refine": false, "max_added_poles": 0, "preserve_datum": true}, "needs_clarification": false}`
几何判定：no_candidate_in_authorized_space
审核人：______；语义标注：□；保留/撤销正确：□

### 第4轮
检测截面继续固定，但解除接口整片锁定，重新允许加密，预算恢复105；高度和其他规则不变。

更新：`{"updates": {"lock_interface": false, "allow_refine": true, "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

## c07

### 第1轮
目标中心相对交付模型提高1毫米。接口整片不动，其他过渡片允许联动；允许加密，优先不增加控制点。

更新：`{"updates": {"amplitude": 1.0, "lock_interface": true, "allow_expand": true, "allow_refine": true, "priority": "complexity", "max_added_poles": 105}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第2轮
把刚才的中心高度改成相对交付模型提高1.5毫米，其他要求继续执行。

更新：`{"updates": {"amplitude": 1.5}, "needs_clarification": false}`
几何判定：feasible
审核人：______；语义标注：□；保留/撤销正确：□

### 第3轮
现在控制网冻结，不许加控制点；检测截面也必须保留交付轮廓。仍允许过渡片联动，其他要求不变。

更新：`{"updates": {"allow_refine": false, "max_added_poles": 0, "preserve_datum": true}, "needs_clarification": false}`
几何判定：no_candidate_in_authorized_space
审核人：______；语义标注：□；保留/撤销正确：□

### 第4轮
那就把刚才的限制适当放宽一点。

更新：`{"updates": {}, "needs_clarification": true}`
几何判定：needs_clarification
审核人：______；语义标注：□；保留/撤销正确：□
