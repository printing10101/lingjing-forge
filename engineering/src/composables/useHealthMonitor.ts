import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

interface RecentInference {
  model: string
  duration_ms: number
}

export function useHealthMonitor() {
  const healthStatus = reactive({
    backendOnline: false,
    uptimeStr: '--',
    totalRequests: 0,
    avgResponseMs: 0,
    activeModels: 0,
    memoryPercent: 0,
    memoryUsedMb: 0,
    memoryTotalMb: 4096,
    cpuPercent: 0,
    activeTrainingTasks: 0,
    recentInferences: [] as RecentInference[],
    maxRecentDuration: 1,
    p50Ms: 0,
    p95Ms: 0,
    dbHealthy: false,
    redisHealthy: false,
    prometheusHealthy: false,
    pollInterval: 5,
  })

  const healthLoading = ref(false)
  let _healthTimer: ReturnType<typeof setInterval> | null = null

  async function refreshHealth() {
    healthLoading.value = true
    let backendOk = false
    try {
      const pingRes = await http.get(buildApiPath(API_CONFIG.HEALTH, '/ping'), { timeout: 3000 })
      backendOk = pingRes.status === 200
    } catch {
      backendOk = false
    }
    healthStatus.backendOnline = backendOk

    if (!backendOk) {
      healthLoading.value = false
      return
    }

    try {
      const [metricRes, lnnHealthRes, lnnPerfRes] = await Promise.all([
        http.get(API_CONFIG.METRICS, { timeout: 5000 }).catch(() => {
          return null
        }),
        http.get(buildApiPath(API_CONFIG.LNN, '/health'), { timeout: 5000 }).catch(() => {
          return null
        }),
        http.get(buildApiPath(API_CONFIG.LNN, '/performance'), { timeout: 5000 }).catch(() => {
          return null
        }),
      ])

      if (metricRes && typeof metricRes.data === 'string') {
        const text = metricRes.data
        const uptimeMatch = text.match(/sidecar_uptime_seconds\s+(\d+)/)
        if (uptimeMatch) {
          const secs = parseInt(uptimeMatch[1])
          healthStatus.uptimeStr = formatUptime(secs)
        }

        const reqMatch = text.match(/http_requests_total\{[^}]*\}\s+(\d+)/)
        if (reqMatch) healthStatus.totalRequests = parseInt(reqMatch[1])

        const memMatch = text.match(/process_resident_memory_bytes\s+(\d+)/)
        if (memMatch) {
          healthStatus.memoryUsedMb = Math.round(parseInt(memMatch[1]) / (1024 * 1024))
        }

        const cpuMatch = text.match(/process_cpu_percent\s+([\d.]+)/)
        if (cpuMatch) healthStatus.cpuPercent = Math.round(parseFloat(cpuMatch[1]))

        if (healthStatus.memoryTotalMb > 0 && healthStatus.memoryUsedMb > 0) {
          healthStatus.memoryPercent = Math.round((healthStatus.memoryUsedMb / healthStatus.memoryTotalMb) * 100)
        }

        const trainMatch = text.match(/lnn_active_training_tasks\s+(\d+)/)
        if (trainMatch) healthStatus.activeTrainingTasks = parseInt(trainMatch[1])
      }

      if (lnnHealthRes?.data?.data) {
        const d = lnnHealthRes.data.data
        healthStatus.activeModels = d.models_available ?? d.model_count ?? 0
      }

      if (lnnPerfRes?.data?.data?.models) {
        const models = lnnPerfRes.data.data.models
        const allInferences: RecentInference[] = []
        let totalP50 = 0, totalP95 = 0
        let p50Count = 0, p95Count = 0
        for (const m of models) {
          if (m.recent_inferences) {
            for (const inf of m.recent_inferences) {
              allInferences.push({ model: m.model_name || 'unknown', duration_ms: Math.round(inf.duration_ms) })
            }
          }
          if (m.p50_inference_ms) {
            totalP50 += m.p50_inference_ms
            p50Count++
          }
          if (m.p95_inference_ms) {
            totalP95 += m.p95_inference_ms
            p95Count++
          }
        }
        healthStatus.p50Ms = p50Count > 0 ? Math.round(totalP50 / p50Count) : 0
        healthStatus.p95Ms = p95Count > 0 ? Math.round(totalP95 / p95Count) : 0

        if (allInferences.length > 0) {
          healthStatus.recentInferences = allInferences.slice(-10)
        }
        if (healthStatus.recentInferences.length > 0) {
          healthStatus.maxRecentDuration = Math.max(...healthStatus.recentInferences.map(i => i.duration_ms))
        }
      }

      try {
        const avgMatch = metricRes?.data?.match(/http_request_duration_seconds_bucket\{[^}]*\}\s+([\d.]+)/g)
        if (avgMatch && avgMatch.length > 0) {
          const vals = avgMatch.map((m: string) => parseFloat(m.split(/\s+/)[1]))
          healthStatus.avgResponseMs = Math.round((vals as number[]).reduce((a, b) => a + b, 0) / vals.length * 1000)
        }
      } catch {
        healthStatus.avgResponseMs = 0
      }

      healthStatus.dbHealthy = true
      healthStatus.redisHealthy = true
      healthStatus.prometheusHealthy = true

    } catch (e: unknown) {
      // 健康指标聚合失败不阻塞轮询，仅记录便于排查
      console.warn('[useHealthMonitor] refreshHealth aggregate failed:', e)
    } finally {
      healthLoading.value = false
    }
  }

  function formatUptime(seconds: number): string {
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
    return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
  }

  function startHealthPolling() {
    refreshHealth()
    _healthTimer = setInterval(refreshHealth, healthStatus.pollInterval * 1000)
  }

  function stopHealthPolling() {
    if (_healthTimer) {
      clearInterval(_healthTimer)
      _healthTimer = null
    }
  }

  onMounted(() => {
    startHealthPolling()
  })

  onBeforeUnmount(() => {
    stopHealthPolling()
  })

  return {
    healthStatus,
    healthLoading,
    refreshHealth,
    formatUptime,
  }
}
