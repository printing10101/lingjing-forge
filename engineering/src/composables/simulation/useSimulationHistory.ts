// 仿真历史与统计（从 Simulation.vue 拆出，V1）
import { computed, ref } from 'vue'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import type { HistoryItem } from './types'

export function useSimulationHistory() {
  const historyItems = ref<HistoryItem[]>([])
  const historyLoading = ref(false)

  async function fetchHistory() {
    historyLoading.value = true
    try {
      const res = await http.get(buildApiPath(API_CONFIG.SIMULATION, '/history'), {
        params: { limit: 20 },
      })
      const data = res.data?.data ?? res.data
      historyItems.value = data?.items ?? []
    } catch {
      historyItems.value = []
    } finally {
      historyLoading.value = false
    }
  }

  const passCount = computed(() => historyItems.value.filter((h) => !h.collision_collided).length)
  const failCount = computed(() => historyItems.value.filter((h) => h.collision_collided).length)
  const avgDuration = computed(() => {
    const items = historyItems.value
    if (items.length === 0) return '--'
    const total = items.reduce((sum, h) => sum + (h.duration_seconds ?? 0), 0)
    return (total / items.length).toFixed(1) + 's'
  })

  return {
    historyItems,
    historyLoading,
    fetchHistory,
    passCount,
    failCount,
    avgDuration,
  }
}
