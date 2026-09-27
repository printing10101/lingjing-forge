// 仿真运行核心状态与逻辑（从 Simulation.vue 拆出，V1）
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import { useProjectStore } from '@/stores/project'
import { useI18n } from 'vue-i18n'
import type { SimParams, SimResultData, SimState } from './types'

export interface SimulationRunnerOptions {
  // 任务完成回调（组件用于刷新历史等）
  onTaskCompleted?: () => void | Promise<void>
  // 3D 查看器获取器（用于完成时加载体素数据）
  getViewer?: () => { loadVoxelData?: (data: any) => void } | null
}

export function useSimulationRunner(options: SimulationRunnerOptions = {}) {
  const { t } = useI18n()
  const projectStore = useProjectStore()
  const { onTaskCompleted, getViewer } = options

  const gcode = ref('')
  const simParams = ref<SimParams>({
    voxelSize: 1.0,
    toolType: 'flat',
    toolDiameter: 10.0,
    toolLength: 50.0,
    toolCornerRadius: 0.0,
    safeZ: 30.0,
    stockStlPath: '',
  })
  const simState = ref<SimState>('idle')
  const currentTaskId = ref('')
  const simResult = ref<SimResultData | null>(null)
  const showCollisionDetail = ref(false)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function handleRunSimulation() {
    if (!gcode.value.trim()) {
      ElMessage.warning(t('simulationPage.msgNoGcode'))
      return
    }

    simState.value = 'running'
    simResult.value = null
    showCollisionDetail.value = false

    try {
      // Submit async simulation
      const res = await http.post(buildApiPath(API_CONFIG.SIMULATION, '/run/async'), {
        project_id: projectStore.projectId || 'default',
        voxel_size: simParams.value.voxelSize,
        tool_diameter: simParams.value.toolDiameter,
        tool_length: simParams.value.toolLength,
        tool_type: simParams.value.toolType,
        tool_corner_radius: simParams.value.toolCornerRadius,
        gcode: gcode.value,
        safe_z_height: simParams.value.safeZ,
        stock_stl_path: simParams.value.stockStlPath || undefined,
      })

      const responseData = res.data?.data ?? res.data
      const taskId = responseData?.task_id
      if (!taskId) {
        throw new Error(t('simulationPage.msgNoTaskId'))
      }

      currentTaskId.value = taskId
      ElMessage.info(t('simulationPage.msgTaskSubmitted', { taskId }))

      // Start polling
      startPolling(taskId)
    } catch (err: unknown) {
      simState.value = 'failed'
      const msg = err instanceof Error ? err.message : t('simulationPage.msgSubmitFailed')
      ElMessage.error(msg)
    }
  }

  function startPolling(taskId: string) {
    stopPolling()
    pollTimer = setInterval(async () => {
      try {
        const res = await http.get(buildApiPath(API_CONFIG.SIMULATION, '/status/' + taskId))
        const responseData = res.data?.data ?? res.data
        const status = responseData?.status

        if (status === 'completed') {
          stopPolling()
          simState.value = 'completed'
          simResult.value = responseData?.result ?? null

          if (simResult.value?.collision_detected) {
            ElMessage.warning(t('simulationPage.msgCollisionDetected'))
          } else {
            ElMessage.success(t('simulationPage.msgSimPassed'))
          }

          // Try to load STL into the 3D viewer
          const viewer = getViewer?.()
          if (simResult.value?.simulation_result?.workpiece_stl_path && viewer?.loadVoxelData) {
            viewer.loadVoxelData(simResult.value.simulation_result)
          }

          // Refresh history
          if (onTaskCompleted) {
            void onTaskCompleted()
          }
        } else if (status === 'not_found') {
          stopPolling()
          simState.value = 'failed'
          ElMessage.error(t('simulationPage.msgTaskNotFound'))
        }
        // status === 'running' or 'pending' -> continue polling
      } catch {
        // Network error, continue polling
      }
    }, 2000)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // 重置本次仿真运行状态（新建仿真时调用）
  function resetRun() {
    gcode.value = ''
    simResult.value = null
    simState.value = 'idle'
    currentTaskId.value = ''
    showCollisionDetail.value = false
  }

  return {
    gcode,
    simParams,
    simState,
    currentTaskId,
    simResult,
    showCollisionDetail,
    handleRunSimulation,
    startPolling,
    stopPolling,
    resetRun,
  }
}
