<template>
  <div class="learnings-panel">
    <!-- 工具栏 -->
    <div class="learnings-toolbar">
      <div class="learnings-toolbar__title">
        <h3>{{ t("flywheel.learningsTitle") }}</h3>
        <span class="learnings-toolbar__subtitle">{{
          t("flywheel.learningsSubtitle")
        }}</span>
      </div>
      <div class="learnings-toolbar__actions">
        <el-select
          :model-value="days"
          size="small"
          style="width: 110px"
          @change="handleDaysChange"
        >
          <el-option :label="t('flywheel.learningsDays7')" :value="7" />
          <el-option :label="t('flywheel.learningsDays14')" :value="14" />
          <el-option :label="t('flywheel.learningsDays30')" :value="30" />
        </el-select>
        <el-button
          size="small"
          :icon="Refresh"
          :loading="loading"
          @click="fetchLearnings"
        >
          {{ t("flywheel.btnRefresh") }}
        </el-button>
      </div>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="!loading && events.length === 0"
      :description="t('flywheel.learningsEmpty')"
    />

    <!-- 事件时间线 -->
    <el-timeline v-else class="learnings-timeline">
      <el-timeline-item
        v-for="(event, index) in events"
        :key="`${event.type}-${event.rule_id}-${event.operated_at}-${index}`"
        :timestamp="formatTime(event.operated_at)"
        :type="tagType(event.type)"
        placement="top"
      >
        <div class="learning-card">
          <div class="learning-card__head">
            <el-tag :type="tagType(event.type)" size="small" effect="light">
              {{ eventLabel(event) }}
            </el-tag>
            <span class="learning-card__rule-id">{{ event.rule_id }}</span>
          </div>
          <div
            v-if="event.type === 'draft_created' && event.description"
            class="learning-card__desc"
          >
            {{ event.description }}
          </div>
          <div
            v-if="event.type === 'draft_created' && event.confidence != null"
            class="learning-card__meta"
          >
            {{ t("flywheel.learningsConfidence") }}:
            {{ (event.confidence * 100).toFixed(0) }}%
          </div>
          <div
            v-if="
              event.type !== 'draft_created' &&
              (event.from_stage || event.to_stage)
            "
            class="learning-card__meta"
          >
            {{ formatStage(event.from_stage) }}
            →
            {{ formatStage(event.to_stage) }}
          </div>
          <div v-if="event.reason" class="learning-card__reason">
            {{ event.reason }}
          </div>
        </div>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { Refresh } from "@element-plus/icons-vue";
import {
  getDreamingLearnings,
  type DreamingLearningEvent,
} from "@/api/dreaming";

const { t, te } = useI18n();

const loading = ref(false);
const days = ref(7);
const events = ref<DreamingLearningEvent[]>([]);

// 事件类型 → 文案键 / 标签色
const EVENT_LABEL_KEYS: Record<string, string> = {
  draft_created: "flywheel.learningsEventDraft",
  stage_publish: "flywheel.learningsEventPublish",
  stage_promote: "flywheel.learningsEventPromote",
  stage_demote: "flywheel.learningsEventDemote",
  stage_auto_demote_on_apply_failure: "flywheel.learningsEventAutoDemote",
};
const EVENT_TAG_TYPES: Record<
  string,
  "success" | "info" | "warning" | "danger" | "primary"
> = {
  draft_created: "success",
  stage_publish: "primary",
  stage_promote: "success",
  stage_demote: "warning",
  stage_auto_demote_on_apply_failure: "danger",
};

function eventLabel(event: DreamingLearningEvent): string {
  const key = EVENT_LABEL_KEYS[event.type];
  if (key && te(key)) return t(key);
  return event.type;
}

function tagType(
  type: string,
): "success" | "info" | "warning" | "danger" | "primary" {
  return EVENT_TAG_TYPES[type] ?? "info";
}

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatStage(stage: string | null | undefined): string {
  if (!stage) return "-";
  const key = `flywheel.learningsStage.${stage}`;
  return te(key) ? t(key) : stage;
}

async function fetchLearnings(): Promise<void> {
  loading.value = true;
  try {
    const resp = await getDreamingLearnings(days.value);
    if (resp.code === 0 && resp.data) {
      events.value = resp.data.events ?? [];
    }
  } catch (e) {
    console.warn("[FlywheelLearnings] fetch failed:", e);
    events.value = [];
  } finally {
    loading.value = false;
  }
}

async function handleDaysChange(value: number): Promise<void> {
  days.value = value;
  await fetchLearnings();
}

onMounted(() => {
  void fetchLearnings();
});
</script>

<style scoped>
.learnings-panel {
  padding: 8px 4px;
}

.learnings-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.learnings-toolbar__title h3 {
  margin: 0 0 4px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.learnings-toolbar__subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.learnings-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.learnings-timeline {
  padding-left: 4px;
}

.learning-card {
  padding: 2px 0;
}

.learning-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.learning-card__rule-id {
  font-family: monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.learning-card__desc {
  font-size: 13px;
  color: var(--el-text-color-primary);
  margin-bottom: 2px;
}

.learning-card__meta {
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.learning-card__reason {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
</style>
