<template>
  <div class="dialect-manager">
    <el-card class="header-card">
      <div class="header-content">
        <h2>{{ t('dialectManager.pageTitle') }}</h2>
        <div class="actions">
          <el-button type="primary" :loading="loading" @click="loadList">
            <el-icon><Refresh /></el-icon> {{ t('dialectManager.btnRefresh') }}
          </el-button>
          <el-button type="success" @click="showCreateDialog = true">
            <el-icon><Plus /></el-icon> {{ t('dialectManager.btnCreate') }}
          </el-button>
          <el-input
            v-model="searchQuery"
            :placeholder="t('dialectManager.searchPlaceholder')"
            style="width: 220px; margin-left: 10px"
            clearable
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
      </div>
      <p class="hint">{{ t('dialectManager.pageHint') }}</p>
    </el-card>

    <!-- 新建方言向导对话框 -->
    <el-dialog v-model="showCreateDialog" :title="t('dialectManager.createTitle')" width="480px">
      <el-form label-width="90px">
        <el-form-item :label="t('dialectManager.fieldId')">
          <el-input
            v-model="createForm.id"
            :placeholder="t('dialectManager.placeholderId')"
            data-testid="create-id"
          />
        </el-form-item>
        <el-form-item :label="t('dialectManager.fieldName')">
          <el-input v-model="createForm.name" :placeholder="t('dialectManager.placeholderName')" />
        </el-form-item>
        <el-form-item :label="t('dialectManager.fieldExtends')">
          <el-select v-model="createForm.extends" style="width: 100%">
            <el-option
              v-for="base in builtinBaseDialects"
              :key="base"
              :value="base"
              :label="base"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('dialectManager.fieldDescription')">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="2"
            :placeholder="t('dialectManager.placeholderDescription')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">{{ t('dialectManager.btnCancel') }}</el-button>
        <el-button type="primary" :loading="createLoading" @click="handleCreate">
          {{ t('dialectManager.btnCreate') }}
        </el-button>
      </template>
    </el-dialog>

    <el-card class="content-card">
      <el-row :gutter="16">
        <!-- 左：方言列表 -->
        <el-col :span="10">
          <el-table
            :data="filteredDialects"
            highlight-current-row
            :row-key="(row: any) => row.id"
            @current-change="handleSelect"
          >
            <el-table-column prop="name" :label="t('dialectManager.colName')" min-width="160" />
            <el-table-column prop="version" :label="t('dialectManager.colVersion')" width="90" />
            <el-table-column :label="t('dialectManager.colSource')" width="100">
              <template #default="{ row }">
                <el-tag :type="row.source === 'declared' ? 'success' : 'info'" size="small">
                  {{ row.source === 'declared' ? t('dialectManager.srcDeclared') : t('dialectManager.srcBuiltin') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('dialectManager.colTemplateCount')" width="110" align="center">
              <template #default="{ row }">
                {{ row.template_methods?.length ?? 0 }}
              </template>
            </el-table-column>
          </el-table>
        </el-col>

        <!-- 右：详情 + 预览 -->
        <el-col :span="14">
          <template v-if="selected">
            <el-descriptions :title="selected.name" :column="2" border size="small">
              <el-descriptions-item :label="t('dialectManager.descId')">{{ selected.id }}</el-descriptions-item>
              <el-descriptions-item :label="t('dialectManager.descVersion')">{{ selected.version }}</el-descriptions-item>
              <el-descriptions-item :label="t('dialectManager.descExtends')">{{ selected.extends ?? '—' }}</el-descriptions-item>
              <el-descriptions-item :label="t('dialectManager.descSource')">
                <el-tag :type="selected.source === 'declared' ? 'success' : 'info'" size="small">
                  {{ selected.source === 'declared' ? t('dialectManager.srcDeclared') : t('dialectManager.srcBuiltin') }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="t('dialectManager.descDescription')" :span="2">
                {{ selected.description || '—' }}
              </el-descriptions-item>
            </el-descriptions>

            <!-- 声明镜像：模板方法列表 + 编辑 + 编译状态 -->
            <div v-if="selected.source === 'declared'" class="template-section">
              <div class="section-title">
                {{ t('dialectManager.sectionTemplates') }}
                <el-tag v-if="selected.compile_ok" type="success" size="small">
                  {{ t('dialectManager.compileOk') }}
                </el-tag>
                <el-tag v-else type="danger" size="small">
                  {{ t('dialectManager.compileError') }}
                </el-tag>
                <el-button
                  type="primary"
                  size="small"
                  :loading="savingTemplate"
                  :disabled="templateContent === null"
                  @click="handleSaveTemplate"
                >
                  <el-icon><Check /></el-icon> {{ t('dialectManager.btnSaveTemplate') }}
                </el-button>
              </div>
              <el-alert
                v-if="selected.compile_error"
                :title="selected.compile_error"
                type="error"
                :closable="false"
                show-icon
              />
              <el-radio-group v-model="selectedTemplateMethod" size="small" class="method-group" @change="loadTemplate">
                <el-radio-button
                  v-for="m in (selected.template_methods ?? [])"
                  :key="m"
                  :value="m"
                >
                  {{ shortMethodName(m) }}
                </el-radio-button>
              </el-radio-group>
              <el-input
                v-if="templateContent !== null"
                v-model="templateContent"
                type="textarea"
                :rows="8"
                class="template-editor"
                :placeholder="t('dialectManager.templatePlaceholder')"
              />
            </div>

            <!-- 参数编辑（遗留项⑤：工艺员在页面调参数而非改 YAML） -->
            <div v-if="selected.source === 'declared'" class="params-section">
              <div class="section-title">
                {{ t('dialectManager.sectionParams') }}
                <el-button
                  type="primary"
                  size="small"
                  :loading="savingParams"
                  @click="handleSaveParams"
                >
                  <el-icon><Check /></el-icon> {{ t('dialectManager.btnSaveParams') }}
                </el-button>
              </div>
              <el-input
                v-model="paramsJson"
                type="textarea"
                :rows="6"
                class="params-editor"
                :placeholder="t('dialectManager.paramsPlaceholder')"
              />
              <p v-if="paramsError" class="params-error">{{ paramsError }}</p>
              <p class="params-hint">
                {{ t('dialectManager.paramsHint') }}: {{ effectiveParamsSummary }}
              </p>
            </div>

            <!-- 删除声明式方言 -->
            <div v-if="selected.source === 'declared'" class="danger-zone">
              <el-button type="danger" size="small" plain @click="handleDelete">
                <el-icon><Delete /></el-icon> {{ t('dialectManager.btnDelete') }}
              </el-button>
            </div>

            <!-- NC 实时预览器（杀手锏） -->
            <div class="preview-section">
              <div class="section-title">
                {{ t('dialectManager.sectionPreview') }}
                <el-button
                  type="primary"
                  size="small"
                  :loading="previewLoading"
                  @click="loadPreview"
                >
                  <el-icon><VideoPlay /></el-icon> {{ t('dialectManager.btnPreview') }}
                </el-button>
              </div>
              <pre v-if="previewOutput !== null" class="nc-output">{{ previewOutput }}</pre>
              <el-empty v-else :description="t('dialectManager.previewEmpty')" :image-size="60" />
            </div>
          </template>
          <el-empty v-else :description="t('dialectManager.noSelection')" />
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search, VideoPlay, Plus, Check, Delete } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

import {
  listDialects,
  getDialectDetail,
  readTemplate,
  previewDialect,
  createDialect,
  saveTemplate,
  deleteDialect,
  getDialectParams,
  saveDialectParams,
  type DialectInfo,
} from '@/api/postprocessorDialects'

const { t } = useI18n()

const loading = ref(false)
const dialects = ref<DialectInfo[]>([])
const selected = ref<DialectInfo | null>(null)
const searchQuery = ref('')
const selectedTemplateMethod = ref('')
const templateContent = ref<string | null>(null)
const previewOutput = ref<string | null>(null)
const previewLoading = ref(false)

// 新建向导状态
const showCreateDialog = ref(false)
const createLoading = ref(false)
const createForm = ref({
  id: '',
  name: '',
  extends: 'fanuc_0i',
  description: '',
})
const savingTemplate = ref(false)

// 参数编辑状态（遗留项⑤）
const paramsJson = ref('')
const paramsError = ref('')
const savingParams = ref(false)
const effectiveParams = ref<Record<string, unknown>>({})

const effectiveParamsSummary = computed(() => {
  const keys = Object.keys(effectiveParams.value)
  return keys.length ? keys.slice(0, 6).join(', ') + (keys.length > 6 ? '...' : '') : '—'
})

// 可继承的内置基类方言（与后端 BUILTIN_BASE_DIALECTS 对齐）
const builtinBaseDialects = [
  'fanuc_0i',
  'siemens_840d',
  'heidenhain_tnc',
  'gsk_980_25i',
  'hnc_848_22',
  'knd_1000_2000_3000',
  'mitsubishi_m70_m80',
  'fagor_8055',
  'xmachine_xm100',
]

const filteredDialects = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return dialects.value
  return dialects.value.filter(
    (d) =>
      d.id.toLowerCase().includes(q) ||
      d.name.toLowerCase().includes(q) ||
      d.description.toLowerCase().includes(q),
  )
})

function shortMethodName(method: string): string {
  return method.replace(/^format_/, '').replace(/_/g, ' ')
}

async function loadList() {
  loading.value = true
  try {
    const result = await listDialects()
    dialects.value = result.dialects ?? []
  } catch (e) {
    ElMessage.error(t('dialectManager.msgLoadFailed'))
    console.error('[dialectManager] loadList failed:', e)
  } finally {
    loading.value = false
  }
}

async function handleSelect(row: DialectInfo | null) {
  if (!row) return
  selected.value = null
  templateContent.value = null
  previewOutput.value = null
  selectedTemplateMethod.value = ''
  paramsJson.value = ''
  paramsError.value = ''
  effectiveParams.value = {}
  try {
    selected.value = await getDialectDetail(row.id)
    if (selected.value.source === 'declared') {
      await loadParams()
    }
    if (selected.value.template_methods?.length) {
      selectedTemplateMethod.value = selected.value.template_methods[0]
      await loadTemplate()
    }
  } catch (e) {
    ElMessage.error(t('dialectManager.msgDetailFailed'))
    console.error('[dialectManager] detail failed:', e)
    selected.value = row
  }
}

async function loadParams() {
  if (!selected.value || selected.value.source !== 'declared') return
  try {
    const result = await getDialectParams(selected.value.id)
    effectiveParams.value = result.effective ?? {}
    paramsJson.value = JSON.stringify(result.dialect_params ?? {}, null, 2)
    paramsError.value = ''
  } catch (e) {
    paramsError.value = String(e)
    console.error('[dialectManager] load params failed:', e)
  }
}

async function handleSaveParams() {
  if (!selected.value) return
  let parsed: Record<string, unknown>
  try {
    parsed = paramsJson.value.trim() ? JSON.parse(paramsJson.value) : {}
  } catch (e) {
    paramsError.value = String(e)
    ElMessage.error(t('dialectManager.msgParamsInvalidJson'))
    return
  }
  savingParams.value = true
  try {
    await saveDialectParams(selected.value.id, parsed)
    ElMessage.success(t('dialectManager.msgParamsSaved'))
    await loadParams() // 刷新有效配置摘要
    await loadPreview() // 参数变化 → 预览同步
  } catch (e) {
    ElMessage.error(t('dialectManager.msgParamsSaveFailed'))
    console.error('[dialectManager] save params failed:', e)
  } finally {
    savingParams.value = false
  }
}

async function loadTemplate() {
  if (!selected.value || !selectedTemplateMethod.value) return
  try {
    const result = await readTemplate(selected.value.id, selectedTemplateMethod.value)
    templateContent.value = result.content
  } catch (e) {
    templateContent.value = null
    console.error('[dialectManager] template failed:', e)
  }
}

async function loadPreview() {
  if (!selected.value) return
  previewLoading.value = true
  try {
    const result = await previewDialect(selected.value.id)
    previewOutput.value = result.output
  } catch (e) {
    ElMessage.error(t('dialectManager.msgPreviewFailed'))
    console.error('[dialectManager] preview failed:', e)
  } finally {
    previewLoading.value = false
  }
}

async function handleCreate() {
  if (!createForm.value.id || !createForm.value.name) {
    ElMessage.warning(t('dialectManager.msgCreateRequired'))
    return
  }
  createLoading.value = true
  try {
    await createDialect({ ...createForm.value })
    ElMessage.success(t('dialectManager.msgCreated'))
    showCreateDialog.value = false
    createForm.value = { id: '', name: '', extends: 'fanuc_0i', description: '' }
    await loadList()
  } catch (e) {
    ElMessage.error(t('dialectManager.msgCreateFailed'))
    console.error('[dialectManager] create failed:', e)
  } finally {
    createLoading.value = false
  }
}

async function handleSaveTemplate() {
  if (!selected.value || !selectedTemplateMethod.value || templateContent.value === null) return
  savingTemplate.value = true
  try {
    await saveTemplate(selected.value.id, selectedTemplateMethod.value, templateContent.value)
    ElMessage.success(t('dialectManager.msgTemplateSaved'))
    await loadPreview() // 保存后立即刷新预览，工艺员立刻看到效果
  } catch (e) {
    ElMessage.error(t('dialectManager.msgTemplateSaveFailed'))
    console.error('[dialectManager] save template failed:', e)
  } finally {
    savingTemplate.value = false
  }
}

async function handleDelete() {
  if (!selected.value) return
  try {
    await ElMessageBox.confirm(
      t('dialectManager.msgConfirmDelete'),
      t('dialectManager.deleteTitle'),
      { type: 'warning', confirmButtonText: t('dialectManager.btnDelete'), cancelButtonText: t('dialectManager.btnCancel') },
    )
  } catch {
    return // 用户取消
  }
  const id = selected.value.id
  try {
    await deleteDialect(id)
    ElMessage.success(t('dialectManager.msgDeleted'))
    selected.value = null
    templateContent.value = null
    previewOutput.value = null
    await loadList()
  } catch (e) {
    ElMessage.error(t('dialectManager.msgDeleteFailed'))
    console.error('[dialectManager] delete failed:', e)
  }
}

onMounted(loadList)

// 暴露内部方法供测试驱动（组件实例代理；生产环境无副作用）
defineExpose({
  handleSelect,
  loadPreview,
  loadTemplate,
  handleCreate,
  handleSaveTemplate,
  handleDelete,
  loadParams,
  handleSaveParams,
  selected,
  previewOutput,
  templateContent,
  selectedTemplateMethod,
  createForm,
  showCreateDialog,
  paramsJson,
})
</script>

<style scoped>
.header-card .header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 8px;
}

.template-section,
.preview-section {
  margin-top: 16px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 8px;
}

.method-group {
  margin-bottom: 8px;
}

.template-preview {
  background: var(--el-fill-color-light);
  border-radius: 4px;
  padding: 10px;
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.template-editor {
  font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}

.params-section {
  margin-top: 16px;
}

.params-editor {
  font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}

.params-error {
  color: var(--el-color-danger);
  font-size: 12px;
}

.params-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.danger-zone {
  margin-top: 12px;
}

.nc-output {
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 6px;
  padding: 12px;
  font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  max-height: 320px;
  overflow: auto;
  white-space: pre;
}
</style>
