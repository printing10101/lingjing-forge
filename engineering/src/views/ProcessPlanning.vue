<template>
  <div class="process-planning-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t('processPlanning.routePage.pageTitle') }}</h1>
        <p>{{ t('processPlanning.routePage.pageSubtitle') }}</p>
      </div>
      <div class="page-header__actions">
        <el-button
          type="primary"
          size="small"
          @click="handleCreate"
        >
          <el-icon :size="16">
            <Plus />
          </el-icon>
          {{ t('processPlanning.routePage.btnNewRoute') }}
        </el-button>
      </div>
    </div>

    <ProcessPlanningFilters
      v-model:search-keyword="searchKeyword"
      v-model:filter-type="filterType"
      v-model:filter-status="filterStatus"
    />

    <!-- 工艺路线卡片列表 -->
    <div
      v-if="loading"
      class="loading-state"
    >
      <el-icon
        class="loading-icon"
        :size="32"
      >
        <Clock />
      </el-icon>
      <span>{{ t('processPlanning.routePage.loading') }}</span>
    </div>
    <div
      v-else-if="loadError"
      class="empty-state"
    >
      <span>{{ t('processPlanning.routePage.loadFailed') }}</span>
      <el-button
        type="primary"
        text
        size="small"
        @click="fetchRoutes"
      >
        {{ t('processPlanning.routePage.btnReload') }}
      </el-button>
    </div>
    <div
      v-else-if="filteredRoutes.length === 0"
      class="empty-state"
    >
      <span>{{ t('processPlanning.routePage.emptyData') }}</span>
    </div>
    <div
      v-else
      class="card-grid"
    >
      <ProcessRouteCard
        v-for="route in filteredRoutes"
        :key="route.id"
        :route="route"
        @select="openDetail"
        @view="handleView"
        @edit="handleEdit"
        @copy="handleCopy"
        @delete="handleDelete"
      />
    </div>

    <ProcessRouteDetail
      :visible="!!selectedRoute"
      :route="selectedRoute"
      @close="closeDetail"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  Plus,
  Clock,
} from '@element-plus/icons-vue'
import ProcessPlanningFilters from '@/components/process_planning/ProcessPlanningFilters.vue'
import ProcessRouteDetail from '@/components/process_planning/ProcessRouteDetail.vue'
import ProcessRouteCard from '@/components/process_planning/ProcessRouteCard.vue'
import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

const { t } = useI18n()

// 类型定义
interface ProcessStep {
  name: string
  description: string
  duration: string
  tool_id?: number
  parameters?: Record<string, unknown>
}

interface ProcessRoute {
  id: number
  name: string
  part_type: string
  description: string
  status: string
  version: string
  material_type: string
  steps: ProcessStep[]
  created_at: string
  updated_at: string
}

// 数据
const routeList = ref<ProcessRoute[]>([])
const loading = ref(false)
const loadError = ref(false)

// 状态到中文的映射（用于筛选下拉框与后端 status 中文值之间的桥接）
const statusValueMap: Record<string, string> = {
  [t('processPlanning.routePage.statusPublished')]: 'published',
  [t('processPlanning.routePage.statusDraft')]: 'draft',
  [t('processPlanning.routePage.statusArchived')]: 'archived',
}

/** 从后端中文 status 反推英文值，供前端筛选逻辑使用 */
function statusToEnglish(cn: string): string {
  return statusValueMap[cn] || ''
}

// API 数据加载
async function fetchRoutes() {
  loading.value = true
  loadError.value = false
  try {
    const params: Record<string, string> = {}
    if (filterStatus.value !== 'all') {
      params.status = filterStatus.value
    }
    if (searchKeyword.value.trim()) {
      params.keyword = searchKeyword.value.trim()
    }
    const res = await http.get(API_CONFIG.PROCESS_ROUTES + '/', { params })
    const data = res.data?.data
    routeList.value = Array.isArray(data) ? data : (data?.routes ?? [])
  } catch {
    loadError.value = true
    routeList.value = []
  } finally {
    loading.value = false
  }
}

// 筛选状态
const searchKeyword = ref('')
const filterType = ref('all')
const filterStatus = ref('all')

// 详情面板
const selectedRoute = ref<ProcessRoute | null>(null)

// 计算属性
const filteredRoutes = computed(() => {
  return routeList.value.filter(route => {
    const keyword = searchKeyword.value.trim().toLowerCase()
    if (keyword && !route.name.toLowerCase().includes(keyword) && !route.description.toLowerCase().includes(keyword)) {
      return false
    }
    if (filterType.value !== 'all' && route.material_type !== filterType.value) {
      return false
    }
    if (filterStatus.value !== 'all' && statusToEnglish(route.status) !== filterStatus.value) {
      return false
    }
    return true
  })
})

// 方法
function openDetail(route: ProcessRoute) {
  selectedRoute.value = route
}

function closeDetail() {
  selectedRoute.value = null
}

async function handleCreate() {
  try {
    const res = await http.post(API_CONFIG.PROCESS_ROUTES + '/', {
      name: t('processPlanning.routePage.defaultRouteName'),
      part_type: '',
      description: '',
      status: t('processPlanning.routePage.statusDraft'),
      steps: [],
    })
    const newRoute = res.data.data as ProcessRoute
    routeList.value.unshift(newRoute)
    ElMessage.success(t('processPlanning.routePage.msgCreateSuccess'))
  } catch {
    // http 拦截器已处理错误提示
  }
}

function handleView(route: ProcessRoute) {
  openDetail(route)
}

async function handleEdit(route: ProcessRoute) {
  try {
    const res = await http.put(`${API_CONFIG.PROCESS_ROUTES}/${route.id}`, {
      ...route,
    })
    const updated = res.data.data as ProcessRoute
    const idx = routeList.value.findIndex(r => r.id === route.id)
    if (idx !== -1) routeList.value[idx] = updated
    if (selectedRoute.value?.id === route.id) selectedRoute.value = updated
    ElMessage.success(t('processPlanning.routePage.msgUpdateSuccess', { name: route.name }))
  } catch {
    // http 拦截器已处理错误提示
  }
}

async function handleCopy(route: ProcessRoute) {
  try {
    const res = await http.post(API_CONFIG.PROCESS_ROUTES + '/', {
      name: `${route.name}${t('processPlanning.routePage.copySuffix')}`,
      part_type: route.part_type,
      description: route.description,
      status: t('processPlanning.routePage.statusDraft'),
      version: route.version,
      material_type: route.material_type,
      steps: route.steps,
    })
    const copied = res.data.data as ProcessRoute
    routeList.value.unshift(copied)
    ElMessage.success(t('processPlanning.routePage.msgCopySuccess', { name: route.name }))
  } catch {
    // http 拦截器已处理错误提示
  }
}

function handleDelete(route: ProcessRoute) {
  ElMessageBox.confirm(
    t('processPlanning.routePage.msgDeleteConfirm', { name: route.name }),
    t('processPlanning.routePage.deleteConfirmTitle'),
    {
      confirmButtonText: t('processPlanning.routePage.btnConfirmDelete'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    },
  ).then(async () => {
    try {
      await http.delete(`${API_CONFIG.PROCESS_ROUTES}/${route.id}`)
      routeList.value = routeList.value.filter(r => r.id !== route.id)
      if (selectedRoute.value?.id === route.id) {
        closeDetail()
      }
      ElMessage.success(t('processPlanning.routePage.msgDeleteSuccess', { name: route.name }))
    } catch {
      // http 拦截器已处理错误提示
    }
  }).catch(() => {
    // 用户取消
  })
}

// 生命周期
onMounted(() => {
  fetchRoutes()
})
</script>

<style scoped>
.process-planning-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}

/* 卡片网格 */
.card-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
}

/* 加载与空状态 */
.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-tertiary);
  font-size: 14px;
}

.loading-icon {
  animation: spin 1.2s linear infinite;
  color: var(--accent-primary);
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 响应式 */
@media (max-width: 900px) {
  .card-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 600px) {
  .process-planning-page {
    padding: 16px;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
}
</style>
