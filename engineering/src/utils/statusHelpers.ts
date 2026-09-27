/**
 * 状态标签映射工具函数
 * 统一管理各业务模块的状态标签类型和中文名称映射
 */

export type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

// 任务状态 (Task Status)
export const TASK_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  in_progress: '进行中',
}

export const TASK_STATUS_TAG_TYPES: Record<string, TagType> = {
  completed: 'success',
  in_progress: 'warning',
  running: 'info',
  failed: 'danger',
  cancelled: 'warning',
  queued: 'info',
}

export function getTaskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] || status
}

export function getTaskStatusTagType(status: string): TagType {
  return TASK_STATUS_TAG_TYPES[status] || 'info'
}

// 审批状态 (Approval Status)
export const APPROVAL_STATUS_LABELS: Record<string, string> = {
  pending: '待审批',
  under_review: '审核中',
  approved: '已通过',
  rejected: '已拒绝',
  escalated: '已升级',
}

export const APPROVAL_STATUS_TAG_TYPES: Record<string, TagType> = {
  pending: 'info',
  under_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  escalated: 'warning',
}

export function getApprovalStatusLabel(status: string): string {
  return APPROVAL_STATUS_LABELS[status] || status
}

export function getApprovalStatusTagType(status: string): TagType {
  return APPROVAL_STATUS_TAG_TYPES[status] || 'info'
}

// 目标层级 (Goal Level)
export const GOAL_LEVEL_LABELS: Record<string, string> = {
  mission: '使命',
  strategic_goal: '战略目标',
  project: '项目',
  task: '任务',
}

export const GOAL_LEVEL_TAG_TYPES: Record<string, TagType> = {
  mission: 'danger',
  strategic_goal: 'warning',
  project: 'primary',
  task: 'success',
}

export const GOAL_STATUS_LABELS: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
  cancelled: '已取消',
  needs_review: '待审核',
}

export const GOAL_STATUS_TAG_TYPES: Record<string, TagType> = {
  not_started: 'info',
  in_progress: 'warning',
  completed: 'success',
  cancelled: 'danger',
  needs_review: 'warning',
}

export function getGoalLevelLabel(level: string): string {
  return GOAL_LEVEL_LABELS[level] || level
}

export function getGoalLevelTagType(level: string): TagType {
  return GOAL_LEVEL_TAG_TYPES[level] || 'info'
}

export function getGoalStatusLabel(status: string): string {
  return GOAL_STATUS_LABELS[status] || status
}

export function getGoalStatusTagType(status: string): TagType {
  return GOAL_STATUS_TAG_TYPES[status] || 'info'
}

// 优先级 (Priority)
export const PRIORITY_LABELS: Record<string | number, string> = {
  1: '紧急',
  2: '高',
  3: '普通',
  4: '低',
  critical: '紧急',
  high: '高',
  medium: '普通',
  low: '低',
}

export const PRIORITY_TAG_TYPES: Record<string | number, TagType> = {
  1: 'danger',
  2: 'warning',
  3: 'info',
  4: 'info',
  critical: 'danger',
  high: 'warning',
  medium: 'info',
  low: 'info',
}

export function getPriorityLabel(priority: string | number): string {
  return PRIORITY_LABELS[priority] || String(priority)
}

export function getPriorityTagType(priority: string | number): TagType {
  return PRIORITY_TAG_TYPES[priority] || 'info'
}

// 分支类型 (Branch Type)
export const BRANCH_TYPE_LABELS: Record<string, string> = {
  main: '主分支',
  industry: '行业',
  material: '材料',
  project: '项目',
  experiment: '实验',
  imported: '导入',
}

export const BRANCH_TYPE_TAG_TYPES: Record<string, TagType> = {
  main: 'success',
  industry: 'warning',
  material: 'primary',
  project: 'info',
  experiment: 'danger',
  imported: 'info',
}

export function getBranchTypeLabel(type: string): string {
  return BRANCH_TYPE_LABELS[type] || type
}

export function getBranchTypeTagType(type: string): TagType {
  return BRANCH_TYPE_TAG_TYPES[type] || 'info'
}

// 审计日志模块 (Audit Log Module)
export const AUDIT_MODULE_LABELS: Record<string, string> = {
  lnn_predict: 'LNN预测',
  lnn_train: 'LNN训练',
  process_optimize: '工艺优化',
  tool_wear_analyze: '刀具磨损分析',
  cad_generate: 'CAD生成',
}

export function getAuditModuleName(module: string): string {
  return AUDIT_MODULE_LABELS[module] || module
}

// 审计日志决策 (Audit Log Decision)
export const AUDIT_DECISION_LABELS: Record<string, string> = {
  accept: '接受',
  modify: '修改',
  reject: '拒绝',
  auto_executed: '自动执行',
}

export const AUDIT_DECISION_TAG_TYPES: Record<string, TagType> = {
  accept: 'success',
  modify: 'warning',
  reject: 'danger',
  auto_executed: 'info',
}

export function getAuditDecisionLabel(decision: string): string {
  return AUDIT_DECISION_LABELS[decision] || decision
}

export function getAuditDecisionTagType(decision: string): TagType {
  return AUDIT_DECISION_TAG_TYPES[decision] || 'info'
}

// 通用状态 (Generic Status)
export const GENERIC_STATUS_LABELS: Record<string, string> = {
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
  pending: '待处理',
}

export const GENERIC_STATUS_TAG_TYPES: Record<string, TagType> = {
  success: 'success',
  failed: 'danger',
  cancelled: 'warning',
  pending: 'info',
}

export function getGenericStatusLabel(status: string): string {
  return GENERIC_STATUS_LABELS[status] || status
}

export function getGenericStatusTagType(status: string): TagType {
  return GENERIC_STATUS_TAG_TYPES[status] || 'info'
}

// 规则状态 (Rule Status)
export const RULE_STATUS_LABELS: Record<string, string> = {
  active: '启用',
  inactive: '停用',
  draft: '草稿',
}

export const RULE_STATUS_TAG_TYPES: Record<string, TagType> = {
  active: 'success',
  inactive: 'info',
  draft: 'warning',
}

export function getRuleStatusLabel(status: string): string {
  return RULE_STATUS_LABELS[status] || status
}

export function getRuleStatusTagType(status: string): TagType {
  return RULE_STATUS_TAG_TYPES[status] || 'info'
}

// 置信度 (Confidence)
export const CONFIDENCE_THRESHOLDS = {
  HIGH: 0.8,
  MEDIUM: 0.5,
}

export function getConfidenceLabel(value: number): string {
  if (value >= CONFIDENCE_THRESHOLDS.HIGH) return '高置信度'
  if (value >= CONFIDENCE_THRESHOLDS.MEDIUM) return '中置信度'
  return '低置信度'
}

export function getConfidenceTagType(value: number): TagType {
  if (value >= CONFIDENCE_THRESHOLDS.HIGH) return 'success'
  if (value >= CONFIDENCE_THRESHOLDS.MEDIUM) return 'warning'
  return 'danger'
}

export function getConfidenceColor(value: number): string {
  if (value >= CONFIDENCE_THRESHOLDS.HIGH) return '#67c23a'
  if (value >= CONFIDENCE_THRESHOLDS.MEDIUM) return '#e6a23c'
  return '#f56c6c'
}
