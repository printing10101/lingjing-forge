<template>
  <div class="tab-panel">
    <div class="fem-section">
      <h3 class="section-title">
        {{ t('simulationPage.femSectionTitle') }}
      </h3>
      <div class="fem-params-grid">
        <div class="param-card">
          <h4 class="param-card-title">
            {{ t('simulationPage.femMaterialTitle') }}
          </h4>
          <el-form
            label-position="top"
            class="param-form"
          >
            <el-form-item :label="t('simulationPage.femMaterialName')">
              <el-select
                :model-value="femParams.material"
                :placeholder="t('simulationPage.femMaterialPlaceholder')"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('material', $event)"
              >
                <el-option
                  :label="t('simulationPage.femSteel45')"
                  value="steel45"
                />
                <el-option
                  :label="t('simulationPage.femAl6061')"
                  value="al6061"
                />
                <el-option
                  :label="t('simulationPage.femSs304')"
                  value="ss304"
                />
                <el-option
                  :label="t('simulationPage.femTi6Al4V')"
                  value="ti6al4v"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('simulationPage.femElasticModulus')">
              <el-input-number
                :model-value="femParams.elasticModulus"
                :min="0.01"
                :precision="1"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('elasticModulus', $event)"
              />
            </el-form-item>
            <el-form-item :label="t('simulationPage.femPoissonRatio')">
              <el-input-number
                :model-value="femParams.poissonRatio"
                :min="0"
                :max="0.5"
                :step="0.01"
                :precision="3"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('poissonRatio', $event)"
              />
            </el-form-item>
            <el-form-item :label="t('simulationPage.femDensity')">
              <el-input-number
                :model-value="femParams.density"
                :min="0.01"
                :step="10"
                :precision="1"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('density', $event)"
              />
            </el-form-item>
            <el-form-item :label="t('simulationPage.femYieldStrength')">
              <el-input-number
                :model-value="femParams.yieldStrength"
                :min="0.01"
                :precision="1"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('yieldStrength', $event)"
              />
            </el-form-item>
            <el-form-item :label="t('simulationPage.femThermalConductivity')">
              <el-input-number
                :model-value="femParams.thermalConductivity"
                :min="0.01"
                :precision="2"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('thermalConductivity', $event)"
              />
            </el-form-item>
          </el-form>
        </div>
        <div class="param-card">
          <h4 class="param-card-title">
            {{ t('simulationPage.femMeshTitle') }}
          </h4>
          <el-form
            label-position="top"
            class="param-form"
          >
            <el-form-item :label="t('simulationPage.femMeshType')">
              <el-select
                :model-value="femParams.meshType"
                :placeholder="t('simulationPage.femMeshTypePlaceholder')"
                size="small"
                style="width: 100%"
                @update:model-value="updateFemParam('meshType', $event)"
              >
                <el-option
                  :label="t('simulationPage.femMeshTetra')"
                  value="tetrahedral"
                />
                <el-option
                  :label="t('simulationPage.femMeshHex')"
                  value="hexahedral"
                />
                <el-option
                  :label="t('simulationPage.femMeshHybrid')"
                  value="hybrid"
                />
              </el-select>
            </el-form-item>
            <el-form-item :label="t('simulationPage.femElementSize')">
              <el-slider
                :model-value="femParams.elementSize"
                :min="0.1"
                :max="10"
                :step="0.1"
                :show-tooltip="true"
                @update:model-value="updateFemParam('elementSize', $event)"
              />
              <div class="slider-labels">
                <span>0.1 mm</span>
                <span>10 mm</span>
              </div>
            </el-form-item>
            <el-form-item :label="t('simulationPage.femMeshCount')">
              <el-input
                :model-value="estimatedMeshCount"
                readonly
                size="small"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item :label="t('simulationPage.femAdaptive')">
              <el-switch
                :model-value="femParams.adaptiveRefinement"
                @update:model-value="updateFemParam('adaptiveRefinement', $event)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="fem-actions">
        <el-button
          type="primary"
          size="small"
          @click="emit('solve')"
        >
          {{ t('simulationPage.femStartSolve') }}
        </el-button>
        <el-button
          size="small"
          @click="emit('reset')"
        >
          {{ t('simulationPage.femResetParams') }}
        </el-button>
      </div>
    </div>
    <div class="results-section">
      <div class="result-viewport-wrap">
        <div
          class="result-viewport"
          v-loading="femSolving"
        >
          <template v-if="femResult">
            <div class="fem-result-summary">
              <div class="fem-result-row">
                <span class="fem-result-label">{{ t('simulationPage.femResultMaxStress') }}</span>
                <span class="fem-result-value">{{ femResult.max_stress }} MPa</span>
              </div>
              <div class="fem-result-row">
                <span class="fem-result-label">{{ t('simulationPage.femResultDeflection') }}</span>
                <span class="fem-result-value">{{ femResult.max_deflection }} mm</span>
              </div>
              <div class="fem-result-row">
                <span class="fem-result-label">{{ t('simulationPage.femResultSafety') }}</span>
                <el-tag
                  :type="femResult.safety_factor >= 1.5 ? 'success' : femResult.safety_factor >= 1 ? 'warning' : 'danger'"
                  size="small"
                  effect="light"
                >
                  {{ femResult.safety_factor }}
                </el-tag>
              </div>
              <div class="fem-result-row">
                <span class="fem-result-label">{{ t('simulationPage.femResultNodes') }}</span>
                <span class="fem-result-value">{{ femResult.nodes }}</span>
              </div>
              <p
                v-if="femResult.warning"
                class="fem-result-warning"
              >
                {{ femResult.warning }}
              </p>
            </div>
          </template>
          <div
            v-else
            class="result-placeholder"
          >
            <span>{{ t('simulationPage.femResultPlaceholder') }}</span>
          </div>
          <div class="color-legend">
            <div class="legend-bar" />
            <div class="legend-labels">
              <span>Max</span>
              <span>Min</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// ─── Types ──────────────────────────────────────────────

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

export interface FemResult {
  material: string
  max_stress: number
  max_deflection: number
  yield_strength: number
  safety_factor: number
  nodes: number
  status: string
  warning?: string
  stress_distribution?: Array<{ x: number; stress: number }>
}

// ─── Props ──────────────────────────────────────────────

const props = defineProps<{
  femParams: FemParams
  femResult: FemResult | null
  femSolving: boolean
}>()

const emit = defineEmits<{
  'update:femParams': [value: FemParams]
  'solve': []
  'reset': []
}>()

// ─── Estimated Mesh Count ───────────────────────────────

const estimatedMeshCount = computed(() => {
  const base = 50000
  const factor = (10 - props.femParams.elementSize) ** 2
  return t('simulationPage.meshCountUnit', { count: Math.round(base * factor * 0.8).toLocaleString() })
})

// ─── Update Fem Param ───────────────────────────────────

function updateFemParam(key: string, value: unknown) {
  if (value === undefined) return
  // el-slider can emit number[] for range, but we use single value
  const finalValue = Array.isArray(value) ? value[0] : value
  emit('update:femParams', { ...props.femParams, [key]: finalValue })
}
</script>

<style scoped>
/* ─── Tab Panel ────────────────────────────────────────── */
.tab-panel {
  animation: fadeIn 0.25s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ─── Section Title ──────────────────────────────────── */
.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 16px;
}

/* ─── FEM Section ──────────────────────────────────────── */
.fem-section {
  margin-bottom: 32px;
}

.fem-params-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

@media (max-width: 768px) {
  .fem-params-grid {
    grid-template-columns: 1fr;
  }
}

.param-card {
  background: var(--bg-100);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-200);
  padding: 20px 24px;
}

.param-card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 16px;
}

:deep(.param-form .el-form-item__label) {
  color: var(--text-secondary);
  font-size: 13px;
}

.fem-actions {
  display: flex;
  gap: 12px;
  margin-top: 20px;
}

.slider-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

/* Results Section */
.results-section {
  margin-top: 32px;
}

.result-viewport-wrap {
  position: relative;
}

.result-viewport {
  aspect-ratio: 16 / 9;
  min-height: 360px;
  background: var(--bg-100);
  border-radius: var(--radius-lg);
  border: 1px solid var(--bg-200);
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.result-placeholder {
  color: var(--text-tertiary);
  font-size: 15px;
}

.color-legend {
  position: absolute;
  right: 20px;
  top: 20px;
  bottom: 20px;
  width: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.legend-bar {
  flex: 1;
  width: 14px;
  border-radius: var(--radius-md);
  background: var(--gradient-heatmap);
  border: 1px solid var(--bg-200);
}

.legend-labels {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-tertiary);
  margin-top: 4px;
  height: 100%;
  position: absolute;
  left: 24px;
  top: 0;
}

.fem-result-summary {
  width: 100%;
  padding: 20px;
  text-align: left;
}

.fem-result-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-color, rgba(0, 0, 0, 0.06));
}

.fem-result-label {
  color: var(--text-secondary);
  font-size: 13px;
}

.fem-result-value {
  font-weight: 600;
  font-size: 14px;
}

.fem-result-warning {
  margin-top: 12px;
  font-size: 12px;
  color: var(--warning);
  line-height: 1.5;
}
</style>