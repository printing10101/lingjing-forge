<template>
  <div class="rule-editor">
    <div class="page-header">
      <h2>{{ $t('ruleEditor.pageTitle') }}</h2>
      <p class="subtitle">
        {{ $t('ruleEditor.subtitle') }}
      </p>
    </div>

    <RuleEditorStats :stats="ruleStore.stats!" />

    <RuleEditorToolbar
      v-model:search-keyword="searchKeyword"
      v-model:filter-group="filterGroup"
      v-model:filter-status="filterStatus"
      :groups="ruleStore.groups"
      @search="handleSearch"
      @create-rule="ruleStore.openCreateDialog()"
      @create-group="ruleStore.openCreateGroupDialog()"
      @export="handleExport"
      @import="handleImport"
      @backup="handleBackup"
    />

    <el-card class="table-card">
      <el-table
        v-loading="ruleStore.loading"
        :data="ruleStore.rules"
        stripe
        border
        style="width: 100%"
        @sort-change="handleSortChange"
      >
        <el-table-column
          prop="id"
          :label="$t('ruleEditor.tableId')"
          width="70"
          sortable="custom"
        />
        <el-table-column
          prop="name"
          :label="$t('ruleEditor.tableName')"
          min-width="180"
          sortable="custom"
        >
          <template #default="{ row }">
            <el-link
              type="primary"
              @click="handleViewDetail(row as ProcessRule)"
            >
              {{ row.name }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column
          prop="preview_text"
          :label="$t('ruleEditor.tablePreview')"
          min-width="300"
        >
          <template #default="{ row }">
            <span class="preview-text">{{ row.preview_text }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="status"
          :label="$t('ruleEditor.tableStatus')"
          width="100"
          sortable="custom"
        >
          <template #default="{ row }">
            <el-tag
              :type="getRuleStatusTagType(row.status)"
              size="small"
            >
              {{ getRuleStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="priority"
          :label="$t('ruleEditor.tablePriority')"
          width="90"
          sortable="custom"
        />
        <el-table-column
          prop="group_id"
          :label="$t('ruleEditor.tableGroup')"
          width="120"
        >
          <template #default="{ row }">
            {{ getGroupName(row.group_id) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="updated_at"
          :label="$t('ruleEditor.tableUpdated')"
          width="170"
          sortable="custom"
        />
        <el-table-column
          :label="$t('ruleEditor.tableActions')"
          width="200"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              @click="ruleStore.openEditDialog(row as ProcessRule)"
            >
              {{ $t('ruleEditor.edit') }}
            </el-button>
            <el-button
              size="small"
              type="success"
              link
              @click="handleToggleStatus(row as ProcessRule)"
            >
              {{ row.status === 'active' ? $t('ruleEditor.disable') : $t('ruleEditor.enable') }}
            </el-button>
            <el-popconfirm
              :title="$t('ruleEditor.deleteConfirm')"
              :confirm-button-text="$t('common.delete')"
              :cancel-button-text="$t('common.cancel')"
              @confirm="handleDelete(row.id)"
            >
              <template #reference>
                <el-button
                  size="small"
                  type="danger"
                  link
                >
                  {{ $t('common.delete') }}
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="ruleStore.currentPage"
          v-model:page-size="ruleStore.pageSize"
          :total="ruleStore.totalRules"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSearch"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <RuleEditDialog
      v-model:visible="ruleStore.showDialog"
      :rule="ruleStore.editingRule"
      @saved="ruleStore.refreshAll()"
    />

    <GroupManagerDialog
      v-model:visible="ruleStore.showGroupDialog"
      :group="ruleStore.editingGroup"
      @saved="ruleStore.refreshAll()"
    />

    <RuleDetailDialog v-model:visible="detailDialogVisible" :rule="currentDetailRule" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRuleStore } from '@/stores/rules'
import type { ProcessRule } from '@/types'
import RuleEditDialog from '@/components/rule_editor/RuleEditDialog.vue'
import GroupManagerDialog from '@/components/rule_editor/GroupManagerDialog.vue'
import RuleEditorStats from '@/components/rule_editor/RuleEditorStats.vue'
import RuleEditorToolbar from '@/components/rule_editor/RuleEditorToolbar.vue'
import RuleDetailDialog from '@/components/rule_editor/RuleDetailDialog.vue'
import { getRuleStatusTagType, getRuleStatusLabel } from '@/utils/statusHelpers'

const ruleStore = useRuleStore()

const searchKeyword = ref('')
const filterGroup = ref<number | undefined>(undefined)
const filterStatus = ref<string | undefined>(undefined)
const detailDialogVisible = ref(false)
const currentDetailRule = ref<ProcessRule | null>(null)

onMounted(() => {
  ruleStore.refreshAll()
})

function handleSearch() {
  ruleStore.fetchRules({
    keyword: searchKeyword.value || undefined,
    group_id: filterGroup.value,
    status: filterStatus.value,
    page: ruleStore.currentPage,
    page_size: ruleStore.pageSize,
  })
}

function handlePageChange(page: number) {
  ruleStore.fetchRules({
    keyword: searchKeyword.value || undefined,
    group_id: filterGroup.value,
    status: filterStatus.value,
    page,
    page_size: ruleStore.pageSize,
  })
}

function handleSortChange({ prop, order }: { prop: string | null; order: string | null }) {
  ruleStore.fetchRules({
    keyword: searchKeyword.value || undefined,
    group_id: filterGroup.value,
    status: filterStatus.value,
    sort_by: prop || undefined,
    sort_order: order === 'ascending' ? 'ASC' : 'DESC',
    page: ruleStore.currentPage,
    page_size: ruleStore.pageSize,
  })
}

async function handleExport() {
  await ruleStore.exportRules()
}

async function handleImport(file: File) {
  await ruleStore.importRules(file)
  return false
}

async function handleBackup() {
  await ruleStore.backupDatabase()
}

function handleViewDetail(rule: ProcessRule) {
  currentDetailRule.value = rule
  detailDialogVisible.value = true
}

async function handleToggleStatus(rule: ProcessRule) {
  const newStatus = rule.status === 'active' ? 'inactive' : 'active'
  await ruleStore.updateRule(rule.id!, { status: newStatus })
}

async function handleDelete(ruleId?: number) {
  if (!ruleId) return
  await ruleStore.deleteRule(ruleId)
}

function getGroupName(groupId?: number): string {
  if (!groupId) return '-'
  const group = ruleStore.groups.find((g) => g.id === groupId)
  return group?.name || '-'
}

</script>

<style scoped>
.rule-editor {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0 0 4px 0;
  font-size: 24px;
  color: var(--text-primary);
  font-weight: 600;
}

.subtitle {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 14px;
}

.preview-text {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  max-width: 300px;
}

.pagination {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
