<template>
  <main class="knowledge-review">
    <header class="page-heading">
      <div>
        <h1>工艺知识审核</h1>
        <p>核对来源、参数单位与适用条件，保留每次审核和修订的依据。</p>
      </div>
      <div class="actions">
        <button :disabled="busy" @click="reload">重新加载</button>
        <button :disabled="busy || !canReview" @click="startCreate">
          新增待审资料
        </button>
      </div>
    </header>
    <p class="scope-note">
      知识批准用于条件明确的方案推荐；实际加工仍需确认刀具、现场工况和物理校验结果。
    </p>
    <p v-if="!canReview" role="alert">
      需要已登录的工程师或管理员身份，请核对当前账号。
    </p>
    <p v-if="error || validationError" class="error" role="alert">
      {{ validationError || error }}
    </p>
    <p v-if="notice" role="status" class="notice">
      {{ notice }}
    </p>
    <p v-if="loading" role="status">正在读取知识记录…</p>
    <div v-if="canReview" class="review-layout">
      <section class="record-list" aria-label="知识记录">
        <label
          >查找资料<input v-model="search" placeholder="材料、刀具、特征或来源"
        /></label>
        <label
          >审核状态<select v-model="filter">
            <option value="all">全部状态</option>
            <option
              v-for="(label, value) in statusLabels"
              :key="value"
              :value="value"
            >
              {{ label }}
            </option>
          </select></label
        >
        <p>{{ filteredRecords.length }} 条资料</p>
        <p v-if="!loading && !records.length">
          尚无工艺知识记录。可新增待审资料，完整填写后再审核。
        </p>
        <p v-else-if="!loading && !filteredRecords.length">
          没有符合条件的资料。
        </p>
        <button
          v-for="record in filteredRecords"
          :key="record.record_id"
          class="record-card"
          :class="{ selected: selectedId === record.record_id }"
          :disabled="busy"
          :aria-pressed="selectedId === record.record_id"
          @click="selectRecord(record)"
        >
          <strong>{{ record.material }} · {{ record.feature }}</strong>
          <span>{{ record.process }} / {{ record.tool }}</span>
          <span
            >{{ statusLabels[record.review.status] || "状态待核对" }} · 版本
            {{ record.revision }}</span
          >
        </button>
      </section>
      <section
        v-if="selected || creating"
        class="review-detail"
        aria-label="审核详情"
      >
        <h2>
          {{
            creating ? "新增待审资料" : `资料详情 · 版本 ${selected?.revision}`
          }}
        </h2>
        <p v-if="selected">
          当前状态：{{
            statusLabels[selected.review.status] || "状态待核对"
          }}。来源标识：{{ selected.source }}
        </p>
        <p v-if="selected?.candidate_context?.note">
          资料说明：{{ selected.candidate_context.note }}
        </p>
        <form @submit.prevent="submit">
          <fieldset :disabled="busy || stale">
            <label v-if="!creating"
              >本次操作<select v-model="decision" data-testid="decision">
                <option
                  v-if="selected?.review.status === 'pending'"
                  value="approved"
                >
                  批准用于条件明确的推荐
                </option>
                <option
                  v-if="selected?.review.status === 'pending'"
                  value="rejected"
                >
                  拒绝
                </option>
                <option
                  v-if="selected?.review.status === 'approved'"
                  value="revoked"
                >
                  撤销批准
                </option>
                <option value="revise">修订并重新待审</option>
              </select></label
            >
            <div class="form-grid">
              <label
                >加工特征<input
                  v-model="form.feature"
                  :readonly="!editing"
                  data-testid="feature"
                  placeholder="例如 hole"
              /></label>
              <label
                >工艺方法<input
                  v-model="form.process"
                  :readonly="!editing"
                  data-testid="process"
                  placeholder="例如 drill"
              /></label>
              <label
                >具体刀具<input
                  v-model="form.tool"
                  :readonly="!editing"
                  data-testid="tool"
              /></label>
              <label
                >明确材料<input
                  v-model="form.material"
                  :readonly="!editing"
                  data-testid="material"
              /></label>
              <label
                >来源标识<input
                  v-model="form.source"
                  :readonly="!editing"
                  data-testid="source"
              /></label>
            </div>
            <h3>参数与单位</h3>
            <div
              v-for="(parameter, index) in form.parameters"
              :key="index"
              class="parameter-row"
            >
              <label
                >参数名<input
                  v-model="parameter.name"
                  :readonly="!editing"
                  :data-testid="`parameter-name-${index}`"
              /></label>
              <label
                >数值<input
                  v-model="parameter.value"
                  :readonly="!editing"
                  :data-testid="`parameter-value-${index}`"
                  inputmode="decimal"
              /></label>
              <label
                >单位<input
                  v-model="parameter.unit"
                  :readonly="!editing && decision !== 'approved'"
                  :data-testid="`parameter-unit-${index}`"
                  placeholder="例如 rpm、mm/rev"
              /></label>
              <button
                v-if="editing"
                type="button"
                :disabled="form.parameters.length <= 1"
                @click="form.parameters.splice(index, 1)"
              >
                删除参数
              </button>
            </div>
            <button
              v-if="editing"
              type="button"
              @click="form.parameters.push({ name: '', value: '', unit: '' })"
            >
              添加参数
            </button>
            <p v-if="editing">
              新增和修订只保存为待审，批准时再核对来源及单位。
            </p>
            <section
              v-if="editing || decision === 'approved'"
              class="evidence-form"
              aria-label="来源依据"
            >
              <h3>来源与审核依据</h3>
              <label
                >来源链接<input
                  v-model="form.sourceUrl"
                  data-testid="source-url"
                  placeholder="https://…"
              /></label>
              <label
                >来源文档 SHA256 校验值<input
                  v-model="form.documentHash"
                  data-testid="document-hash"
              /></label>
              <label
                >来源页码<input
                  v-model="form.page"
                  data-testid="page"
                  inputmode="numeric"
              /></label>
              <label
                >适用条件<textarea
                  v-model="form.applicability"
                  data-testid="applicability"
                  placeholder="具体材料、刀具规格、加工方式及适用范围"
                />
              </label>
              <label
                >核验方法<textarea
                  v-model="form.verificationMethod"
                  data-testid="verification-method"
                  placeholder="如何核对原文、单位、材料及加工条件"
                />
              </label>
              <fieldset>
                <legend>钻孔适用范围（批准时必填）</legend>
                <p>
                  仅填写有来源且经核对的范围；孔径包含上下界，硬度上界需明确是否包含。暂不支持其他加工方式的自动适用性核验。
                </p>
                <label
                  >最低硬度 HRC<input
                    v-model="form.hardnessMin"
                    data-testid="hardness-min"
                    inputmode="decimal"
                /></label>
                <label
                  >最高硬度 HRC<input
                    v-model="form.hardnessMax"
                    data-testid="hardness-max"
                    inputmode="decimal"
                /></label>
                <label
                  >硬度上界<select
                    v-model="form.hardnessMaxInclusive"
                    data-testid="hardness-max-inclusive"
                  >
                    <option value="">请选择</option>
                    <option value="inclusive">包含（≤）</option>
                    <option value="exclusive">不包含（＜）</option>
                  </select></label
                >
                <label
                  >最小孔径 mm<input
                    v-model="form.diameterMin"
                    data-testid="diameter-min"
                    inputmode="decimal"
                /></label>
                <label
                  >最大孔径 mm<input
                    v-model="form.diameterMax"
                    data-testid="diameter-max"
                    inputmode="decimal"
                /></label>
                <label
                  >最大孔深／孔径比<input
                    v-model="form.depthRatio"
                    data-testid="depth-ratio"
                    inputmode="decimal"
                /></label>
                <label
                  >已核验冷却方式（可多选）<select
                    v-model="form.coolants"
                    multiple
                    data-testid="coolants"
                  >
                    <option value="dry">干式</option>
                    <option value="flood">浇注冷却</option>
                    <option value="mist">喷雾冷却</option>
                    <option value="through_tool">内冷</option>
                  </select></label
                >
                <label
                  >已核验装夹方式（可多选）<select
                    v-model="form.workholdings"
                    multiple
                    data-testid="workholdings"
                  >
                    <option value="vise">台钳</option>
                    <option value="chuck">卡盘</option>
                    <option value="fixture">专用夹具</option>
                    <option value="collet">夹头</option>
                  </select></label
                >
              </fieldset>
              <label
                >尚未解决的问题<textarea
                  v-model="form.unresolved"
                  data-testid="unresolved"
                  placeholder="仍有疑问时保留待审，不提交批准"
                />
              </label>
              <label v-if="!editing" class="check"
                ><input
                  v-model="form.resolvedConfirmed"
                  type="checkbox"
                  data-testid="resolved-confirmed"
                />已逐项核对，未解决事项已清空</label
              >
              <label v-if="!editing" class="check"
                ><input
                  v-model="form.approvalConfirmed"
                  type="checkbox"
                  data-testid="approval-confirmed"
                />已确认来源、单位及当前材料和刀具的适用条件</label
              >
            </section>
            <label
              >本次说明<textarea
                v-model="form.note"
                maxlength="4000"
                data-testid="note"
                placeholder="说明新增、批准、拒绝、撤销或修订的原因"
              />
            </label>
            <button
              type="submit"
              class="primary"
              :disabled="busy || stale"
              data-testid="submit"
            >
              {{
                saving
                  ? "正在保存…"
                  : creating
                    ? "保存为待审核"
                    : "提交本次变更"
              }}
            </button>
          </fieldset>
        </form>
        <section
          v-if="selected?.review.actor"
          class="current-review"
          aria-label="当前审核记录"
        >
          <h3>当前审核记录</h3>
          <p>
            {{ selected.review.actor }} · {{ selected.review.at }} ·
            {{ selected.review.note }}
          </p>
          <template v-if="selected.review.evidence?.source_url">
            <p>
              来源：<a
                v-if="safeSource(selected.review.evidence.source_url)"
                :href="safeSource(selected.review.evidence.source_url)"
                target="_blank"
                rel="noopener noreferrer"
                >查看来源文档</a
              >
              · 第 {{ selected.review.evidence.page }} 页
            </p>
            <p class="hash">
              校验值：{{ selected.review.evidence.document_sha256 }}
            </p>
            <p>适用条件：{{ selected.review.evidence.applicability }}</p>
            <p v-if="selected.review.evidence.constraints">
              审核范围：{{
                describeDrillingLimits(selected.review.evidence.constraints)
              }}
            </p>
            <p
              v-if="
                !selected.review.evidence.constraints ||
                !selected.review.evidence_digest
              "
              role="alert"
            >
              此旧记录缺少结构化适用范围或证据摘要，需要修订并重新审核，不能参与正式推荐。
            </p>
            <p>核验方法：{{ selected.review.evidence.verification_method }}</p>
          </template>
        </section>
        <section v-if="selected" aria-label="版本历史" class="history">
          <h3>版本历史</h3>
          <p v-if="!selected.review_history.length">尚无审核或修订记录。</p>
          <details
            v-for="(event, index) in [...selected.review_history].reverse()"
            :key="index"
          >
            <summary>
              版本 {{ event.before.revision }} →
              {{ event.before.revision + 1 }} ·
              {{ statusLabels[event.after_review.status] }} ·
              {{ event.after_review.actor }}
            </summary>
            <p>{{ event.after_review.at }} · {{ event.after_review.note }}</p>
            <p>
              变更前：{{ event.before.material }} / {{ event.before.feature }} /
              {{ event.before.process }} / {{ event.before.tool }}
            </p>
            <p v-for="(value, name) in event.before.parameters" :key="name">
              {{ name }}：{{ value }}
            </p>
            <p v-if="event.after_review.evidence?.source_url">
              当次依据：{{ event.after_review.evidence.source_url }} · 第
              {{ event.after_review.evidence.page }} 页；{{
                event.after_review.evidence.applicability
              }}
            </p>
            <p v-if="event.before.review.evidence?.constraints">
              变更前审核范围：{{
                describeDrillingLimits(event.before.review.evidence.constraints)
              }}
            </p>
            <p v-if="event.after_review.evidence?.constraints">
              当次审核范围：{{
                describeDrillingLimits(event.after_review.evidence.constraints)
              }}
            </p>
          </details>
        </section>
      </section>
      <section v-else class="review-detail">
        <h2>选择资料查看审核依据</h2>
        <p>待审核资料不会进入正式工艺推荐。</p>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useProcessReview } from "@/composables/useProcessReview";
import {
  approvalEvidence,
  describeDrillingLimits,
  formContent,
  newReviewForm,
} from "@/utils/processReviewForm";
import type {
  ProcessRecord,
  ReviewAction,
  ReviewStatus,
} from "@/api/processKnowledge";

const auth = useAuthStore();
const canReview = computed(
  () =>
    auth.isAuthenticated &&
    !auth.isGuest &&
    ["admin", "engineer"].includes(auth.userRole),
);
const {
  records,
  selectedId,
  selected,
  loading,
  saving,
  stale,
  error,
  notice,
  load,
  save,
} = useProcessReview();
const busy = computed(() => loading.value || saving.value);
const creating = ref(false);
const decision = ref<ReviewAction["decision"]>("approved");
const form = ref(newReviewForm());
const validationError = ref("");
const search = ref("");
const filter = ref<ReviewStatus | "all">("all");
const statusLabels: Record<ReviewStatus, string> = {
  pending: "待审核",
  approved: "已批准",
  rejected: "已拒绝",
  revoked: "已撤销",
};
const editing = computed(() => creating.value || decision.value === "revise");
const filteredRecords = computed(() =>
  records.value.filter(
    (record) =>
      (filter.value === "all" || record.review.status === filter.value) &&
      [record.material, record.tool, record.feature, record.source]
        .join(" ")
        .toLowerCase()
        .includes(search.value.trim().toLowerCase()),
  ),
);

watch(selected, (record) => {
  if (!record) return;
  creating.value = false;
  form.value = newReviewForm(record);
  decision.value =
    record.review.status === "pending"
      ? "approved"
      : record.review.status === "approved"
        ? "revoked"
        : "revise";
  validationError.value = "";
});
watch(decision, () => {
  if (selected.value) form.value = newReviewForm(selected.value);
  validationError.value = "";
});
function selectRecord(record: ProcessRecord) {
  selectedId.value = record.record_id;
  creating.value = false;
}
function startCreate() {
  selectedId.value = "";
  creating.value = true;
  form.value = newReviewForm();
  validationError.value = "";
  notice.value = "";
}
async function reload() {
  validationError.value = "";
  if (canReview.value) await load();
}
function safeSource(value: string): string | undefined {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
async function submit() {
  if (!canReview.value || busy.value || stale.value) return;
  validationError.value = "";
  try {
    if (!form.value.note.trim()) throw new Error("请填写本次说明。");
    if (creating.value) {
      await save(formContent(form.value), true);
      return;
    }
    if (!selected.value) return;
    const action: ReviewAction = {
      expected_revision: selected.value.revision,
      decision: decision.value,
      note: form.value.note.trim(),
    };
    if (decision.value === "approved")
      action.evidence = approvalEvidence(form.value, selected.value);
    if (decision.value === "revise")
      action.replacement = formContent(form.value, selected.value);
    await save(action);
  } catch (failure) {
    validationError.value =
      failure instanceof Error ? failure.message : "请核对表单。";
  }
}
onMounted(reload);
</script>

<style scoped>
.knowledge-review {
  padding: 28px;
  color: var(--text-primary, #213247);
}
.page-heading,
.actions {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: center;
}
h1 {
  margin: 0;
  font-size: 26px;
}
h2 {
  font-size: 20px;
}
h3 {
  font-size: 16px;
}
p {
  line-height: 1.6;
}
.scope-note {
  padding: 12px 16px;
  background: var(--bg-200, #edf3fa);
  border-radius: 8px;
}
.review-layout {
  display: grid;
  grid-template-columns: minmax(230px, 300px) minmax(0, 1fr);
  gap: 24px;
}
.record-list,
.review-detail {
  border: 1px solid var(--border-light, #d7e0ea);
  border-radius: 12px;
  padding: 20px;
  background: var(--bg-100, white);
}
label {
  display: grid;
  gap: 6px;
  margin-bottom: 14px;
  font-size: 14px;
}
input,
select,
textarea,
button {
  font: inherit;
  color: inherit;
  border: 1px solid var(--border-light, #bac9da);
  border-radius: 6px;
  background: var(--bg-100, white);
  padding: 9px 11px;
  min-width: 0;
}
textarea {
  min-height: 76px;
  resize: vertical;
}
input[readonly] {
  background: var(--bg-200, #edf3fa);
}
button {
  cursor: pointer;
}
button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
button:focus-visible,
input:focus-visible,
textarea:focus-visible,
select:focus-visible {
  outline: 2px solid #3478d4;
  outline-offset: 2px;
}
.record-card {
  display: grid;
  gap: 8px;
  width: 100%;
  margin-bottom: 10px;
  text-align: left;
}
.record-card.selected {
  border-color: #3478d4;
  box-shadow: inset 3px 0 #3478d4;
}
.record-card span {
  font-size: 13px;
  overflow-wrap: anywhere;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
.parameter-row {
  display: grid;
  grid-template-columns: 1.3fr 1fr 1fr auto;
  gap: 10px;
  align-items: end;
}
.parameter-row button {
  margin-bottom: 14px;
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.check input {
  margin-top: 3px;
}
.evidence-form,
.current-review,
.history {
  margin-top: 22px;
  padding-top: 8px;
  border-top: 1px solid var(--border-light, #d7e0ea);
}
.primary {
  background: #2465b8;
  color: white;
  border-color: #2465b8;
}
.error {
  color: #a72e2e;
  padding: 12px;
  background: #fff0f0;
}
.notice {
  color: #19613b;
}
.hash,
details p {
  overflow-wrap: anywhere;
}
details {
  padding: 12px 0;
}
summary {
  cursor: pointer;
}
@media (max-width: 980px) {
  .review-layout {
    grid-template-columns: 1fr;
  }
  .page-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (max-width: 620px) {
  .knowledge-review {
    padding: 14px;
  }
  .form-grid,
  .parameter-row {
    grid-template-columns: 1fr;
  }
}
</style>
