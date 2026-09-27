<template>
  <div class="logs-panel">
    <div class="panel-toolbar">
      <el-select
        v-model="selectedPlugin"
        :placeholder="t('pluginLogs.placeholderSelectPlugin')"
        style="width: 200px"
      >
        <el-option :label="t('pluginLogs.labelAllPlugins')" value="" />
        <el-option
          v-for="p in plugins"
          :key="p.id"
          :label="p.name"
          :value="p.id"
        />
      </el-select>
      <el-select
        v-model="logLevel"
        :placeholder="t('pluginLogs.placeholderLogLevel')"
        style="width: 120px; margin-left: 10px"
      >
        <el-option :label="t('pluginLogs.labelAll')" value="" />
        <el-option label="DEBUG" value="debug" />
        <el-option label="INFO" value="info" />
        <el-option label="WARNING" value="warning" />
        <el-option label="ERROR" value="error" />
      </el-select>
      <el-button
        type="primary"
        style="margin-left: 10px"
        :loading="loading"
        @click="refreshLogs"
      >
        <el-icon><Refresh /></el-icon> {{ t("pluginLogs.btnRefresh") }}
      </el-button>
      <el-button
        type="success"
        style="margin-left: 10px"
        :disabled="filteredLogs.length === 0"
        @click="exportLogs"
      >
        <el-icon><Download /></el-icon> {{ t("pluginLogs.btnExport") }}
      </el-button>
    </div>

    <el-card>
      <div v-loading="loading" class="log-container">
        <div
          v-for="log in filteredLogs"
          :key="`${log.timestamp}-${log.plugin}-${log.message}`"
          class="log-entry"
          :class="log.level"
        >
          <span class="log-time">{{ formatTime(log.timestamp) }}</span>
          <el-tag
            :type="getLevelType(log.level)"
            size="small"
            class="log-level"
          >
            {{ log.level.toUpperCase() }}
          </el-tag>
          <span class="log-plugin">[{{ log.plugin }}]</span>
          <span class="log-message">{{ log.message }}</span>
        </div>
        <el-empty
          v-if="!loading && filteredLogs.length === 0"
          :description="t('pluginLogs.emptyNoLogs')"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
// 原 /plugin-logs 页面内容，现挂载于插件中心"日志"Tab
import { ref, computed, onMounted, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import { Refresh, Download } from "@element-plus/icons-vue";
import http from "@/utils/http";
import { triggerFileDownload } from "@/utils/download";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { usePluginStore } from "../../stores/plugin";

const { t } = useI18n();

/** 日志条目接口 */
interface LogEntry {
  timestamp: number;
  level: "debug" | "info" | "warning" | "error";
  plugin: string;
  message: string;
}

/** 后端日志响应 */
interface PluginLogsResponse {
  code: number;
  data: {
    logs: Array<{
      timestamp?: string | number;
      time?: string | number;
      level?: string;
      plugin?: string;
      message?: string;
      msg?: string;
    }>;
    total?: number;
  };
  message?: string;
}

const pluginStore = usePluginStore();
const selectedPlugin = ref("");
const logLevel = ref("");
const loading = ref(false);
const logs = ref<LogEntry[]>([]);

const plugins = computed(() => pluginStore.plugins);

const filteredLogs = computed(() => {
  let result = logs.value;
  if (selectedPlugin.value) {
    result = result.filter((l) => l.plugin === selectedPlugin.value);
  }
  if (logLevel.value) {
    result = result.filter((l) => l.level === logLevel.value);
  }
  return result.sort((a, b) => b.timestamp - a.timestamp);
});

/**
 * 将后端返回的日志条目规范化为前端统一格式
 */
function normalizeLogEntry(
  raw: PluginLogsResponse["data"]["logs"][number],
  fallbackPlugin: string,
): LogEntry | null {
  const rawLevel = (raw.level || "info").toLowerCase();
  // 白名单校验日志级别，防止注入未知级别
  const level: LogEntry["level"] = (
    ["debug", "info", "warning", "error"].includes(rawLevel) ? rawLevel : "info"
  ) as LogEntry["level"];

  const rawTime = raw.timestamp ?? raw.time;
  let timestamp: number;
  if (typeof rawTime === "number") {
    timestamp = rawTime;
  } else if (typeof rawTime === "string") {
    const parsed = Date.parse(rawTime);
    timestamp = Number.isNaN(parsed) ? Date.now() : parsed;
  } else {
    timestamp = Date.now();
  }

  const message = raw.message ?? raw.msg ?? "";
  if (!message) return null;

  return {
    timestamp,
    level,
    plugin: raw.plugin || fallbackPlugin,
    message,
  };
}

/**
 * 拉取指定插件的日志
 * 后端端点：GET /api/v1/plugins/{plugin_id}/logs
 */
async function fetchLogsForPlugin(pluginId: string): Promise<LogEntry[]> {
  const params: Record<string, string | number> = { limit: 500 };
  if (logLevel.value) params.level = logLevel.value;

  const response = await http.get<PluginLogsResponse>(
    buildApiPath(API_CONFIG.PLUGINS, `/${encodeURIComponent(pluginId)}/logs`),
    { params },
  );
  const rawLogs = response.data?.data?.logs ?? [];
  return rawLogs
    .map((r) => normalizeLogEntry(r, pluginId))
    .filter((v): v is LogEntry => v !== null);
}

/**
 * 刷新日志：
 * - 若选中具体插件，仅拉取该插件日志
 * - 若选择"全部插件"，并行拉取所有已启用插件日志后合并
 */
async function refreshLogs() {
  if (loading.value) return;
  loading.value = true;
  try {
    if (selectedPlugin.value) {
      logs.value = await fetchLogsForPlugin(selectedPlugin.value);
    } else {
      const targets = pluginStore.plugins;
      if (targets.length === 0) {
        logs.value = [];
      } else {
        // 并行拉取所有插件日志，任一失败不影响其他
        const results = await Promise.allSettled(
          targets.map((p) => fetchLogsForPlugin(p.id)),
        );
        const merged: LogEntry[] = [];
        for (const r of results) {
          if (r.status === "fulfilled") merged.push(...r.value);
        }
        logs.value = merged;
      }
    }
    ElMessage.success(t("pluginLogs.msgLoaded", { count: logs.value.length }));
  } catch {
    // http 拦截器已统一提示错误，此处仅兜底
    logs.value = [];
  } finally {
    loading.value = false;
  }
}

/**
 * 导出当前过滤后的日志为 CSV 文件
 */
function exportLogs() {
  const data = filteredLogs.value;
  if (data.length === 0) {
    ElMessage.warning(t("pluginLogs.msgNoLogsToExport"));
    return;
  }

  const header = "timestamp,level,plugin,message\n";
  const escapeCsv = (s: string | number) => {
    const str = String(s);
    if (/[",\n]/.test(str)) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };
  const rows = data
    .map((l) =>
      [new Date(l.timestamp).toISOString(), l.level, l.plugin, l.message]
        .map(escapeCsv)
        .join(","),
    )
    .join("\n");

  const csv = header + rows + "\n";
  // 添加 BOM 以便 Excel 正确识别 UTF-8 编码
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  triggerFileDownload(
    blob,
    `plugin-logs-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`,
  );
  ElMessage.success(t("pluginLogs.msgExported", { count: data.length }));
}

const formatTime = (timestamp: number) => {
  return new Date(timestamp).toLocaleTimeString();
};

const getLevelType = (level: string) => {
  switch (level) {
    case "debug":
      return "info";
    case "info":
      return "success";
    case "warning":
      return "warning";
    case "error":
      return "danger";
    default:
      return "info";
  }
};

onMounted(async () => {
  // 并行请求插件列表和日志数据
  await Promise.all([pluginStore.fetchPlugins(), refreshLogs()]);
});

// 切换插件或日志级别时自动刷新
watch([selectedPlugin, logLevel], () => {
  refreshLogs();
});
</script>

<style scoped>
.panel-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.log-container {
  min-height: 300px;
  max-height: 560px;
  overflow-y: auto;
}
</style>
