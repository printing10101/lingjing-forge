<template>
  <div v-if="entries.length" class="param-slider-panel">
    <div class="param-slider-panel__header">
      <span class="param-slider-panel__title">{{
        t("nlModeling.sliderTitle")
      }}</span>
      <span v-if="regenerating" class="param-slider-panel__status">{{
        t("nlModeling.sliderRegenerating")
      }}</span>
    </div>
    <div
      v-for="entry in entries"
      :key="entry.name"
      class="param-slider-panel__row"
    >
      <span class="param-slider-panel__label" :title="entry.name">{{
        entry.label
      }}</span>
      <el-slider
        v-model="entry.value"
        :min="entry.min"
        :max="entry.max"
        :step="entry.step"
        :disabled="regenerating"
        size="small"
        @change="scheduleRegenerate"
      />
      <span class="param-slider-panel__value"
        >{{ entry.value.toFixed(1) }} mm</span
      >
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 参数滑杆面板（参数化直调）：把 /generate 返回的脚本参数表渲染为滑杆，
 * 拖动后调用 POST /nl2cad/regenerate-params 免 LLM 重执行，亚秒级换模型。
 * 脚本无参数（parameters 为空）时整体不渲染——诚实降级，不造假滑杆。
 */
import { ref, watch, onBeforeUnmount } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElSlider } from "element-plus";
import { regenerateModelParams } from "@/api/nl2cad";

const props = defineProps<{
  script: string;
  parameters: Record<string, number>;
}>();

const emit = defineEmits<{
  (e: "regenerated", modelPath: string, params: Record<string, number>): void;
}>();

const { t } = useI18n();

interface SliderEntry {
  name: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
}

const entries = ref<SliderEntry[]>([]);
const regenerating = ref(false);
let regenTimer: ReturnType<typeof setTimeout> | null = null;
// 请求 in-flight 期间用户又拖了滑杆 → 置脏，本次请求结束后重新排队，
// 否则该次变更被静默丢弃，滑杆显示值与后端模型从此不一致
let dirty = false;

// 滑杆范围按当前值推导（±50%，0.5mm 步进），脚本变化时重建
watch(
  () => [props.script, props.parameters],
  () => {
    entries.value = Object.entries(props.parameters || {})
      .filter(([, v]) => Number.isFinite(v) && v > 0)
      .map(([name, v]) => ({
        name,
        label: name.replace(/_/g, " "),
        value: v,
        min: round2(Math.max(0.5, v / 2)),
        max: round2(v * 1.5),
        step: 0.5,
      }));
  },
  { immediate: true },
);

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function scheduleRegenerate() {
  if (!props.script || entries.value.length === 0) return;
  dirty = true;
  if (regenTimer) clearTimeout(regenTimer);
  regenTimer = setTimeout(runRegenerate, 400);
}

async function runRegenerate() {
  if (regenerating.value) return;
  dirty = false;
  regenerating.value = true;
  try {
    const params = Object.fromEntries(
      entries.value.map((e) => [e.name, e.value]),
    );
    const data = await regenerateModelParams({ script: props.script, params });
    emit("regenerated", data.model_path, data.params);
  } catch (error) {
    console.error("Regenerate with params failed:", error);
    ElMessage.error(t("nlModeling.sliderRegenFailed"));
  } finally {
    regenerating.value = false;
    if (dirty) scheduleRegenerate();
  }
}

onBeforeUnmount(() => {
  if (regenTimer) clearTimeout(regenTimer);
});
</script>

<style scoped>
.param-slider-panel {
  padding: 12px 20px;
  border-bottom: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.param-slider-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.param-slider-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.param-slider-panel__status {
  font-size: 12px;
  color: var(--text-tertiary);
}

.param-slider-panel__row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.param-slider-panel__label {
  width: 96px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.param-slider-panel__row .el-slider {
  flex: 1;
}

.param-slider-panel__value {
  width: 72px;
  flex-shrink: 0;
  text-align: right;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}
</style>
