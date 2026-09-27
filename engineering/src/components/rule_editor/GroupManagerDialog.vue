<template>
  <el-dialog
    :model-value="visible"
    :title="t('groupManagerDialog.title')"
    width="600px"
    @update:model-value="$emit('update:visible', $event)"
    @close="handleClose"
  >
    <div class="group-manager">
      <div class="group-form">
        <el-input
          v-model="groupName"
          :placeholder="t('groupManagerDialog.namePlaceholder')"
          style="width: 200px"
          @keyup.enter="handleCreate"
        />
        <el-input
          v-model="groupDesc"
          :placeholder="t('groupManagerDialog.descriptionPlaceholder')"
          style="width: 180px; margin-left: 8px"
        />
        <el-button
          type="primary"
          style="margin-left: 8px"
          @click="handleCreate"
        >
          <el-icon><Plus /></el-icon>
          {{ isEditing ? t('groupManagerDialog.save') : t('groupManagerDialog.create') }}
        </el-button>
      </div>

      <el-table
        :data="ruleStore.groups"
        border
        style="margin-top: 16px"
      >
        <el-table-column
          prop="id"
          :label="t('groupManagerDialog.id')"
          width="70"
        />
        <el-table-column
          prop="name"
          :label="t('groupManagerDialog.groupName')"
        />
        <el-table-column
          prop="description"
          :label="t('groupManagerDialog.description')"
        />
        <el-table-column
          prop="rule_count"
          :label="t('groupManagerDialog.ruleCount')"
          width="90"
        />
        <el-table-column
          :label="t('groupManagerDialog.operation')"
          width="160"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              @click="handleEdit(row as RuleGroup)"
            >
              {{ t('groupManagerDialog.edit') }}
            </el-button>
            <el-popconfirm
              :title="t('groupManagerDialog.deleteConfirm', { name: row.name })"
              :confirm-button-text="t('groupManagerDialog.delete')"
              :cancel-button-text="t('groupManagerDialog.cancel')"
              @confirm="handleDelete(row.id)"
            >
              <template #reference>
                <el-button
                  size="small"
                  type="danger"
                  link
                  :disabled="row.rule_count > 0"
                >
                  {{ t('groupManagerDialog.delete') }}
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('groupManagerDialog.close') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { useRuleStore } from '@/stores/rules'
import type { RuleGroup } from '@/types'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  group: RuleGroup | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  saved: []
}>()

const ruleStore = useRuleStore()
const groupName = ref('')
const groupDesc = ref('')
const isEditing = ref(false)

watch(
  () => props.group,
  (newGroup) => {
    if (newGroup) {
      groupName.value = newGroup.name
      groupDesc.value = newGroup.description
      isEditing.value = true
    } else {
      groupName.value = ''
      groupDesc.value = ''
      isEditing.value = false
    }
  },
  { immediate: true }
)

async function handleCreate() {
  if (!groupName.value.trim()) {
    ElMessage.warning(t('groupManagerDialog.nameRequired'))
    return
  }

  try {
    if (isEditing.value && props.group?.id) {
      await ruleStore.updateGroup(props.group.id, {
        name: groupName.value,
        description: groupDesc.value,
      })
    } else {
      await ruleStore.createGroup({
        name: groupName.value,
        description: groupDesc.value,
      })
    }
    groupName.value = ''
    groupDesc.value = ''
    isEditing.value = false
    emit('saved')
  } catch (e: unknown) {
    // createGroup/updateGroup 已在 store 内通过 ElMessage.error 提示用户，
    // 此处仅记录调用栈上下文便于排查，避免重复弹窗
    console.warn('[GroupManagerDialog] handleSave failed:', e)
  }
}

function handleEdit(group: RuleGroup) {
  groupName.value = group.name
  groupDesc.value = group.description
  isEditing.value = true
}

async function handleDelete(groupId?: number) {
  if (!groupId) return
  await ruleStore.deleteGroup(groupId)
}

function handleClose() {
  groupName.value = ''
  groupDesc.value = ''
  isEditing.value = false
}
</script>

<style scoped>
.group-form {
  display: flex;
  align-items: center;
}
</style>
