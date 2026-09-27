<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { WarningFilled, CircleCheckFilled } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'

const { t } = useI18n()

interface CollisionInfo {
  position: [number, number, number]
  severity: 'warning' | 'critical'
  toolSegment: number
  description: string
}

interface Props {
  visible: boolean
  collisions: CollisionInfo[]
}

const props = withDefaults(defineProps<Props>(), {
  visible: false,
  collisions: () => [],
})

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'locate', index: number): void
  (e: 'dismiss'): void
  (e: 'dismiss-all'): void
}>()

const hasCollisions = computed(() => props.collisions.length > 0)
const collisionCount = computed(() => props.collisions.length)

const formatPosition = (pos: [number, number, number]): string => {
  return `(${pos[0].toFixed(2)}, ${pos[1].toFixed(2)}, ${pos[2].toFixed(2)})`
}

const severityLabel = (severity: 'warning' | 'critical'): string => {
  return severity === 'critical' ? t('simulation.collisionAlert.severityCritical') : t('simulation.collisionAlert.severityWarning')
}

const severityTagType = (severity: 'warning' | 'critical'): 'danger' | 'warning' => {
  return severity === 'critical' ? 'danger' : 'warning'
}

const handleClose = () => {
  emit('update:visible', false)
}

const handleLocate = (index: number) => {
  emit('locate', index)
  handleClose()
}

const handleDismiss = () => {
  emit('dismiss')
  handleClose()
}

const handleDismissAll = async () => {
  try {
    await ElMessageBox.confirm(
      t('simulation.collisionAlert.confirmDismissAllMsg'),
      t('simulation.collisionAlert.confirmDismissAllTitle'),
      {
        confirmButtonText: t('simulation.collisionAlert.confirmDismissAllBtn'),
        cancelButtonText: t('common.cancel'),
        type: 'warning',
        customClass: 'collision-confirm-dialog',
      }
    )
    emit('dismiss-all')
    handleClose()
    ElMessage({
      message: t('simulation.collisionAlert.dismissedAllMsg'),
      type: 'info',
      duration: 2000,
    })
  } catch {
    // 用户取消，不做任何操作
  }
}

const handleLocateFirst = () => {
  if (props.collisions.length > 0) {
    emit('locate', 0)
    handleClose()
  }
}

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      // Dialog opened - collision detection state is managed by parent component
    }
  }
)
</script>

<template>
  <el-dialog
    :model-value="visible"
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="true"
    class="collision-alert-dialog"
    :show-close="true"
    align-center
    destroy-on-close
    @update:model-value="emit('update:visible', $event)"
  >
    <template #header>
      <div class="collision-dialog-header">
        <el-icon
          class="header-icon"
          :size="20"
          color="var(--state-warning)"
        >
          <WarningFilled />
        </el-icon>
        <span class="header-title">{{ t('simulation.collisionAlert.dialogTitle') }}</span>
      </div>
    </template>

    <div class="collision-dialog-body">
      <!-- 无碰撞 -->
      <div
        v-if="!hasCollisions"
        class="collision-empty"
      >
        <el-icon
          class="empty-icon"
          :size="48"
          color="var(--accent-primary)"
        >
          <CircleCheckFilled />
        </el-icon>
        <p class="empty-text">
          {{ t('simulation.collisionAlert.noCollision') }}
        </p>
        <p class="empty-sub">
          {{ t('simulation.collisionAlert.noCollisionHint') }}
        </p>
      </div>

      <!-- 有碰撞 -->
      <div
        v-else
        class="collision-content"
      >
        <div class="collision-summary">
          <span class="summary-label">{{ t('simulation.collisionAlert.collisionDetectedPrefix') }}</span>
          <span class="summary-count">{{ collisionCount }}</span>
          <span class="summary-label">{{ t('simulation.collisionAlert.collisionDetectedSuffix') }}</span>
        </div>

        <div class="collision-list">
          <div
            v-for="(collision, index) in collisions"
            :key="`${collision.toolSegment}-${collision.position[0]}-${collision.position[1]}-${collision.position[2]}`"
            class="collision-card"
            :class="[`severity-${collision.severity}`]"
          >
            <div class="card-header">
              <el-tag
                :type="severityTagType(collision.severity)"
                size="small"
                effect="dark"
                class="severity-tag"
              >
                {{ severityLabel(collision.severity) }}
              </el-tag>
              <span class="card-index">#{{ index + 1 }}</span>
            </div>

            <div class="card-body">
              <div class="card-info-row">
                <span class="info-label">{{ t('simulation.collisionAlert.positionLabel') }}</span>
                <span class="info-value position-value">
                  {{ formatPosition(collision.position) }}
                </span>
              </div>
              <div class="card-info-row">
                <span class="info-label">{{ t('simulation.collisionAlert.toolSegmentLabel') }}</span>
                <span class="info-value">
                  #{{ collision.toolSegment }}
                </span>
              </div>
              <div class="card-info-row">
                <span class="info-label">{{ t('simulation.collisionAlert.descriptionLabel') }}</span>
                <span class="info-value description-text">
                  {{ collision.description }}
                </span>
              </div>
            </div>

            <div class="card-footer">
              <el-button
                size="small"
                type="primary"
                @click="handleLocate(index)"
              >
                {{ t('simulation.collisionAlert.btnLocate') }}
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="collision-dialog-footer">
        <template v-if="hasCollisions">
          <el-button @click="handleDismiss">
            {{ t('simulation.collisionAlert.btnDismiss') }}
          </el-button>
          <el-button @click="handleDismissAll">
            {{ t('simulation.collisionAlert.btnDismissAll') }}
          </el-button>
          <el-button
            type="primary"
            @click="handleLocateFirst"
          >
            {{ t('simulation.collisionAlert.btnLocateFirst') }}
          </el-button>
        </template>
        <template v-else>
          <el-button
            type="primary"
            @click="handleClose"
          >
            {{ t('simulation.collisionAlert.btnClose') }}
          </el-button>
        </template>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
/* ========== Dialog Container ========== */
.collision-alert-dialog :deep(.el-dialog) {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-xl);
}

.collision-alert-dialog :deep(.el-dialog__header) {
  padding: 16px 24px;
  margin: 0;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-light);
}

.collision-alert-dialog :deep(.el-dialog__headerbtn) {
  top: 16px;
  right: 16px;
}

.collision-alert-dialog :deep(.el-dialog__headerbtn .el-dialog__close) {
  color: var(--text-secondary);
  font-size: 16px;
}

.collision-alert-dialog :deep(.el-dialog__headerbtn:hover .el-dialog__close) {
  color: var(--text-primary);
}

.collision-alert-dialog :deep(.el-dialog__body) {
  padding: 0;
  background: var(--bg-card);
  color: var(--text-primary);
}

.collision-alert-dialog :deep(.el-dialog__footer) {
  padding: 12px 24px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-light);
}

/* ========== Header ========== */
.collision-dialog-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-icon {
  flex-shrink: 0;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.5px;
}

/* ========== Body ========== */
.collision-dialog-body {
  max-height: 480px;
  overflow-y: auto;
  padding: 20px 24px;
}

/* Scrollbar */
.collision-dialog-body::-webkit-scrollbar {
  width: 6px;
}

.collision-dialog-body::-webkit-scrollbar-track {
  background: transparent;
}

.collision-dialog-body::-webkit-scrollbar-thumb {
  background: var(--border-light);
  border-radius: var(--radius-2xs);
}

.collision-dialog-body::-webkit-scrollbar-thumb:hover {
  background: var(--bg-400);
}

/* ========== Empty State ========== */
.collision-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;
}

.empty-icon {
  margin-bottom: 16px;
  opacity: 0.85;
}

.empty-text {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 8px 0;
}

.empty-sub {
  font-size: 13px;
  color: var(--text-muted);
  margin: 0;
}

/* ========== Summary ========== */
.collision-summary {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
  background: var(--accent-light);
  border-radius: var(--radius-md);
  border: 1px solid var(--accent-border);
}

.summary-label {
  font-size: 14px;
  color: var(--text-secondary);
}

.summary-count {
  font-size: 18px;
  font-weight: 700;
  color: var(--state-warning);
  min-width: 24px;
  text-align: center;
}

/* ========== Collision List ========== */
.collision-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* ========== Collision Card ========== */
.collision-card {
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border-left: 3px solid transparent;
  overflow: hidden;
  transition: background-color 0.2s ease;
}

.collision-card:hover {
  background: var(--bg-tertiary);
}

.collision-card.severity-critical {
  border-left-color: var(--state-error);
}

.collision-card.severity-warning {
  border-left-color: var(--state-warning);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px 0 16px;
}

.severity-tag {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.card-index {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.card-body {
  padding: 8px 16px 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.card-info-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.info-label {
  color: var(--text-muted);
  flex-shrink: 0;
  min-width: 52px;
}

.info-value {
  color: var(--text-secondary);
  word-break: break-all;
}

.position-value {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--text-primary);
  background: var(--bg-200);
  padding: 1px 6px;
  border-radius: var(--radius-xs);
  letter-spacing: 0.3px;
}

.description-text {
  color: var(--text-primary);
  opacity: 0.9;
}

.card-footer {
  padding: 8px 16px 12px 16px;
  display: flex;
  justify-content: flex-end;
}

/* ========== Footer ========== */
.collision-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* ========== Element Plus Overrides ========== */
.collision-alert-dialog :deep(.el-button--primary) {
  --el-button-bg-color: var(--accent-primary);
  --el-button-border-color: var(--accent-primary);
  --el-button-hover-bg-color: var(--brand-400);
  --el-button-hover-border-color: var(--brand-400);
  --el-button-active-bg-color: var(--brand-600);
  --el-button-active-border-color: var(--brand-600);
}

.collision-alert-dialog :deep(.el-button--default) {
  --el-button-bg-color: transparent;
  --el-button-border-color: var(--border-light);
  --el-button-text-color: var(--text-secondary);
  --el-button-hover-bg-color: var(--bg-tertiary);
  --el-button-hover-border-color: var(--border-medium);
  --el-button-hover-text-color: var(--text-primary);
}

.collision-alert-dialog :deep(.el-button--small) {
  font-size: 12px;
  padding: 5px 12px;
  height: 28px;
}

/* Mask overlay */
.collision-alert-dialog :deep(.el-overlay) {
  backdrop-filter: blur(2px);
  background-color: var(--bg-overlay);
}
</style>
