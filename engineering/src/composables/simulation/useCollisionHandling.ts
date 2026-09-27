// 碰撞处理逻辑（从 Simulation.vue 拆出，V1）
import { computed, ref, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import type { CollisionInfo, SimResultData } from './types'

export function useCollisionHandling(simResult: Ref<SimResultData | null>) {
  const { t } = useI18n()
  // 已忽略的碰撞索引集合
  const dismissedCollisions = ref<Set<number>>(new Set())

  const collisionList = computed<CollisionInfo[]>(() => {
    if (!simResult.value?.collision_detected || !simResult.value.collision_details) return []
    const details = simResult.value.collision_details
    return details.positions
      .map((pos, idx) => ({
        position: pos as [number, number, number],
        severity: (details.severity === 'critical' ? 'critical' : 'warning') as CollisionInfo['severity'],
        toolSegment: details.segment_indices[idx] ?? idx,
        description: t('simulationPage.msgCollisionDesc', { idx: idx + 1, severity: details.severity }),
      }))
      .filter((_, idx) => !dismissedCollisions.value.has(idx))
  })

  function handleLocateCollision(index: number) {
    const collision = collisionList.value[index]
    if (collision) {
      // Future: highlight collision point in 3D viewer
      ElMessage.info(t('simulationPage.msgLocateCollision', { index: index + 1, pos: collision.position.map((v) => v.toFixed(2)).join(', ') }))
    }
  }

  function handleDismissCollision(index?: number) {
    if (index === undefined) {
      dismissedCollisions.value = new Set()
      return
    }
    dismissedCollisions.value = new Set(dismissedCollisions.value).add(index)
  }

  function handleDismissAllCollisions() {
    const details = simResult.value?.collision_details
    if (details) {
      details.positions.forEach((_, idx) => {
        dismissedCollisions.value = new Set(dismissedCollisions.value).add(idx)
      })
    }
    ElMessage.success(t('simulationPage.msgAllCollisionsDismissed'))
  }

  return {
    collisionList,
    dismissedCollisions,
    handleLocateCollision,
    handleDismissCollision,
    handleDismissAllCollisions,
  }
}
