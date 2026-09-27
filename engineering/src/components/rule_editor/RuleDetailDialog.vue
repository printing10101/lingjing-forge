<template>
  <el-dialog
    :model-value="visible"
    :title="$t('ruleEditor.detailTitle')"
    width="700px"
    @update:model-value="$emit('update:visible', $event)"
  >
    <div
      v-if="rule"
      class="rule-detail"
    >
      <el-descriptions
        :column="2"
        border
      >
        <el-descriptions-item :label="$t('ruleEditor.ruleId')">
          {{ rule.id }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.ruleName')">
          {{ rule.name }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.status')">
          <el-tag :type="getRuleStatusTagType(rule.status)">
            {{ getRuleStatusLabel(rule.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.priority')">
          {{ rule.priority }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.group')">
          {{ rule.group_id ?? '-' }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.logicOperator')">
          {{ rule.logic_operator }}
        </el-descriptions-item>
      </el-descriptions>

      <h4 class="section-title">
        {{ $t('ruleEditor.conditions') }}
      </h4>
      <el-table
        :data="rule.conditions"
        border
        size="small"
      >
        <el-table-column
          prop="parameter"
          :label="$t('ruleEditor.parameter')"
        />
        <el-table-column
          prop="operator"
          :label="$t('ruleEditor.operator')"
          width="100"
        />
        <el-table-column
          prop="value"
          :label="$t('ruleEditor.value')"
        />
        <el-table-column
          prop="unit"
          :label="$t('ruleEditor.unit')"
          width="80"
        />
      </el-table>

      <h4 class="section-title">
        {{ $t('ruleEditor.result') }}
      </h4>
      <el-descriptions
        :column="4"
        border
        size="small"
      >
        <el-descriptions-item :label="$t('ruleEditor.parameter')">
          {{ rule.result?.parameter }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.operator')">
          {{ rule.result?.operator }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.value')">
          {{ rule.result?.value }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('ruleEditor.unit')">
          {{ rule.result?.unit || '-' }}
        </el-descriptions-item>
      </el-descriptions>

      <h4 class="section-title">
        {{ $t('ruleEditor.rulePreview') }}
      </h4>
      <el-alert
        :title="rule.preview_text"
        type="info"
        :closable="false"
      />

      <p
        v-if="rule.description"
        class="description"
      >
        <strong>{{ $t('ruleEditor.description') }}：</strong>{{ rule.description }}
      </p>
      <p class="time-info">
        {{ $t('ruleEditor.timeInfo', { created: rule.created_at, updated: rule.updated_at }) }}
      </p>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import type { ProcessRule } from '@/types'
import { getRuleStatusTagType, getRuleStatusLabel } from '@/utils/statusHelpers'

defineProps<{
  visible: boolean
  rule: ProcessRule | null
}>()

defineEmits<{
  'update:visible': [value: boolean]
}>()
</script>

<style scoped>
.rule-detail {
  padding: 12px 0;
}

.section-title {
  margin: 20px 0 12px 0;
  font-size: 16px;
  color: var(--text-primary);
  border-left: 3px solid var(--accent-primary);
  padding-left: 8px;
  font-weight: 600;
}

.description {
  margin-top: 16px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.6;
}

.time-info {
  margin-top: 16px;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: right;
}
</style>