# Gradišek2005数字化表：来源核查状态

2026-11-11，审阅版本2。原516行CSV未改，SHA-256：`cc830a9d65eb4be10196f76149c29ec9585910912200537be13e7cbb3ebf3863`。

原表不能直接作为已核验实验真值。固定种子、四个浸入比各抽8行，共32行；第二版暂保留25行，7行是边界节点或不可靠重叠。明确错标签仅保留原零基行407（CSV第409行）：原表stable，源图叉号chatter。数字坐标未独立重新标定。

助手第一版曾声称有4处明确错误，复核发现助手本身把圆圈和重叠误认成叉号，已撤回：行81是圆圈，行4、97退回不确定。第一版27点导出与派生结果均附撤回说明，不能继续作为当前结论。

当前审阅：`research/prototypes/thinwall_inverse_design/evidence/round3/blind_audit_review.json` revision2。
当前25点导出：`research/prototypes/thinwall_inverse_design/artifacts/reviewed_reference_20261111_02/`。
详情：`docs/reports/thinwall_reference_audit_20261111.md`。

审阅由同一助手完成，部分图此前见过，不是独立盲评。25点仅作暂定的来源一致性检查。旧68.41%仅是与旧表一致率；当前21/25也不能外推到全部516行或真实加工性能。
