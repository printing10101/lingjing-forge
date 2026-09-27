<template>
  <el-card class="toolbar-card">
    <div class="toolbar">
      <div class="toolbar-left">
        <el-input
          v-model="searchKeywordModel"
          :placeholder="$t('ruleEditor.searchPlaceholder')"
          prefix-icon="Search"
          clearable
          style="width: 280px"
          @keyup.enter="$emit('search')"
          @clear="$emit('search')"
        />
        <el-select
          v-model="filterGroupModel"
          :placeholder="$t('ruleEditor.filterByGroup')"
          clearable
          style="width: 180px; margin-left: 12px"
          @change="$emit('search')"
        >
          <el-option
            v-for="g in groups"
            :key="g.id"
            :label="g.name"
            :value="g.id"
          />
        </el-select>
        <el-select
          v-model="filterStatusModel"
          :placeholder="$t('ruleEditor.filterByStatus')"
          clearable
          style="width: 140px; margin-left: 12px"
          @change="$emit('search')"
        >
          <el-option
            :label="$t('ruleEditor.statusActive')"
            value="active"
          />
          <el-option
            :label="$t('ruleEditor.statusInactive')"
            value="inactive"
          />
          <el-option
            :label="$t('ruleEditor.statusDraft')"
            value="draft"
          />
        </el-select>
      </div>
      <div class="toolbar-right">
        <el-button
          type="primary"
          @click="$emit('create-rule')"
        >
          <el-icon><Plus /></el-icon>
          {{ $t('ruleEditor.newRule') }}
        </el-button>
        <el-button @click="$emit('create-group')">
          <el-icon><FolderAdd /></el-icon>
          {{ $t('ruleEditor.newGroup') }}
        </el-button>
        <el-button @click="$emit('export')">
          <el-icon><Download /></el-icon>
          {{ $t('common.export') }}
        </el-button>
        <el-upload
          :show-file-list="false"
          :before-upload="handleImport"
          accept=".json"
        >
          <el-button>
            <el-icon><Upload /></el-icon>
            {{ $t('common.import') }}
          </el-button>
        </el-upload>
        <el-button @click="$emit('backup')">
          <el-icon><CopyDocument /></el-icon>
          {{ $t('common.backup') }}
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Plus, FolderAdd, Download, Upload, CopyDocument } from '@element-plus/icons-vue'

const props = defineProps<{
  searchKeyword: string
  filterGroup: number | undefined
  filterStatus: string | undefined
  groups: { id: number, name: string }[]
}>()

const emit = defineEmits<{
  'update:searchKeyword': [value: string]
  'update:filterGroup': [value: number | undefined]
  'update:filterStatus': [value: string | undefined]
  search: []
  'create-rule': []
  'create-group': []
  export: []
  import: [file: File]
  backup: []
}>()

const searchKeywordModel = computed({
  get: () => props.searchKeyword,
  set: (val: string) => emit('update:searchKeyword', val),
})

const filterGroupModel = computed({
  get: () => props.filterGroup,
  set: (val: number | undefined) => emit('update:filterGroup', val),
})

const filterStatusModel = computed({
  get: () => props.filterStatus,
  set: (val: string | undefined) => emit('update:filterStatus', val),
})

function handleImport(file: File) {
  emit('import', file)
  return false
}
</script>

<style scoped>
.toolbar-card {
  margin-bottom: 16px;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.toolbar-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>