import { describe, it, expect } from 'vitest'
import {
  TASK_STATUS_LABELS,
  TASK_STATUS_TAG_TYPES,
  getTaskStatusLabel,
  getTaskStatusTagType,
  APPROVAL_STATUS_LABELS,
  APPROVAL_STATUS_TAG_TYPES,
  getApprovalStatusLabel,
  getApprovalStatusTagType,
  GOAL_LEVEL_LABELS,
  GOAL_LEVEL_TAG_TYPES,
  GOAL_STATUS_LABELS,
  GOAL_STATUS_TAG_TYPES,
  getGoalLevelLabel,
  getGoalLevelTagType,
  getGoalStatusLabel,
  getGoalStatusTagType,
  PRIORITY_LABELS,
  PRIORITY_TAG_TYPES,
  getPriorityLabel,
  getPriorityTagType,
  BRANCH_TYPE_LABELS,
  BRANCH_TYPE_TAG_TYPES,
  getBranchTypeLabel,
  getBranchTypeTagType,
  AUDIT_MODULE_LABELS,
  getAuditModuleName,
  AUDIT_DECISION_LABELS,
  AUDIT_DECISION_TAG_TYPES,
  getAuditDecisionLabel,
  getAuditDecisionTagType,
  GENERIC_STATUS_LABELS,
  GENERIC_STATUS_TAG_TYPES,
  getGenericStatusLabel,
  getGenericStatusTagType,
  RULE_STATUS_LABELS,
  RULE_STATUS_TAG_TYPES,
  getRuleStatusLabel,
  getRuleStatusTagType,
  CONFIDENCE_THRESHOLDS,
  getConfidenceLabel,
  getConfidenceTagType,
  getConfidenceColor,
  type TagType,
} from '@/utils/statusHelpers'

describe('statusHelpers', () => {
  describe('任务状态 (Task Status)', () => {
    it('返回任务状态中文名称', () => {
      expect(getTaskStatusLabel('queued')).toBe('排队中')
      expect(getTaskStatusLabel('running')).toBe('运行中')
      expect(getTaskStatusLabel('completed')).toBe('已完成')
      expect(getTaskStatusLabel('failed')).toBe('失败')
      expect(getTaskStatusLabel('cancelled')).toBe('已取消')
      expect(getTaskStatusLabel('in_progress')).toBe('进行中')
    })

    it('未知状态返回原值', () => {
      expect(getTaskStatusLabel('unknown')).toBe('unknown')
    })

    it('返回任务状态标签类型', () => {
      expect(getTaskStatusTagType('completed')).toBe('success')
      expect(getTaskStatusTagType('in_progress')).toBe('warning')
      expect(getTaskStatusTagType('running')).toBe('info')
      expect(getTaskStatusTagType('failed')).toBe('danger')
      expect(getTaskStatusTagType('cancelled')).toBe('warning')
      expect(getTaskStatusTagType('queued')).toBe('info')
    })

    it('未知状态返回 info 标签类型', () => {
      expect(getTaskStatusTagType('unknown')).toBe('info')
    })

    it('TASK_STATUS_LABELS 与 TASK_STATUS_TAG_TYPES key 一致', () => {
      Object.keys(TASK_STATUS_LABELS).forEach((key) => {
        expect(TASK_STATUS_TAG_TYPES[key]).toBeDefined()
      })
    })
  })

  describe('审批状态 (Approval Status)', () => {
    it('返回审批状态中文名称', () => {
      expect(getApprovalStatusLabel('pending')).toBe('待审批')
      expect(getApprovalStatusLabel('under_review')).toBe('审核中')
      expect(getApprovalStatusLabel('approved')).toBe('已通过')
      expect(getApprovalStatusLabel('rejected')).toBe('已拒绝')
      expect(getApprovalStatusLabel('escalated')).toBe('已升级')
    })

    it('未知审批状态返回原值', () => {
      expect(getApprovalStatusLabel('unknown')).toBe('unknown')
    })

    it('返回审批状态标签类型', () => {
      expect(getApprovalStatusTagType('pending')).toBe('info')
      expect(getApprovalStatusTagType('under_review')).toBe('warning')
      expect(getApprovalStatusTagType('approved')).toBe('success')
      expect(getApprovalStatusTagType('rejected')).toBe('danger')
      expect(getApprovalStatusTagType('escalated')).toBe('warning')
    })

    it('未知审批状态返回 info 标签类型', () => {
      expect(getApprovalStatusTagType('unknown')).toBe('info')
    })

    it('APPROVAL_STATUS 常量映射完整', () => {
      expect(Object.keys(APPROVAL_STATUS_LABELS)).toHaveLength(5)
      expect(Object.keys(APPROVAL_STATUS_TAG_TYPES)).toHaveLength(5)
    })
  })

  describe('目标层级 (Goal Level)', () => {
    it('返回目标层级中文名称', () => {
      expect(getGoalLevelLabel('mission')).toBe('使命')
      expect(getGoalLevelLabel('strategic_goal')).toBe('战略目标')
      expect(getGoalLevelLabel('project')).toBe('项目')
      expect(getGoalLevelLabel('task')).toBe('任务')
    })

    it('未知层级返回原值', () => {
      expect(getGoalLevelLabel('unknown')).toBe('unknown')
    })

    it('返回目标层级标签类型', () => {
      expect(getGoalLevelLabel('mission')) // 验证常量存在
      expect(getGoalLevelTagType('mission')).toBe('danger')
      expect(getGoalLevelTagType('strategic_goal')).toBe('warning')
      expect(getGoalLevelTagType('project')).toBe('primary')
      expect(getGoalLevelTagType('task')).toBe('success')
    })

    it('未知层级返回 info 标签类型', () => {
      expect(getGoalLevelTagType('unknown')).toBe('info')
    })

    it('GOAL_LEVEL_LABELS 与 GOAL_LEVEL_TAG_TYPES key 一致', () => {
      Object.keys(GOAL_LEVEL_LABELS).forEach((key) => {
        expect(GOAL_LEVEL_TAG_TYPES[key]).toBeDefined()
      })
    })
  })

  describe('目标状态 (Goal Status)', () => {
    it('返回目标状态中文名称', () => {
      expect(getGoalStatusLabel('not_started')).toBe('未开始')
      expect(getGoalStatusLabel('in_progress')).toBe('进行中')
      expect(getGoalStatusLabel('completed')).toBe('已完成')
      expect(getGoalStatusLabel('cancelled')).toBe('已取消')
      expect(getGoalStatusLabel('needs_review')).toBe('待审核')
    })

    it('未知状态返回原值', () => {
      expect(getGoalStatusLabel('unknown')).toBe('unknown')
    })

    it('返回目标状态标签类型', () => {
      expect(getGoalStatusTagType('not_started')).toBe('info')
      expect(getGoalStatusTagType('in_progress')).toBe('warning')
      expect(getGoalStatusTagType('completed')).toBe('success')
      expect(getGoalStatusTagType('cancelled')).toBe('danger')
      expect(getGoalStatusTagType('needs_review')).toBe('warning')
    })

    it('GOAL_STATUS 常量映射完整', () => {
      expect(Object.keys(GOAL_STATUS_LABELS)).toHaveLength(5)
      expect(Object.keys(GOAL_STATUS_TAG_TYPES)).toHaveLength(5)
    })
  })

  describe('优先级 (Priority)', () => {
    it('返回数字优先级的中文名称', () => {
      expect(getPriorityLabel(1)).toBe('紧急')
      expect(getPriorityLabel(2)).toBe('高')
      expect(getPriorityLabel(3)).toBe('普通')
      expect(getPriorityLabel(4)).toBe('低')
    })

    it('返回字符串优先级的中文名称', () => {
      expect(getPriorityLabel('critical')).toBe('紧急')
      expect(getPriorityLabel('high')).toBe('高')
      expect(getPriorityLabel('medium')).toBe('普通')
      expect(getPriorityLabel('low')).toBe('低')
    })

    it('未知优先级返回字符串形式', () => {
      expect(getPriorityLabel('unknown')).toBe('unknown')
      expect(getPriorityLabel(99)).toBe('99')
    })

    it('返回优先级标签类型', () => {
      expect(getPriorityTagType(1)).toBe('danger')
      expect(getPriorityTagType(2)).toBe('warning')
      expect(getPriorityTagType(3)).toBe('info')
      expect(getPriorityTagType(4)).toBe('info')
      expect(getPriorityTagType('critical')).toBe('danger')
      expect(getPriorityTagType('high')).toBe('warning')
      expect(getPriorityTagType('medium')).toBe('info')
      expect(getPriorityTagType('low')).toBe('info')
    })

    it('未知优先级返回 info 标签类型', () => {
      expect(getPriorityTagType('unknown')).toBe('info')
    })

    it('PRIORITY 常量同时支持数字与字符串', () => {
      // 数字键与字符串键应保持语义一致
      expect(PRIORITY_LABELS[1]).toBe(PRIORITY_LABELS['critical'])
      expect(PRIORITY_LABELS[2]).toBe(PRIORITY_LABELS['high'])
      expect(PRIORITY_LABELS[3]).toBe(PRIORITY_LABELS['medium'])
      expect(PRIORITY_LABELS[4]).toBe(PRIORITY_LABELS['low'])
      expect(PRIORITY_TAG_TYPES[1]).toBe(PRIORITY_TAG_TYPES['critical'])
      expect(PRIORITY_TAG_TYPES[2]).toBe(PRIORITY_TAG_TYPES['high'])
    })
  })

  describe('分支类型 (Branch Type)', () => {
    it('返回分支类型中文名称', () => {
      expect(getBranchTypeLabel('main')).toBe('主分支')
      expect(getBranchTypeLabel('industry')).toBe('行业')
      expect(getBranchTypeLabel('material')).toBe('材料')
      expect(getBranchTypeLabel('project')).toBe('项目')
      expect(getBranchTypeLabel('experiment')).toBe('实验')
      expect(getBranchTypeLabel('imported')).toBe('导入')
    })

    it('未知分支类型返回原值', () => {
      expect(getBranchTypeLabel('unknown')).toBe('unknown')
    })

    it('返回分支类型标签类型', () => {
      expect(getBranchTypeTagType('main')).toBe('success')
      expect(getBranchTypeTagType('industry')).toBe('warning')
      expect(getBranchTypeTagType('material')).toBe('primary')
      expect(getBranchTypeTagType('project')).toBe('info')
      expect(getBranchTypeTagType('experiment')).toBe('danger')
      expect(getBranchTypeTagType('imported')).toBe('info')
    })

    it('BRANCH_TYPE 常量映射完整', () => {
      Object.keys(BRANCH_TYPE_LABELS).forEach((key) => {
        expect(BRANCH_TYPE_TAG_TYPES[key]).toBeDefined()
      })
    })
  })

  describe('审计模块 (Audit Module)', () => {
    it('返回审计模块中文名称', () => {
      expect(getAuditModuleName('lnn_predict')).toBe('LNN预测')
      expect(getAuditModuleName('lnn_train')).toBe('LNN训练')
      expect(getAuditModuleName('process_optimize')).toBe('工艺优化')
      expect(getAuditModuleName('tool_wear_analyze')).toBe('刀具磨损分析')
      expect(getAuditModuleName('cad_generate')).toBe('CAD生成')
    })

    it('未知模块返回原值', () => {
      expect(getAuditModuleName('unknown')).toBe('unknown')
    })

    it('AUDIT_MODULE_LABELS 包含 5 个模块', () => {
      expect(Object.keys(AUDIT_MODULE_LABELS)).toHaveLength(5)
    })
  })

  describe('审计决策 (Audit Decision)', () => {
    it('返回审计决策中文名称', () => {
      expect(getAuditDecisionLabel('accept')).toBe('接受')
      expect(getAuditDecisionLabel('modify')).toBe('修改')
      expect(getAuditDecisionLabel('reject')).toBe('拒绝')
      expect(getAuditDecisionLabel('auto_executed')).toBe('自动执行')
    })

    it('未知决策返回原值', () => {
      expect(getAuditDecisionLabel('unknown')).toBe('unknown')
    })

    it('返回审计决策标签类型', () => {
      expect(getAuditDecisionTagType('accept')).toBe('success')
      expect(getAuditDecisionTagType('modify')).toBe('warning')
      expect(getAuditDecisionTagType('reject')).toBe('danger')
      expect(getAuditDecisionTagType('auto_executed')).toBe('info')
    })

    it('AUDIT_DECISION 常量映射完整', () => {
      Object.keys(AUDIT_DECISION_LABELS).forEach((key) => {
        expect(AUDIT_DECISION_TAG_TYPES[key]).toBeDefined()
      })
    })
  })

  describe('通用状态 (Generic Status)', () => {
    it('返回通用状态中文名称', () => {
      expect(getGenericStatusLabel('success')).toBe('成功')
      expect(getGenericStatusLabel('failed')).toBe('失败')
      expect(getGenericStatusLabel('cancelled')).toBe('已取消')
      expect(getGenericStatusLabel('pending')).toBe('待处理')
    })

    it('未知状态返回原值', () => {
      expect(getGenericStatusLabel('unknown')).toBe('unknown')
    })

    it('返回通用状态标签类型', () => {
      expect(getGenericStatusTagType('success')).toBe('success')
      expect(getGenericStatusTagType('failed')).toBe('danger')
      expect(getGenericStatusTagType('cancelled')).toBe('warning')
      expect(getGenericStatusTagType('pending')).toBe('info')
    })

    it('GENERIC_STATUS 常量映射完整', () => {
      Object.keys(GENERIC_STATUS_LABELS).forEach((key) => {
        expect(GENERIC_STATUS_TAG_TYPES[key]).toBeDefined()
      })
    })
  })

  describe('规则状态 (Rule Status)', () => {
    it('返回规则状态中文名称', () => {
      expect(getRuleStatusLabel('active')).toBe('启用')
      expect(getRuleStatusLabel('inactive')).toBe('停用')
      expect(getRuleStatusLabel('draft')).toBe('草稿')
    })

    it('未知状态返回原值', () => {
      expect(getRuleStatusLabel('unknown')).toBe('unknown')
    })

    it('返回规则状态标签类型', () => {
      expect(getRuleStatusTagType('active')).toBe('success')
      expect(getRuleStatusTagType('inactive')).toBe('info')
      expect(getRuleStatusTagType('draft')).toBe('warning')
    })

    it('RULE_STATUS 常量映射完整', () => {
      Object.keys(RULE_STATUS_LABELS).forEach((key) => {
        expect(RULE_STATUS_TAG_TYPES[key]).toBeDefined()
      })
    })
  })

  describe('置信度 (Confidence)', () => {
    it('CONFIDENCE_THRESHOLDS 阈值常量', () => {
      expect(CONFIDENCE_THRESHOLDS.HIGH).toBe(0.8)
      expect(CONFIDENCE_THRESHOLDS.MEDIUM).toBe(0.5)
    })

    it('返回高置信度标签', () => {
      expect(getConfidenceLabel(0.9)).toBe('高置信度')
      expect(getConfidenceLabel(0.8)).toBe('高置信度')
      expect(getConfidenceLabel(1)).toBe('高置信度')
    })

    it('返回中置信度标签', () => {
      expect(getConfidenceLabel(0.5)).toBe('中置信度')
      expect(getConfidenceLabel(0.79)).toBe('中置信度')
    })

    it('返回低置信度标签', () => {
      expect(getConfidenceLabel(0.49)).toBe('低置信度')
      expect(getConfidenceLabel(0)).toBe('低置信度')
    })

    it('返回置信度标签类型', () => {
      expect(getConfidenceTagType(0.9)).toBe('success')
      expect(getConfidenceTagType(0.5)).toBe('warning')
      expect(getConfidenceTagType(0.3)).toBe('danger')
    })

    it('返回置信度颜色值', () => {
      expect(getConfidenceColor(0.9)).toBe('#67c23a')
      expect(getConfidenceColor(0.5)).toBe('#e6a23c')
      expect(getConfidenceColor(0.3)).toBe('#f56c6c')
    })

    it('TagType 类型约束', () => {
      const tagTypes: TagType[] = ['success', 'warning', 'danger', 'info', 'primary']
      tagTypes.forEach((tt) => {
        expect(typeof tt).toBe('string')
      })
    })
  })
})
