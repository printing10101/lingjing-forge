import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.plugins.skill_loader import SkillLoader  # noqa: E402

GLOBAL_SKILL_CONTENT = """---
skill_id: global_safety_check
name: 全局安全检查
display_name: 全局安全检查协议
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["safety", "global", "compliance"]
description: 适用于所有项目和代理的通用安全检查技能，确保操作合规
---

# 全局安全检查协议

## 适用场景
所有加工操作前必须执行的安全检查。

## 输入参数
- operation_type: 操作类型
- machine_status: 机床状态

## 执行步骤
1. 检查机床紧急停止按钮状态
2. 验证防护门是否关闭
3. 确认冷却液液位正常
4. 检查刀具磨损状态
"""

PROJECT_SKILL_CONTENT = """---
skill_id: project_custom_validation
name: 项目自定义校验
display_name: 项目专属参数校验
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["project", "validation"]
description: 仅在特定项目中生效的自定义参数校验逻辑
---

# 项目专属参数校验

## 适用场景
特定项目的专用参数验证。

## 输入参数
- project_specific_param: 项目特定参数

## 执行步骤
1. 校验项目配置完整性
2. 验证项目环境变量
"""

AGENT_SKILL_CONTENT = """---
skill_id: agent_expert_rules
name: 代理专家规则
display_name: 代理专属决策规则
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["agent", "expert"]
description: 特定代理的自定义决策规则集
---

# 代理专属决策规则

## 适用场景
特定代理执行任务时的专家经验。

## 输入参数
- confidence_threshold: 置信度阈值

## 执行步骤
1. 应用代理专属推理规则
2. 根据历史记录调整决策权重
"""

TRAINING_ONLY_SKILL = """---
skill_id: training_specialist
name: 训练专用技能
display_name: LNN训练专家指导
version: 1.0.0
applicable_tasks: ["training"]
required_context: []
tags: ["training", "lnn"]
description: 仅在训练任务中可用的专用技能，提供LNN模型训练指导
---

# LNN训练专家指导

## 适用场景
LNN模型训练任务的专项指导。

## 输入参数
- learning_rate: 学习率
- epochs: 训练轮数

## 执行步骤
1. 验证训练数据完整性和格式
2. 配置训练超参数建议范围
3. 监控训练过程中的异常检测
"""

TERM_SKILL_A = """---
skill_id: term_definition_a
name: 术语定义A
display_name: 术语定义版本A
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["terminology"]
description: 术语定义集 - 版本A
parameters:
  max_speed: 5000
  precision_level: "high"
  material_grade: "A2"
---

# 术语定义集 - 版本A

## 关键术语
- **最大转速 (max_speed)**: 5000 RPM（保守估计）
- **精度等级 (precision_level)**: high（高精度模式）
- **材料等级 (material_grade)**: A2（标准工业级）
- **冷却方式 (cooling_method)**: 干式切削
"""

TERM_SKILL_B = """---
skill_id: term_definition_b
name: 术语定义B
display_name: 术语定义版本B
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["terminology"]
description: 术语定义集 - 版本B
parameters:
  max_speed: 8000
  precision_level: "medium"
  material_grade: "B1"
---

# 术语定义集 - 版本B

## 关键术语
- **最大转速 (max_speed)**: 8000 RPM（激进策略）
- **精度等级 (precision_level)**: medium（中等精度）
- **材料等级 (material_grade)**: B1（高级工业级）
- **冷却方式 (cooling_method)**: 湿式切削
"""

TERM_SKILL_C = """---
skill_id: term_definition_c
name: 术语定义C
display_name: 术语定义版本C
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["terminology"]
description: 术语定义集 - 版本C
parameters:
  max_speed: 6000
  precision_level: "standard"
  material_grade: "C3"
---

# 术语定义集 - 版本C

## 关键术语
- **最大转速 (max_speed)**: 6000 RPM（均衡策略）
- **精度等级 (precision_level)**: standard（标准精度）
- **材料等级 (material_grade)**: C3（通用工业级）
- **冷却方式 (cooling_method)**: 混合切削
"""


class TestGlobalSkillAvailability:
    """测试1：全局技能可用性测试 — 部署全局技能后，所有项目和代理都能获取"""

    @pytest.fixture
    def global_skill_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()

        global_dir = skills_base / "global"
        global_dir.mkdir()
        (global_dir / "global_safety_check.md").write_text(
            GLOBAL_SKILL_CONTENT, encoding="utf-8"
        )

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        for pid in ["proj_alpha", "proj_beta", "proj_gamma"]:
            (projects_dir / pid).mkdir()

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        for aid in ["agent_01", "agent_02", "agent_03", "agent_04", "agent_05"]:
            (agents_dir / aid).mkdir()

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield (
            loader,
            skills_base,
            ["proj_alpha", "proj_beta", "proj_gamma"],
            ["agent_01", "agent_02", "agent_03", "agent_04", "agent_05"],
        )
        loader.stop_watcher()

    def test_global_skill_accessible_to_all_projects(self, global_skill_setup):
        loader, _, projects, agents = global_skill_setup

        for pid in projects:
            for aid in agents:
                skills = loader.get_skills_for_task(
                    task_type="prediction", project_id=pid, agent_id=aid
                )
                global_ids = [s.metadata.skill_id for s in skills]
                assert "global_safety_check" in global_ids, (
                    f"全局技能未被 project={pid}, agent={aid} 获取到"
                )

    def test_global_skill_accessible_across_diverse_tasks(self, global_skill_setup):
        loader, _, projects, agents = global_skill_setup

        task_types = ["prediction", "training", "analysis", "optimization", "inference"]

        for task_type in task_types:
            for pid in projects:
                skills = loader.get_skills_for_task(
                    task_type=task_type, project_id=pid, agent_id=agents[0]
                )
                global_ids = [s.metadata.skill_id for s in skills]
                assert "global_safety_check" in global_ids, (
                    f"全局技能未被 task={task_type}, project={pid} 获取到"
                )

    def test_global_skill_available_without_project_agent(self, global_skill_setup):
        loader, _, _, _ = global_skill_setup
        skills = loader.get_skills_for_task(task_type="prediction")
        global_ids = [s.metadata.skill_id for s in skills]
        assert "global_safety_check" in global_ids

    def test_global_skill_appears_in_injected_context(self, global_skill_setup):
        loader, _, projects, agents = global_skill_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="analysis",
                project_id=projects[0],
                agent_id=agents[0],
            )
        )
        assert "全局安全检查" in ctx
        assert "全局安全检查协议" in ctx


class TestProjectSkillIsolation:
    """测试2：项目级技能隔离测试 — 项目内可访问，项目外不可访问"""

    @pytest.fixture
    def project_isolation_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()
        (skills_base / "global").mkdir()

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()

        for pid in ["proj_internal", "proj_external"]:
            proj_dir = projects_dir / pid
            proj_dir.mkdir()

        (projects_dir / "proj_internal" / "project_custom_validation.md").write_text(
            PROJECT_SKILL_CONTENT, encoding="utf-8"
        )

        (projects_dir / "proj_external").mkdir(exist_ok=True)

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_common").mkdir()

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield loader
        loader.stop_watcher()

    def test_project_skill_visible_inside_project(self, project_isolation_setup):
        loader = project_isolation_setup
        skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_internal"
        )
        skill_ids = [s.metadata.skill_id for s in skills]
        assert "project_custom_validation" in skill_ids

    def test_project_skill_invisible_outside_project(self, project_isolation_setup):
        loader = project_isolation_setup
        skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_external"
        )
        skill_ids = [s.metadata.skill_id for s in skills]
        assert "project_custom_validation" not in skill_ids

    def test_project_skill_invisible_without_project_id(self, project_isolation_setup):
        loader = project_isolation_setup
        skills = loader.get_skills_for_task(task_type="prediction")
        skill_ids = [s.metadata.skill_id for s in skills]
        assert "project_custom_validation" not in skill_ids

    def test_multiple_tasks_in_project_see_skill(self, project_isolation_setup):
        loader = project_isolation_setup
        for task_type in ["prediction", "training", "analysis"]:
            skills = loader.get_skills_for_task(
                task_type=task_type, project_id="proj_internal"
            )
            skill_ids = [s.metadata.skill_id for s in skills]
            assert "project_custom_validation" in skill_ids, (
                f"项目技能未被 task={task_type} 获取到"
            )


class TestAgentSkillExclusivity:
    """测试3：代理级技能专属测试 — 仅目标代理可获取"""

    @pytest.fixture
    def agent_exclusivity_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()
        (skills_base / "global").mkdir()

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        (projects_dir / "proj_common").mkdir()

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        for aid in ["agent_target", "agent_other_1", "agent_other_2"]:
            (agents_dir / aid).mkdir()

        (agents_dir / "agent_target" / "agent_expert_rules.md").write_text(
            AGENT_SKILL_CONTENT, encoding="utf-8"
        )

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield loader
        loader.stop_watcher()

    def test_target_agent_can_access_its_skill(self, agent_exclusivity_setup):
        loader = agent_exclusivity_setup
        skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_common", agent_id="agent_target"
        )
        skill_ids = [s.metadata.skill_id for s in skills]
        assert "agent_expert_rules" in skill_ids

    def test_other_agent_cannot_access_target_skill(self, agent_exclusivity_setup):
        loader = agent_exclusivity_setup
        for other_agent in ["agent_other_1", "agent_other_2"]:
            skills = loader.get_skills_for_task(
                task_type="prediction", project_id="proj_common", agent_id=other_agent
            )
            skill_ids = [s.metadata.skill_id for s in skills]
            assert "agent_expert_rules" not in skill_ids, (
                f"代理 {other_agent} 不应获取到 agent_target 的技能"
            )

    def test_same_task_type_different_agents(self, agent_exclusivity_setup):
        loader = agent_exclusivity_setup

        target_skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_common", agent_id="agent_target"
        )
        assert "agent_expert_rules" in [s.metadata.skill_id for s in target_skills]

        other_skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_common", agent_id="agent_other_1"
        )
        assert "agent_expert_rules" not in [s.metadata.skill_id for s in other_skills]


class TestSkillHotReload:
    """测试4：技能热更新功能测试 — 修改文件后自动检测并加载更新"""

    @pytest.fixture
    def hot_reload_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()
        (skills_base / "global").mkdir()
        (skills_base / "projects").mkdir()
        (skills_base / "agents").mkdir()

        loader = SkillLoader(skills_base_dir=str(skills_base))

        skill_content_v1 = """---
skill_id: hot_reload_test
name: 热更新测试技能
display_name: 热更新测试技能V1
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
description: 版本1 - 原始内容
---

# 版本1

## 执行逻辑
result = input_value * 2
"""
        global_dir = skills_base / "global"
        (global_dir / "hot_reload_test.md").write_text(
            skill_content_v1, encoding="utf-8"
        )

        loader.hot_reload()

        yield loader, skills_base, global_dir
        loader.stop_watcher()

    def test_hot_reload_detects_content_change(self, hot_reload_setup):
        loader, skills_base, global_dir = hot_reload_setup

        skills_before = loader.get_skills_for_task(task_type="prediction")
        {
            s.metadata.skill_id: s.metadata.version
            for s in skills_before
            if s.metadata.skill_id == "hot_reload_test"
        }

        skill_content_v2 = """---
skill_id: hot_reload_test
name: 热更新测试技能
display_name: 热更新测试技能V2
version: 2.0.0
applicable_tasks: ["*"]
required_context: []
description: 版本2 - 更新后的内容
---

# 版本2

## 执行逻辑
result = input_value * 3 + offset
"""
        (global_dir / "hot_reload_test.md").write_text(
            skill_content_v2, encoding="utf-8"
        )

        result = loader.hot_reload(skill_id="hot_reload_test")

        assert result["status"] == "reloaded"
        assert result["skill_id"] == "hot_reload_test"

        skill = loader.registry.get("hot_reload_test")
        assert skill is not None
        assert skill.metadata.version == "2.0.0"
        assert skill.metadata.display_name == "热更新测试技能V2"
        assert "result = input_value * 3 + offset" in skill.body

    def test_full_hot_reload_rebuilds_all_skills(self, hot_reload_setup):
        loader, skills_base, _ = hot_reload_setup

        initial_count = len(loader.registry.list_all())

        result = loader.hot_reload()
        assert result["status"] == "full_reload"
        assert result["count"] >= initial_count

    def test_hot_reload_nonexistent_skill(self, hot_reload_setup):
        loader, _, _ = hot_reload_setup

        result = loader.hot_reload(skill_id="nonexistent_skill_xyz")
        assert result["status"] == "not_found"

    def test_new_skill_file_detected_by_watcher(self, hot_reload_setup):
        loader, skills_base, global_dir = hot_reload_setup

        new_skill_content = """---
skill_id: watcher_detected
name: 文件监听检测技能
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
---
# 文件监听检测技能
"""
        (global_dir / "watcher_detected.md").write_text(
            new_skill_content, encoding="utf-8"
        )

        loader.hot_reload()

        skill = loader.registry.get("watcher_detected")
        assert skill is not None
        assert skill.metadata.name == "文件监听检测技能"


class TestSkillContextLogVerification:
    """测试5：技能上下文日志验证 — 注入的技能正确显示在上下文和日志中"""

    @pytest.fixture
    def context_log_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()

        global_dir = skills_base / "global"
        global_dir.mkdir()
        (global_dir / "global_safety_check.md").write_text(
            GLOBAL_SKILL_CONTENT, encoding="utf-8"
        )

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        (projects_dir / "proj_alpha").mkdir()
        (projects_dir / "proj_alpha" / "project_custom_validation.md").write_text(
            PROJECT_SKILL_CONTENT, encoding="utf-8"
        )

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_01").mkdir()
        (agents_dir / "agent_01" / "agent_expert_rules.md").write_text(
            AGENT_SKILL_CONTENT, encoding="utf-8"
        )

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield loader
        loader.stop_watcher()

    def test_injected_context_contains_skill_name_and_scope(self, context_log_setup):
        loader = context_log_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction",
                project_id="proj_alpha",
                agent_id="agent_01",
            )
        )

        assert "已注入技能指南" in ctx
        assert "全局安全检查协议" in ctx
        assert "项目专属参数校验" in ctx
        assert "代理专属决策规则" in ctx

    def test_injected_context_shows_skill_level_labels(self, context_log_setup):
        loader = context_log_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction",
                project_id="proj_alpha",
                agent_id="agent_01",
            )
        )

        assert "全局" in ctx
        assert "项目" in ctx
        assert "代理" in ctx

    def test_injected_context_includes_skill_body(self, context_log_setup):
        loader = context_log_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction",
                project_id="proj_alpha",
                agent_id="agent_01",
            )
        )

        assert "检查机床紧急停止按钮状态" in ctx
        assert "验证防护门是否关闭" in ctx
        assert "校验项目配置完整性" in ctx

    def test_injected_context_without_project_agent(self, context_log_setup):
        loader = context_log_setup
        import asyncio

        ctx = asyncio.run(loader.inject_skills(task_type="prediction"))
        assert "已注入技能指南" in ctx
        assert "全局安全检查协议" in ctx
        assert "项目专属参数校验" not in ctx
        assert "代理专属决策规则" not in ctx


class TestTaskTypeFiltering:
    """测试6：技能任务类型筛选测试 — applicable_tasks精确过滤"""

    @pytest.fixture
    def task_filter_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()

        global_dir = skills_base / "global"
        global_dir.mkdir()

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        (projects_dir / "proj_test").mkdir()
        (projects_dir / "proj_test" / "training_specialist.md").write_text(
            TRAINING_ONLY_SKILL, encoding="utf-8"
        )

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_test").mkdir()

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield loader
        loader.stop_watcher()

    def test_training_skill_available_for_training_tasks(self, task_filter_setup):
        loader = task_filter_setup
        for _ in range(3):
            skills = loader.get_skills_for_task(
                task_type="training", project_id="proj_test", agent_id="agent_test"
            )
            skill_ids = [s.metadata.skill_id for s in skills]
            assert "training_specialist" in skill_ids

    def test_training_skill_unavailable_for_prediction_tasks(self, task_filter_setup):
        loader = task_filter_setup
        for _ in range(3):
            skills = loader.get_skills_for_task(
                task_type="prediction", project_id="proj_test", agent_id="agent_test"
            )
            skill_ids = [s.metadata.skill_id for s in skills]
            assert "training_specialist" not in skill_ids

    def test_training_skill_unavailable_for_other_task_types(self, task_filter_setup):
        loader = task_filter_setup
        for task_type in ["analysis", "optimization", "inference"]:
            skills = loader.get_skills_for_task(
                task_type=task_type, project_id="proj_test", agent_id="agent_test"
            )
            skill_ids = [s.metadata.skill_id for s in skills]
            assert "training_specialist" not in skill_ids, (
                f"training_specialist 不应被 task={task_type} 获取"
            )

    def test_training_skill_injected_only_for_training(self, task_filter_setup):
        loader = task_filter_setup
        import asyncio

        training_ctx = asyncio.run(
            loader.inject_skills(
                task_type="training", project_id="proj_test", agent_id="agent_test"
            )
        )
        assert "LNN训练专家指导" in training_ctx

        prediction_ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction", project_id="proj_test", agent_id="agent_test"
            )
        )
        assert "LNN训练专家指导" not in prediction_ctx


class TestSkillLoadFailureTolerance:
    """测试7：技能加载失败容错测试 — 损坏文件不应导致系统崩溃"""

    @pytest.fixture
    def failure_tolerance_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()

        global_dir = skills_base / "global"
        global_dir.mkdir()

        (global_dir / "healthy_skill.md").write_text(
            GLOBAL_SKILL_CONTENT, encoding="utf-8"
        )

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        (projects_dir / "proj_test").mkdir()

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_test").mkdir()

        yield skills_base
        import shutil

        if skills_base.exists():
            shutil.rmtree(str(skills_base), ignore_errors=True)

    def test_loader_handles_corrupt_yaml_frontmatter(self, failure_tolerance_setup):
        skills_base = failure_tolerance_setup
        global_dir = skills_base / "global"

        corrupt_content = """---
skill_id: corrupt_skill
name: 损坏的技能
version: 错误版本号 {
  broken: true
  syntax error here >>>>
applicable_tasks: ["*"]
---
# 损坏的技能
"""
        (global_dir / "corrupt_skill.md").write_text(corrupt_content, encoding="utf-8")

        loader = SkillLoader(skills_base_dir=str(skills_base))
        try:
            healthy_skills = loader.get_skills_for_task(
                task_type="prediction", project_id="proj_test", agent_id="agent_test"
            )
            healthy_ids = [s.metadata.skill_id for s in healthy_skills]
            assert "global_safety_check" in healthy_ids, "健康的全局技能应该仍然可用"

            import asyncio

            ctx = asyncio.run(
                loader.inject_skills(
                    task_type="prediction",
                    project_id="proj_test",
                    agent_id="agent_test",
                )
            )
            assert "全局安全检查" in ctx, "注入上下文应包含健康技能的内容"
        finally:
            loader.stop_watcher()

    def test_loader_handles_malformed_markdown(self, failure_tolerance_setup):
        skills_base = failure_tolerance_setup
        global_dir = skills_base / "global"

        (global_dir / "malformed.md").write_text(
            "\x00\x01\x02\xff\xfe", encoding="utf-8"
        )

        loader = SkillLoader(skills_base_dir=str(skills_base))
        try:
            skills = loader.get_skills_for_task(task_type="prediction")
            skill_ids = [s.metadata.skill_id for s in skills]
            assert "global_safety_check" in skill_ids
        finally:
            loader.stop_watcher()

    def test_loader_handles_empty_skill_file(self, failure_tolerance_setup):
        skills_base = failure_tolerance_setup
        global_dir = skills_base / "global"

        (global_dir / "empty_skill.md").write_text("", encoding="utf-8")

        loader = SkillLoader(skills_base_dir=str(skills_base))
        try:
            skills = loader.get_skills_for_task(task_type="prediction")
            assert len(skills) > 0
        finally:
            loader.stop_watcher()

    def test_loader_handles_runtime_corrupt_addition(self, failure_tolerance_setup):
        skills_base = failure_tolerance_setup

        loader = SkillLoader(skills_base_dir=str(skills_base))
        try:
            global_dir = skills_base / "global"
            (global_dir / "late_corrupt.md").write_text(
                "NOT EVEN VALID MARKDOWN {{{{{", encoding="utf-8"
            )
            loader.hot_reload()

            skills = loader.get_skills_for_task(task_type="prediction")
            healthy_ids = [s.metadata.skill_id for s in skills]
            assert "global_safety_check" in healthy_ids

            import asyncio

            ctx = asyncio.run(loader.inject_skills(task_type="prediction"))
            assert len(ctx) > 0
        finally:
            loader.stop_watcher()

    def test_task_execution_continues_with_corrupt_skill_present(
        self, failure_tolerance_setup
    ):
        skills_base = failure_tolerance_setup
        global_dir = skills_base / "global"

        (global_dir / "corrupt_binary.md").write_bytes(
            b"\x00\x01\x02\x03\xff\xfe\xfd\xfc"
        )

        loader = SkillLoader(skills_base_dir=str(skills_base))
        try:
            skills = loader.get_skills_for_task(task_type="training")
            assert len(skills) >= 0

            skill = loader.registry.get("global_safety_check")
            assert skill is not None
        finally:
            loader.stop_watcher()


class TestMultiSkillMergeConflicts:
    """测试8：多技能术语合并冲突测试 — 同术语不同定义的正确合并"""

    @pytest.fixture
    def merge_conflict_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()

        global_dir = skills_base / "global"
        global_dir.mkdir()
        (global_dir / "term_definition_a.md").write_text(TERM_SKILL_A, encoding="utf-8")
        (global_dir / "term_definition_b.md").write_text(TERM_SKILL_B, encoding="utf-8")
        (global_dir / "term_definition_c.md").write_text(TERM_SKILL_C, encoding="utf-8")

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        (projects_dir / "proj_merge").mkdir()

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent_merge").mkdir()

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield loader
        loader.stop_watcher()

    def test_three_terminology_skills_all_loaded(self, merge_conflict_setup):
        loader = merge_conflict_setup
        skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_merge", agent_id="agent_merge"
        )
        term_skills = [
            s for s in skills if s.metadata.skill_id.startswith("term_definition_")
        ]
        assert len(term_skills) == 3

    def test_merged_context_contains_all_skill_names(self, merge_conflict_setup):
        loader = merge_conflict_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction", project_id="proj_merge", agent_id="agent_merge"
            )
        )

        assert "术语定义版本A" in ctx
        assert "术语定义版本B" in ctx
        assert "术语定义版本C" in ctx

    def test_merged_context_has_no_duplicate_sections(self, merge_conflict_setup):
        loader = merge_conflict_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction", project_id="proj_merge", agent_id="agent_merge"
            )
        )

        assert ctx.count("## 已注入技能指南") == 1

    def test_priority_order_preserved_in_merge(self, merge_conflict_setup):
        loader = merge_conflict_setup
        skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_merge", agent_id="agent_merge"
        )
        term_skills = [
            s for s in skills if s.metadata.skill_id.startswith("term_definition_")
        ]
        assert (
            term_skills[0].metadata.priority.value
            <= term_skills[2].metadata.priority.value
        )

    def test_merge_handles_empty_context_gracefully(self, merge_conflict_setup):
        loader = merge_conflict_setup
        result = loader._merge_skills_to_context([])
        assert result == ""

    def test_all_term_definitions_present_in_merged_output(self, merge_conflict_setup):
        loader = merge_conflict_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction", project_id="proj_merge", agent_id="agent_merge"
            )
        )

        assert "最大转速" in ctx
        assert "精度等级" in ctx
        assert "材料等级" in ctx


class TestEndToEndIntegration:
    """端到端集成测试 — 多技能三层全场景"""

    @pytest.fixture
    def e2e_setup(self, tmp_path):
        skills_base = tmp_path / "skills"
        skills_base.mkdir()

        global_dir = skills_base / "global"
        global_dir.mkdir()
        (global_dir / "global_safety_check.md").write_text(
            GLOBAL_SKILL_CONTENT, encoding="utf-8"
        )

        projects_dir = skills_base / "projects"
        projects_dir.mkdir()
        for pid in ["proj_a", "proj_b", "proj_c"]:
            (projects_dir / pid).mkdir(exist_ok=True)
        (projects_dir / "proj_a" / "project_custom_validation.md").write_text(
            PROJECT_SKILL_CONTENT, encoding="utf-8"
        )

        agents_dir = skills_base / "agents"
        agents_dir.mkdir()
        for aid in ["agent_x", "agent_y", "agent_z"]:
            (agents_dir / aid).mkdir(exist_ok=True)
        (agents_dir / "agent_x" / "agent_expert_rules.md").write_text(
            AGENT_SKILL_CONTENT, encoding="utf-8"
        )

        loader = SkillLoader(skills_base_dir=str(skills_base))
        yield loader
        loader.stop_watcher()

    def test_full_three_tier_injection(self, e2e_setup):
        loader = e2e_setup
        import asyncio

        ctx = asyncio.run(
            loader.inject_skills(
                task_type="prediction", project_id="proj_a", agent_id="agent_x"
            )
        )

        assert "全局安全检查" in ctx
        assert "项目专属参数校验" in ctx
        assert "代理专属决策规则" in ctx

        assert "全局" in ctx
        assert "项目" in ctx
        assert "代理" in ctx

    def test_proj_a_without_agent_gets_global_and_project(self, e2e_setup):
        loader = e2e_setup
        skills = loader.get_skills_for_task(task_type="prediction", project_id="proj_a")
        skill_ids = [s.metadata.skill_id for s in skills]

        assert "global_safety_check" in skill_ids
        assert "project_custom_validation" in skill_ids
        assert "agent_expert_rules" not in skill_ids

    def test_agent_x_without_project_gets_global_and_agent(self, e2e_setup):
        loader = e2e_setup
        skills = loader.get_skills_for_task(task_type="prediction", agent_id="agent_x")
        skill_ids = [s.metadata.skill_id for s in skills]

        assert "global_safety_check" in skill_ids
        assert "project_custom_validation" not in skill_ids
        assert "agent_expert_rules" in skill_ids

    def test_proj_b_agent_y_no_skills_gets_only_global(self, e2e_setup):
        loader = e2e_setup
        skills = loader.get_skills_for_task(
            task_type="prediction", project_id="proj_b", agent_id="agent_y"
        )
        skill_ids = [s.metadata.skill_id for s in skills]

        assert "global_safety_check" in skill_ids
        assert "project_custom_validation" not in skill_ids
        assert "agent_expert_rules" not in skill_ids
