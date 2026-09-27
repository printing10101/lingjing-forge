// 动画导出与 STL 下载（从 Simulation.vue 拆出，V1）
import { ref, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import { useI18n } from 'vue-i18n'
import type { SimParams, SimResultData } from './types'

export interface AnimationExportOptions {
  gcode: Ref<string>
  simParams: Ref<SimParams>
  simResult: Ref<SimResultData | null>
}

export function useAnimationExport(opts: AnimationExportOptions) {
  const { t } = useI18n()
  const { gcode, simParams, simResult } = opts

  const gifExport = ref({
    resolution: '1280x720',
    framerate: 15,
    quality: 'medium',
  })

  const mp4Export = ref({
    resolution: '1920x1080',
    framerate: 30,
    codec: 'h264',
    bitrate: '10',
  })

  const exportLoading = ref<'gif' | 'mp4' | null>(null)

  // 导出仿真动画（真实调用 POST /api/simulation/export-animation，blob 下载）
  async function exportAnimation(format: 'gif' | 'mp4') {
    if (!gcode.value.trim()) {
      ElMessage.warning(t('simulationPage.msgNoGcode'))
      return
    }
    exportLoading.value = format
    try {
      const res = await http.post(
        buildApiPath(API_CONFIG.SIMULATION, '/export-animation'),
        {
          nc_code: gcode.value,
          format,
          voxel_size: simParams.value.voxelSize,
          tool_diameter: simParams.value.toolDiameter,
          tool_length: simParams.value.toolLength,
          tool_type: simParams.value.toolType,
        },
        { responseType: 'blob' },
      )
      // 后端返回文件流，触发浏览器下载
      const blob = res.data as Blob
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const ts = new Date().toISOString().replace(/[:.]/g, '-')
      a.href = url
      a.download = 'simulation_' + format + '_' + ts + '.' + format
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      ElMessage.success(t('simulationPage.msgExportSuccess', { format: format.toUpperCase() }))
    } catch (e: unknown) {
      console.warn('[Simulation] export animation failed:', e)
      ElMessage.error(t('simulationPage.msgExportFailed'))
    } finally {
      exportLoading.value = null
    }
  }

  function handleExportGif() {
    void exportAnimation('gif')
  }

  function handleExportMp4() {
    void exportAnimation('mp4')
  }

  function handleDownloadStl() {
    const stlPath = simResult.value?.simulation_result?.workpiece_stl_path
    if (!stlPath) {
      ElMessage.warning(t('simulationPage.msgNoStl'))
      return
    }
    // Extract filename from path
    const filename = stlPath.split('/').pop() || stlPath.split('\\').pop() || 'result.stl'
    const url = '/simulation/output/' + filename
    // Create download link
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
  }

  return {
    gifExport,
    mp4Export,
    exportLoading,
    exportAnimation,
    handleExportGif,
    handleExportMp4,
    handleDownloadStl,
  }
}
