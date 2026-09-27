<template>
  <div class="quality-inspection-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-header__title">
        <h1 class="page-title">
          {{ t('qualityInspection.pageTitle') }}
        </h1>
        <p class="page-subtitle">
          {{ t('qualityInspection.pageSubtitle') }}
        </p>
      </div>
      <div class="page-header__actions">
        <el-button
          type="primary"
          size="small"
          :icon="Plus"
          @click="handleNewInspection"
        >
          {{ t('qualityInspection.btnNewInspection') }}
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <QualityStatsCards :stats="statsCards" />

    <!-- 检测记录 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">{{ t('qualityInspection.recordsTitle') }}</span>
        <div style="display: flex; gap: 8px;">
          <el-select
            v-model="resultFilter"
            :placeholder="t('qualityInspection.filterResult')"
            size="small"
            style="width: 130px"
          >
            <el-option
              :label="t('qualityInspection.filterAll')"
              value="all"
            />
            <el-option
              :label="t('qualityInspection.filterPass')"
              value="pass"
            />
            <el-option
              :label="t('qualityInspection.filterFail')"
              value="fail"
            />
          </el-select>
          <el-input
            v-model="searchKeyword"
            :placeholder="t('qualityInspection.searchPlaceholder')"
            clearable
            size="small"
            style="width: 180px"
          />
        </div>
      </div>
      <div class="content-card__body">
        <QualityRecordsTable
          :records="records"
          :loading="loading"
          :load-error="loadError"
          @view="handleViewDetail"
        />
      </div>
    </div>

    <!-- 新建检测弹窗 -->
    <el-dialog
      v-model="newDialogVisible"
      :title="t('qualityInspection.dialogNewTitle')"
      width="480px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form label-width="90px" @submit.prevent>
        <el-form-item :label="t('qualityInspection.fieldBatchNo')" required>
          <el-input
            v-model="newForm.batch_no"
            :placeholder="t('qualityInspection.placeholderBatchNo')"
            maxlength="64"
          />
        </el-form-item>
        <el-form-item :label="t('qualityInspection.fieldInspectionType')" required>
          <el-select
            v-model="newForm.inspection_type"
            :placeholder="t('qualityInspection.placeholderInspectionType')"
            style="width: 100%"
          >
            <el-option :label="t('qualityInspection.typeIncoming')" value="进料检验" />
            <el-option :label="t('qualityInspection.typeProcess')" value="过程检验" />
            <el-option :label="t('qualityInspection.typeFinished')" value="成品检验" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('qualityInspection.fieldResult')" required>
          <el-radio-group v-model="newForm.result">
            <el-radio value="合格">{{ t('qualityInspection.filterPass') }}</el-radio>
            <el-radio value="不合格">{{ t('qualityInspection.filterFail') }}</el-radio>
            <el-radio value="待判定">待判定</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('qualityInspection.fieldInspector')" required>
          <el-input
            v-model="newForm.inspector"
            :placeholder="t('qualityInspection.placeholderInspector')"
            maxlength="32"
          />
        </el-form-item>
        <el-form-item :label="t('qualityInspection.fieldNotes')">
          <el-input
            v-model="newForm.notes"
            type="textarea"
            :rows="2"
            :placeholder="t('qualityInspection.placeholderNotes')"
            maxlength="200"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newDialogVisible = false">
          {{ t('qualityInspection.btnCancel') }}
        </el-button>
        <el-button
          type="primary"
          :loading="newDialogSubmitting"
          @click="submitInspection"
        >
          {{ t('qualityInspection.btnSubmit') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 检测详情弹窗 -->
    <el-dialog
      v-model="detailDialogVisible"
      :title="t('qualityInspection.dialogDetailTitle')"
      width="520px"
    >
      <div v-loading="detailLoading" style="min-height: 200px">
        <template v-if="detailData">
          <el-descriptions :column="1" border>
            <el-descriptions-item :label="t('qualityInspection.fieldInspectionNo')">
              {{ detailData.inspection_no }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('qualityInspection.fieldInspectionType')">
              {{ detailData.inspection_type }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('qualityInspection.colBatch')">
              {{ detailData.batch_no }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('qualityInspection.fieldResult')">
              <el-tag :type="resultTagType(detailData.result)" size="small" effect="light">
                {{ detailData.result }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item :label="t('qualityInspection.fieldInspector')">
              {{ detailData.inspector }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('qualityInspection.fieldNotes')">
              {{ detailData.notes || '—' }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('qualityInspection.fieldCreatedAt')">
              {{ detailData.created_at }}
            </el-descriptions-item>
          </el-descriptions>
        </template>
      </div>
      <template #footer>
        <el-button @click="detailDialogVisible = false">
          {{ t('qualityInspection.btnCancel') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, type Component } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Checked, DataLine, WarningFilled } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'
import QualityStatsCards from '@/components/quality/QualityStatsCards.vue'
import QualityRecordsTable from '@/components/quality/QualityRecordsTable.vue'

const { t } = useI18n()

// 类型定义
interface InspectionRecord {
  id: string | number
  inspection_no: string
  batch_no: string
  inspection_type: string
  result: string
  inspector: string
  notes?: string | null
  created_at: string
  updated_at?: string
}

/** 新建检测表单数据。 */
interface InspectionForm {
  batch_no: string
  inspection_type: string
  result: string
  inspector: string
  notes: string
}

interface StatsCard {
  label: string
  value: string | number
  icon: Component
  type: 'primary' | 'success' | 'danger' | 'warning' | 'info' | 'default'
}

// 状态
const loading = ref(false)
const loadError = ref(false)
const searchKeyword = ref('')
const resultFilter = ref('all')

const records = ref<InspectionRecord[]>([])
const statsData = ref({ total: 0, pass: 0, fail: 0, pass_rate: 0 })

// 新建检测弹窗
const newDialogVisible = ref(false)
const newDialogSubmitting = ref(false)
const newForm = ref<InspectionForm>({
  batch_no: '',
  inspection_type: '',
  result: '',
  inspector: '',
  notes: '',
})

// 详情弹窗
const detailDialogVisible = ref(false)
const detailLoading = ref(false)
const detailData = ref<InspectionRecord | null>(null)

// 计算属性
const statsCards = computed<StatsCard[]>(() => {
  return [
    { label: t('qualityInspection.statToday'), value: statsData.value.total, icon: Checked, type: 'default' },
    { label: t('qualityInspection.statPassRate'), value: statsData.value.pass_rate + '%', icon: DataLine, type: 'success' },
    { label: t('qualityInspection.statAbnormal'), value: statsData.value.fail, icon: WarningFilled, type: 'danger' },
  ]
})

// 方法
function resultTagType(result: string): 'success' | 'danger' | 'warning' {
  const map: Record<string, 'success' | 'danger' | 'warning'> = {
    [t('qualityInspection.filterPass')]: 'success',
    [t('qualityInspection.filterFail')]: 'danger',
  }
  return map[result] || 'info'
}

async function fetchStats() {
  try {
    // 后端实际路径 /api/v1/quality/stats/，返回 { today_count, pass_rate, anomaly_count, ... }
    const res = await http.get(API_CONFIG.QUALITY + '/stats/')
    if (res.data.code === 0 && res.data.data) {
      const s = res.data.data
      statsData.value = {
        total: s.today_count ?? 0,
        pass: s.pass_count ?? 0,
        fail: s.anomaly_count ?? 0,
        pass_rate: s.pass_rate ?? 0,
      }
    }
  } catch (e: unknown) {
    // 统计加载失败不影响记录列表，但需记录便于排查
    console.warn('[QualityInspection] fetchStats failed:', e)
  }
}

async function fetchRecords() {
  loading.value = true
  loadError.value = false
  try {
    const params: Record<string, string> = {}
    if (resultFilter.value !== 'all') {
      params.result = resultFilter.value === 'pass' ? t('qualityInspection.filterPass') : t('qualityInspection.filterFail')
    }
    if (searchKeyword.value.trim()) {
      // 后端列表接口按 keyword 匹配批次号/检验类型/检测员
      params.keyword = searchKeyword.value.trim()
    }
    // 后端实际路径 /api/v1/quality/，返回 { records, total, limit, offset }
    const res = await http.get(API_CONFIG.QUALITY + '/', { params })
    if (res.data.code === 0 && res.data.data) {
      records.value = res.data.data.records ?? []
    } else {
      records.value = []
      loadError.value = true
    }
  } catch {
    records.value = []
    loadError.value = true
  } finally {
    loading.value = false
  }
}

/** 打开新建检测弹窗。 */
function handleNewInspection() {
  newForm.value = { batch_no: '', inspection_type: '', result: '', inspector: '', notes: '' }
  newDialogVisible.value = true
}

/** 提交新建检测记录。 */
async function submitInspection() {
  const f = newForm.value
  if (!f.batch_no.trim() || !f.inspection_type || !f.result || !f.inspector.trim()) {
    ElMessage.warning(t('qualityInspection.msgFormIncomplete'))
    return
  }
  newDialogSubmitting.value = true
  try {
    const res = await http.post(API_CONFIG.QUALITY + '/', {
      batch_no: f.batch_no.trim(),
      inspection_type: f.inspection_type,
      result: f.result,
      inspector: f.inspector.trim(),
      notes: f.notes.trim() || null,
    })
    if (res.data.code === 0) {
      ElMessage.success(t('qualityInspection.msgCreateSuccess'))
      newDialogVisible.value = false
      fetchRecords()
      fetchStats()
    } else {
      ElMessage.error(res.data.message || t('qualityInspection.msgCreateFailed'))
    }
  } catch (e: unknown) {
    console.warn('[QualityInspection] create failed:', e)
    ElMessage.error(t('qualityInspection.msgCreateFailed'))
  } finally {
    newDialogSubmitting.value = false
  }
}

/** 查看检测详情（GET /api/v1/quality/{id}）。 */
async function handleViewDetail(row: InspectionRecord) {
  detailDialogVisible.value = true
  detailLoading.value = true
  detailData.value = null
  try {
    const res = await http.get(API_CONFIG.QUALITY + `/${row.id}`)
    if (res.data.code === 0 && res.data.data) {
      detailData.value = res.data.data
    } else {
      ElMessage.error(res.data.message || t('qualityInspection.msgDetailLoadFailed'))
    }
  } catch (e: unknown) {
    console.warn('[QualityInspection] fetch detail failed:', e)
    ElMessage.error(t('qualityInspection.msgDetailLoadFailed'))
  } finally {
    detailLoading.value = false
  }
}

// 防抖处理
let debounceTimer: ReturnType<typeof setTimeout> | null = null
function debouncedFetchRecords() {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    fetchRecords()
  }, 300)
}

// 生命周期
onMounted(() => {
  fetchRecords()
  fetchStats()
})

onUnmounted(() => {
  // 清理防抖定时器，避免组件卸载后定时器仍触发状态更新
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
})

// 筛选/搜索变化时防抖请求
watch([resultFilter, searchKeyword], () => {
  debouncedFetchRecords()
})
</script>

<style scoped>
.quality-inspection-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}
</style>
