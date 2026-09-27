import { reactive, computed, onMounted, ref } from "vue";
import {
  getSovereigntySettings as apiGetSovereignty,
  updateSovereigntySettings as apiUpdateSovereignty,
  resetSovereigntySettings as apiResetSovereignty,
} from "@/api/governance";

interface SovereigntySettings {
  ai_autonomy_level: number;
  require_confirmation_for_predict: boolean;
  require_confirmation_for_train: boolean;
  show_confidence_indicator: boolean;
  show_alternatives: boolean;
  show_reasoning: boolean;
}

const STORAGE_KEY = "ai_sovereignty_settings";

export function useSovereigntySettings() {
  const sovereigntySettings = reactive<SovereigntySettings>({
    ai_autonomy_level: 2,
    require_confirmation_for_predict: false,
    require_confirmation_for_train: true,
    show_confidence_indicator: true,
    show_alternatives: true,
    show_reasoning: true,
  });

  // W9.1：设置已后端持久化；true 表示最近一次读/写与后端同步成功。
  // 后端是权威数据源，localStorage 仅作离线缓存（后端不可达时降级使用）。
  const backendSynced = ref(false);

  const autonomyMarks = {
    0: "0",
    1: "1",
    2: "2",
    3: "3",
    4: "4",
  };

  const autonomyLabels = [
    "完全手动",
    "建议需确认",
    "推荐模式",
    "半自动",
    "全自动",
  ];

  function formatAutonomyLevel(val: number): string {
    return `${val} - ${autonomyLabels[val]}`;
  }

  const currentAutonomyDescription = computed(() => {
    const level = sovereigntySettings.ai_autonomy_level;
    const descriptions = [
      "完全手动模式：所有AI建议均需用户明确确认后方可执行，系统不进行任何自动决策。",
      "建议需确认模式：AI提供建议，用户在审阅确认后执行。",
      "推荐模式（默认）：AI提供推荐方案，用户可选择接受、修改或拒绝。",
      "半自动模式：高置信度（≥80%）AI建议自动执行，低置信度需用户确认。",
      "全自动模式：AI可直接执行推荐操作，但保留完整操作日志供事后审查和追溯。",
    ];
    return descriptions[level];
  });

  function getAutonomyAlertType(
    level: number,
  ): "success" | "warning" | "info" | "error" {
    if (level <= 1) return "info";
    if (level === 2) return "success";
    if (level === 3) return "warning";
    return "error";
  }

  function handleAutonomyChange(val: number | number[]) {
    const v = Array.isArray(val) ? val[0] : val;
    if (v >= 3) {
      sovereigntySettings.require_confirmation_for_predict = false;
    }
    if (v >= 4) {
      sovereigntySettings.require_confirmation_for_train = false;
    }
  }

  async function saveSovereigntySettings() {
    // W9.1：先写后端（权威），成功后镜像 localStorage 作离线缓存
    try {
      const resp = await apiUpdateSovereignty({ ...sovereigntySettings });
      if (resp.code === 0) {
        backendSynced.value = true;
        try {
          localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify(sovereigntySettings),
          );
        } catch {
          /* localStorage 不可用时忽略，后端已持久化 */
        }
        ElMessage.success("AI主权设置已保存并同步到后端");
        return;
      }
      throw new Error(resp.message || "后端返回异常");
    } catch (e) {
      // 后端不可达：降级为本地保存（下次打开设置页会重试同步）
      backendSynced.value = false;
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(sovereigntySettings));
      } catch {
        /* localStorage 也不可用 */
      }
      console.warn(
        "[useSovereigntySettings] save: backend sync failed, saved locally:",
        e,
      );
      ElMessage.warning(
        "已本地保存，但后端同步失败（设置暂不生效于后端 AI 行为）",
      );
    }
  }

  function resetSovereigntySettings() {
    sovereigntySettings.ai_autonomy_level = 2;
    sovereigntySettings.require_confirmation_for_predict = false;
    sovereigntySettings.require_confirmation_for_train = true;
    sovereigntySettings.show_confidence_indicator = true;
    sovereigntySettings.show_alternatives = true;
    sovereigntySettings.show_reasoning = true;
    // 同步重置后端（失败不阻断本地重置）
    apiResetSovereignty()
      .then((resp) => {
        if (resp.code === 0) backendSynced.value = true;
      })
      .catch(() => {
        backendSynced.value = false;
      });
    ElMessage.info("已恢复默认AI主权设置");
  }

  onMounted(async () => {
    // W9.1：优先从后端读取（权威数据源）；不可达时降级 localStorage 缓存
    try {
      const resp = await apiGetSovereignty();
      if (resp.code === 0 && resp.data?.settings) {
        Object.assign(sovereigntySettings, resp.data.settings);
        backendSynced.value = true;
        return;
      }
    } catch (e) {
      console.warn(
        "[useSovereigntySettings] onMounted: backend unavailable, using local cache:",
        e,
      );
    }
    backendSynced.value = false;
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        Object.assign(sovereigntySettings, parsed);
      } catch (e: unknown) {
        // localStorage 数据损坏时清理脏数据，避免后续每次启动都重复抛错
        console.warn(
          "[useSovereigntySettings] onMounted: failed to parse localStorage, ignoring saved settings:",
          e,
        );
        try {
          localStorage.removeItem(STORAGE_KEY);
        } catch {
          /* localStorage 不可用时无可清理，忽略 */
        }
      }
    }
  });

  return {
    sovereigntySettings,
    backendSynced,
    autonomyMarks,
    autonomyLabels,
    formatAutonomyLevel,
    currentAutonomyDescription,
    getAutonomyAlertType,
    handleAutonomyChange,
    saveSovereigntySettings,
    resetSovereigntySettings,
  };
}
