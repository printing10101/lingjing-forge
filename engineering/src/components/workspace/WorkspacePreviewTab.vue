<template>
  <div class="preview-tab">
    <!-- 输入区 -->
    <el-form label-width="130px">
      <el-form-item :label="t('workspace.previewCurrentState')">
        <el-input
          v-model="stateInput"
          type="textarea"
          :rows="3"
          class="mono-input"
        />
      </el-form-item>
      <el-form-item :label="t('workspace.previewCandidateAction')">
        <el-input
          v-model="actionInput"
          type="textarea"
          :rows="3"
          class="mono-input"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="handlePreview">
          {{ t("workspace.previewBtn") }}
        </el-button>
        <span class="preview-hint">{{ t("workspace.previewHint") }}</span>
      </el-form-item>
    </el-form>

    <!-- 错误提示 -->
    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      show-icon
      :closable="true"
      class="preview-error"
      @close="errorMsg = null"
    />

    <!-- 预演卡 -->
    <div
      v-if="preview"
      class="preview-card"
      :class="`preview-card--${preview.verdict}`"
    >
      <div class="preview-card__verdict">
        <el-tag :type="verdictTagType" size="large" effect="dark">
          {{ verdictLabel }}
        </el-tag>
        <span v-if="preview.conservatism.scaled" class="preview-card__damped">
          {{
            t("workspace.previewDamped", {
              factor: preview.conservatism.factor,
            })
          }}
        </span>
      </div>

      <ul v-if="preview.reasons.length" class="preview-card__reasons">
        <li v-for="reason in preview.reasons" :key="reason">
          {{ reason }}
        </li>
      </ul>

      <!-- 指标 vs 阈值带 -->
      <el-descriptions
        :column="2"
        border
        size="small"
        class="preview-card__metrics"
      >
        <el-descriptions-item :label="t('workspace.previewMaxChatter')">
          {{ fmt(preview.metrics.max_chatter_probability) }}
          <span class="band-hint">
            ({{ t("workspace.previewBandSafe") }} &lt;
            {{ preview.bands.chatter_probability.safe_below }} /
            {{ t("workspace.previewBandDanger") }} ≥
            {{ preview.bands.chatter_probability.danger_at }})
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('workspace.previewCumulativeWear')">
          {{ fmt(preview.metrics.cumulative_tool_wear) }} mm
          <span class="band-hint"
            >({{ t("workspace.previewBandDanger") }} ≥
            {{ preview.bands.tool_wear.danger_at }})</span
          >
        </el-descriptions-item>
        <el-descriptions-item :label="t('workspace.previewRoughness')">
          {{ fmt(preview.metrics.final_surface_roughness) }} μm
        </el-descriptions-item>
        <el-descriptions-item :label="t('workspace.previewConfidence')">
          {{ fmt(preview.metrics.confidence_mean) }}
        </el-descriptions-item>
      </el-descriptions>

      <!-- 安全盾 -->
      <div class="preview-card__shield">
        <el-tag
          :type="preview.shield.violated ? 'danger' : 'success'"
          size="small"
        >
          {{
            preview.shield.violated
              ? t("workspace.previewShieldBlocked")
              : t("workspace.previewShieldPassed")
          }}
        </el-tag>
        <span
          v-for="v in preview.shield.violations"
          :key="v"
          class="shield-violation"
          >{{ v }}</span
        >
      </div>

      <!-- 最终动作 + 主权 -->
      <div class="preview-card__footer">
        <span class="footer-label"
          >{{ t("workspace.previewFinalAction") }}:</span
        >
        <code>{{ JSON.stringify(preview.final_action) }}</code>
      </div>
      <el-alert
        :title="
          preview.sovereignty.requires_confirmation
            ? t('workspace.previewSovereigntyConfirm', {
                level: preview.sovereignty.autonomy_label,
              })
            : t('workspace.previewSovereigntyAuto', {
                level: preview.sovereignty.autonomy_label,
              })
        "
        :type="preview.sovereignty.requires_confirmation ? 'info' : 'success'"
        :closable="false"
        class="preview-card__sovereignty"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { getPhysicalPreview, type PhysicalPreview } from "@/api/worldModel";

const { t } = useI18n();

const loading = ref(false);
const errorMsg = ref<string | null>(null);
const preview = ref<PhysicalPreview | null>(null);

const DEFAULT_STATE = JSON.stringify(
  { spindle_speed: 6000, feed_rate: 800, depth_of_cut: 1.5 },
  null,
  1,
);
const DEFAULT_ACTION = JSON.stringify(
  {
    spindle_speed_delta: -0.1,
    feed_rate_delta: 0.05,
    depth_of_cut_delta: 0,
    width_of_cut_delta: 0,
  },
  null,
  1,
);

const stateInput = ref(DEFAULT_STATE);
const actionInput = ref(DEFAULT_ACTION);

const verdictTagType = computed(
  () =>
    (preview.value?.verdict === "safe"
      ? "success"
      : preview.value?.verdict === "warning"
        ? "warning"
        : "danger") as "success" | "warning" | "danger",
);
const verdictLabel = computed(() => {
  const v = preview.value?.verdict;
  if (v === "safe") return t("workspace.previewVerdictSafe");
  if (v === "warning") return t("workspace.previewVerdictWarning");
  return t("workspace.previewVerdictDanger");
});

function fmt(value: number | undefined): string {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

function parseJson(input: string, label: string): Record<string, number> {
  try {
    const parsed = JSON.parse(input);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      throw new Error("not an object");
    }
    return parsed as Record<string, number>;
  } catch {
    throw new Error(t("workspace.previewInvalidJson", { field: label }));
  }
}

async function handlePreview(): Promise<void> {
  errorMsg.value = null;
  let currentState: Record<string, number>;
  let candidateAction: Record<string, number>;
  try {
    currentState = parseJson(
      stateInput.value,
      t("workspace.previewCurrentState"),
    );
    candidateAction = parseJson(
      actionInput.value,
      t("workspace.previewCandidateAction"),
    );
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e);
    return;
  }
  loading.value = true;
  try {
    const resp = await getPhysicalPreview({
      current_state: currentState,
      candidate_action: candidateAction,
    });
    if (resp.code === 0 && resp.data) {
      preview.value = resp.data as PhysicalPreview;
    } else {
      errorMsg.value = resp.message || t("workspace.previewFailed");
    }
  } catch (e) {
    errorMsg.value =
      e instanceof Error ? e.message : t("workspace.previewFailed");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.preview-tab {
  max-width: 760px;
}

.mono-input :deep(textarea) {
  font-family: monospace;
  font-size: 12px;
}

.preview-hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.preview-error {
  margin-bottom: 12px;
}

.preview-card {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 16px;
}

.preview-card--safe {
  border-left: 4px solid var(--el-color-success);
}

.preview-card--warning {
  border-left: 4px solid var(--el-color-warning);
}

.preview-card--danger {
  border-left: 4px solid var(--el-color-danger);
}

.preview-card__verdict {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.preview-card__damped {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.preview-card__reasons {
  margin: 0 0 12px 4px;
  padding-left: 16px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.preview-card__metrics {
  margin-bottom: 12px;
}

.band-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.preview-card__shield {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.shield-violation {
  font-size: 12px;
  color: var(--el-color-danger);
}

.preview-card__footer {
  font-size: 12px;
  margin-bottom: 8px;
}

.footer-label {
  color: var(--el-text-color-secondary);
  margin-right: 6px;
}

.preview-card__sovereignty {
  margin-top: 4px;
}
</style>
