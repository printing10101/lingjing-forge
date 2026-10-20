import { computed, ref } from "vue";
import {
  createProcessRecord,
  listProcessRecords,
  reviewProcessRecord,
  type ProcessContent,
  type ProcessRecord,
  type ReviewAction,
} from "@/api/processKnowledge";

export function useProcessReview() {
  const records = ref<ProcessRecord[]>([]);
  const selectedId = ref("");
  const selected = computed(() =>
    records.value.find((item) => item.record_id === selectedId.value),
  );
  const loading = ref(false);
  const saving = ref(false);
  const stale = ref(false);
  const error = ref("");
  const notice = ref("");
  let requestGeneration = 0;

  function reportError(failure: unknown) {
    const status = (failure as { response?: { status?: number } })?.response
      ?.status;
    if (status === 409) {
      stale.value = true;
      error.value = "记录已被更新。请重新加载，核对最新内容后再审核。";
    } else if (status === 401 || status === 403) {
      records.value = [];
      error.value = "需要已登录的工程师或管理员身份，请核对当前账号。";
    } else if (status === 503) {
      error.value = "保存失败，变更未生效。请检查服务状态后重试。";
    } else {
      error.value = "操作未完成。请检查输入和服务连接后重试。";
    }
  }

  async function load() {
    if (saving.value) return;
    const generation = ++requestGeneration;
    loading.value = true;
    error.value = "";
    notice.value = "";
    try {
      const result = await listProcessRecords();
      if (generation !== requestGeneration) return;
      records.value = result;
      if (!result.some((item) => item.record_id === selectedId.value))
        selectedId.value = result[0]?.record_id ?? "";
      stale.value = false;
    } catch (failure) {
      if (generation !== requestGeneration) return;
      records.value = [];
      reportError(failure);
    } finally {
      if (generation === requestGeneration) loading.value = false;
    }
  }

  async function save(action: ReviewAction | ProcessContent, create = false) {
    if (
      saving.value ||
      loading.value ||
      stale.value ||
      (!create && !selected.value)
    )
      return false;
    saving.value = true;
    error.value = "";
    notice.value = "";
    try {
      const saved = create
        ? await createProcessRecord(action as ProcessContent)
        : await reviewProcessRecord(
            selected.value!.record_id,
            action as ReviewAction,
          );
      records.value = [
        ...records.value.filter((item) => item.record_id !== saved.record_id),
        saved,
      ];
      selectedId.value = saved.record_id;
      notice.value = create
        ? "已保存为待审核资料。"
        : "变更已保存，版本与审核历史已更新。";
      return true;
    } catch (failure) {
      reportError(failure);
      return false;
    } finally {
      saving.value = false;
    }
  }

  return {
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
  };
}
