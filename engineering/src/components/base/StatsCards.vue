<!--
  StatsCards 通用统计卡片组件
  
  用于展示统计卡片行，支持动态卡片配置、图标、类型颜色。
  
  ## 使用示例
  <StatsCards 
    :cards="stats" 
    :auto-wrap="true"
    :size="'default'"
    @card-click="handleCardClick"
  />
-->
<template>
  <div
    :class="['stats-cards', 'stats-cards--' + layout]"
    :style="gridStyle"
  >
    <el-card
      v-for="card in cards"
      :key="card.label"
      :class="['stat-card', 'stat-card--' + card.type, { 'stat-card--clickable': card.clickable }]"
      :shadow="hover ? 'hover' : 'never'"
      :size="computedSize"
      @click="handleClick(card)"
      v-bind="customCardClass"
    >
      <template #header v-if="card.headerSlot">
        <slot name="header" :card="card">
          {{ card.headerText }}
        </slot>
      </template>

      <div class="stat-card__content">
        <!-- 图标区域 -->
        <div
          v-if="card.icon || iconSlot"
          class="stat-card__icon"
          :class="'stat-card__icon--' + card.type"
        >
          <!-- 支持动态图标或插槽 -->
          <component
            v-if="typeof card.icon === 'object'"
            :is="card.icon"
            :size="iconSize"
          />
          <slot v-else-if="iconSlot" name="icon" :card="card">
            <el-icon :size="iconSize">
              <component :is="card.icon" />
            </el-icon>
          </slot>
          <slot v-else name="icon" :card="card" />
        </div>

        <!-- 内容区域 -->
        <div class="stat-card__value-section">
          <!-- 值显示 -->
          <div class="stat-card__value" :class="'stat-card__value--' + card.type">
            {{ computedFormatValue(card.value) }}
          </div>
          <!-- 标签显示 -->
          <div class="stat-card__label">
            {{ card.label }}
          </div>
          <!-- 子文本（可选） -->
          <div
            v-if="card.subLabel"
            class="stat-card__sub-label"
          >
            {{ card.subLabel }}
          </div>
        </div>
      </div>

      <!-- 底部操作按钮（可选） -->
      <div
        v-if="card.actionSlot || card.actionText"
        class="stat-card__actions"
      >
        <slot name="action" :card="card">
          <el-button
            v-if="card.actionText"
            :type="card.actionType || 'primary'"
            text
            :size="computedSize"
            @click.stop="handleCardClick(card)"
          >
            {{ card.actionText }}
          </el-button>
        </slot>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useStatsCards, StatsCardItem, type CardSize } from '@/composables/headless/useStatsCards'

interface Props {
  /** 卡片数据列表 */
  cards: StatsCardItem[]
  /** 是否自动换行（grid 模式） */
  autoWrap?: boolean
  /** 卡片尺寸 */
  size?: CardSize
  /** 是否悬停效果 */
  hover?: boolean
  /** 图标大小（像素） */
  iconSize?: number | string
  /** 自定义卡片类名 */
  customCardClass?: Record<string, unknown>
}

const props = withDefaults(defineProps<Props>(), {
  autoWrap: true,
  size: 'default',
  hover: true,
  iconSize: 24,
  customCardClass: () => ({}),
})

/** 定义事件 */
const emit = defineEmits<{
  (e: 'card-click', card: StatsCardItem): void
}>()

/** 使用 Composable 获取处理后的数据 */
const { formatValue, size: getComponentSize } = useStatsCards(
  props.cards,
  { autoWrap: props.autoWrap, size: props.size },
)

/** 计算布局类型 */
const layout = computed(() => (props.autoWrap ? 'auto' : 'fixed'))

/** 计算网格样式 */
const gridStyle = computed(() =>
  props.autoWrap
    ? {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '16px',
      }
    : {
        display: 'grid',
        gridTemplateColumns: `repeat(${Math.min(props.cards.length, 4)}, 1fr)`,
        gap: '16px',
      },
)

/** 计算尺寸 */
const computedSize = computed(() => getComponentSize())

/** 格式化值 */
const computedFormatValue = (value: string | number) => formatValue(value)

/** 处理图标插槽 */
const iconSlot = computed(() => !!props.cards.some((c) => typeof c.icon === 'symbol'))

/** 处理卡片点击 */
function handleCardClick(card: StatsCardItem): void {
  if (card.clickable) {
    emit('card-click', card)
    card.onClick?.()
  }
}

/** 处理卡片内部点击 */
function handleClick(card: StatsCardItem): void {
  if (card.clickable) {
    handleCardClick(card)
  }
}
</script>

<style scoped>
.stats-cards {
  width: 100%;
}

.stats-cards--auto {
}

.stats-cards--fixed {
}

.stat-card {
  position: relative;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.stat-card--clickable {
  cursor: pointer;
}

.stat-card--clickable:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.stat-card__content {
  display: flex;
  align-items: center;
  gap: var(--stat-card-gap, 16px);
}

.stat-card__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: var(--stat-card-icon-size, 44px);
  height: var(--stat-card-icon-size, 44px);
  border-radius: 10px;
  flex-shrink: 0;
  font-size: var(--stat-card-icon-size, 24px);
}

/* 图标颜色 */
.stat-card__icon--primary {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.stat-card__icon--success {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.stat-card__icon--warning {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.stat-card__icon--danger {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.stat-card__icon--info {
  background: var(--el-color-info-light-9);
  color: var(--el-color-info);
}

.stat-card__icon--default {
  background: var(--el-fill-color-light);
  color: var(--text-secondary);
}

.stat-card__value-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-card__value {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--text-primary);
}

.stat-card__value__content {
}

/* 值颜色 */
.stat-card__value--primary {
  color: var(--el-color-primary);
}

.stat-card__value--success {
  color: var(--el-color-success);
}

.stat-card__value--warning {
  color: var(--el-color-warning);
}

.stat-card__value--danger {
  color: var(--el-color-danger);
}

.stat-card__value--info {
  color: var(--el-color-info);
}

.stat-card__value--default {
  color: var(--text-primary);
}

.stat-card__label {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.3;
}

.stat-card__sub-label {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 4px;
}

.stat-card__actions {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

/* 响应式 */
@media (max-width: 768px) {
  .stat-card__icon {
    width: 36px;
    height: 36px;
    font-size: 20px;
  }

  .stat-card__value {
    font-size: 18px;
  }

  .stat-card__label {
    font-size: 11px;
  }
}
</style>
