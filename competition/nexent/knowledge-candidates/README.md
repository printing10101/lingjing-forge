# 厂商来源候选

本目录是待审核资料，不能作为已审核的工艺推荐或上机参数。

- `dormer-source-manifest.json`：官方 PDF 地址、SHA256、选取记录和页码。
- `dormer-drill-candidates.json`：三条规范化候选，始终 unreviewed，不进入正式推荐。
- `dormer-reference-note.md`：来源说明，已放入独立 PRIVATE 待审核知识库，未绑定业务智能体。

原文第 4 页明确进给为 inch/rev，第 5 页给出工具与材料组代码。
选取 R10A、5/16 英寸直径列的三个材料组，仅转换已确认的每转进给单位。
速度单位未确认，不计算转速；材料组未自动映射成国标 45# 钢。
完整 PDF 保存在本机私有来源目录，不随仓库重新分发，不声明其具有开源许可证。

使用系统 Python 3.14 执行 `engineering/python/scripts/build_vendor_candidates.py`，
传入 `--pdf`、`--manifest` 和 `--output` 可重复生成候选；原文哈希变化时会拒绝沿用旧提取记录。
正式应用仍需确认材料牌号、硬度、刀具、孔深、冷却和装夹条件，并补全速度单位及工艺审核。
此目录没有实现自动审核批准。

## I16 补充核对（2026-10-17）

新增 `dormer-review-supplement.json`（逐页补充事实）和 `dormer-review-packet.json`（可重建的人工审核准备包）。
已补充材料分组说明、最近直径选表规则、4×D 标识、NAS907 Type A / HSS / 118°，
以及 5/16 inch 的 R10A 目录号 010120。速度数字的单位仍未确认。
原始三条候选的标识和参数保持不变；资料包不自动写入线上记录，也不授予推荐资格。

用 `engineering/python/scripts/build_vendor_review_packet.py`，传入 `--pdf`、`--manifest`、
`--supplement`、`--output` 可重新生成。补充资料必须绑定同一原文哈希、逐一对应材料组、提供合法页码；
原候选的全部未解决事项始终保留。资料真实性仍需人工对照原文。

真实审核与推荐的逐项操作见 [真实知识闭环验收清单](真实知识闭环验收清单.md)。
