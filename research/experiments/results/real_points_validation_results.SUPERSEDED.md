# 旧7点验证结果的使用限制

2026-11-12 核查发现独立脚本把 `ap > a_lim` 标成稳定。`real_points_validation_results.json` 保留历史，不再作为当前验证结果；相关的旧分数和“重现文献负面结论”表述撤回。

共享规则已统一为 `ap < a_lim`（等号保守记非稳定），分类使用未舍入的边界。重新运行结果见 `real_points_validation_corrected_20261112.json`。C模型重新训练，因此新旧输出不能当作仅改变判定方向的受控消融。

修复说明：`docs/reports/research_reliability_fixes_20261112.md`（相对仓库根）。
