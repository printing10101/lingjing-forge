// FEM 有限元求解逻辑（从 Simulation.vue 拆出，V1）
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import { useI18n } from 'vue-i18n'
import type { FEMResult } from './types'

export interface FemParams {
  material: string
  elasticModulus: number
  poissonRatio: number
  density: number
  yieldStrength: number
  thermalConductivity: number
  meshType: string
  elementSize: number
  adaptiveRefinement: boolean
}

function defaultFemParams(): FemParams {
  return {
    material: 'steel45',
    elasticModulus: 210.0,
    poissonRatio: 0.3,
    density: 7850.0,
    yieldStrength: 355.0,
    thermalConductivity: 50.0,
    meshType: 'tetrahedral',
    elementSize: 2.0,
    adaptiveRefinement: true,
  }
}

export function useFemSolver() {
  const { t } = useI18n()
  const femParams = ref<FemParams>(defaultFemParams())
  const femResult = ref<FEMResult | null>(null)
  const femSolving = ref(false)

  function resetFemParams() {
    femParams.value = defaultFemParams()
  }

  async function handleStartSolve() {
    femSolving.value = true
    femResult.value = null
    try {
      const res = await http.post(buildApiPath(API_CONFIG.SIMULATION, '/fem/solve'), {
        material: femParams.value.material,
        elastic_modulus: femParams.value.elasticModulus,
        poisson_ratio: femParams.value.poissonRatio,
        density: femParams.value.density,
        yield_strength: femParams.value.yieldStrength,
        mesh_type: femParams.value.meshType,
        element_size: femParams.value.elementSize,
        adaptive_refinement: femParams.value.adaptiveRefinement,
        beam_length: 100.0,
        beam_width: 20.0,
        beam_height: 20.0,
        load_force: 5000.0,
      })
      if (res.data.code === 0 && res.data.data) {
        femResult.value = res.data.data
        ElMessage.success(t('simulationPage.msgFemDone'))
      } else {
        ElMessage.error(res.data.message || t('simulationPage.msgFemFailed'))
      }
    } catch (e: unknown) {
      console.warn('[Simulation] FEM solve failed:', e)
      ElMessage.error(t('simulationPage.msgFemFailed'))
    } finally {
      femSolving.value = false
    }
  }

  return {
    femParams,
    resetFemParams,
    femResult,
    femSolving,
    handleStartSolve,
  }
}
