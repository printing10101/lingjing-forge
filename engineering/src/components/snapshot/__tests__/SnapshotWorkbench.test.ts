/**
 * SnapshotWorkbench.vue 组件测试
 *
 * 覆盖范围（行为级——组件已子组件化，列表/详情/创建对话框 UI 移入
 * SnapshotListPanel / SnapshotDetailPanel / SnapshotCreateDialog）：
 *   1. 组件挂载与初始化（挂载即 loadSnapshots）
 *   2. 面板行为（刷新/筛选变更/重置筛选/分页变更/选择快照/关闭详情）
 *   3. 创建快照（提交成功/失败）
 *   4. 复现交互（确认/取消/成功/不支持 warning/其他错误/无选中）
 *
 * 对应 ADR-005 阶段 2 验收标准（前端"实验快照"视图 + "一键复现"按钮）。
 * 原挂载于独立路由 /snapshot-panel，现挂载于数据飞轮"实验快照"Tab。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { shallowMount, flushPromises, VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";
import type { ExperimentSnapshot } from "@/contracts/observability";

// Mock: vue-i18n（useI18n composition API）
vi.mock("vue-i18n", () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params) {
        let result = key;
        Object.entries(params).forEach(([k, v]) => {
          result = result.replace(`{${k}}`, String(v));
        });
        return result;
      }
      return key;
    },
  }),
}));

// Mock: @element-plus/icons-vue
vi.mock("@element-plus/icons-vue", () => ({
  Refresh: { name: "Refresh", template: '<i class="icon-refresh" />' },
  Plus: { name: "Plus", template: '<i class="icon-plus" />' },
  VideoPlay: { name: "VideoPlay", template: '<i class="icon-video-play" />' },
  Close: { name: "Close", template: '<i class="icon-close" />' },
}));

// Mock: element-plus（ElMessage / ElMessageBox）
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}));
const mockElMessageBox = vi.hoisted(() => ({
  confirm: vi.fn(() => Promise.resolve("confirm")),
}));
vi.mock("element-plus", () => ({
  ElMessage: mockElMessage,
  ElMessageBox: mockElMessageBox,
}));

// Mock: @/composables/useSnapshots
const mockUseSnapshots = vi.hoisted(() => ({
  snapshots: { value: [] as ExperimentSnapshot[] },
  loading: { value: false },
  totalCount: { value: 0 },
  currentPage: { value: 1 },
  pageSize: { value: 20 },
  filterCreatedBy: { value: "" as string },
  filterGitSha: { value: "" as string },
  filterModelUri: { value: "" as string },
  loadSnapshots: vi.fn(() => Promise.resolve()),
  resetFilters: vi.fn(() => Promise.resolve()),
  currentSnapshot: { value: null as ExperimentSnapshot | null },
  currentLoading: { value: false },
  selectSnapshot: vi.fn((_id: string) => Promise.resolve()),
  clearCurrent: vi.fn(),
  creating: { value: false },
  reproducing: { value: false },
  submitSnapshot: vi.fn((_body: unknown) => Promise.resolve("snap_new_001")),
  reproduce: vi.fn((_id: string) => Promise.resolve("wf_repro_001")),
}));

vi.mock("@/composables/useSnapshots", () => ({
  useSnapshots: () => mockUseSnapshots,
}));

import SnapshotWorkbench from "@/components/snapshot/SnapshotWorkbench.vue";

// 测试数据
function makeSnapshot(
  overrides: Partial<ExperimentSnapshot> = {},
): ExperimentSnapshot {
  return {
    snapshot_id: "snap-001-abcdefgh",
    created_at: "2026-07-13T10:00:00Z",
    created_by: "user-1",
    git_sha: "abc123def456789",
    code_dirty: false,
    config: { lr: 0.001, epochs: 100 },
    dataset_versions: ["dataset://phm2010/v1"],
    model_uri: "model://ltc-v1",
    metrics: { val_loss: 0.06, pcc: 0.51 },
    environment: { python: "3.10", torch: "2.0.1" },
    ...overrides,
  };
}

// 测试主体
describe("SnapshotWorkbench.vue", () => {
  let pinia: ReturnType<typeof createPinia>;
  let router: ReturnType<typeof createRouter>;

  beforeEach(() => {
    setActivePinia((pinia = createPinia()));
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div/>" } }],
    });

    // 重置 mock 状态
    mockUseSnapshots.snapshots.value = [];
    mockUseSnapshots.loading.value = false;
    mockUseSnapshots.totalCount.value = 0;
    mockUseSnapshots.currentPage.value = 1;
    mockUseSnapshots.pageSize.value = 20;
    mockUseSnapshots.filterCreatedBy.value = "";
    mockUseSnapshots.filterGitSha.value = "";
    mockUseSnapshots.filterModelUri.value = "";
    mockUseSnapshots.currentSnapshot.value = null;
    mockUseSnapshots.currentLoading.value = false;
    mockUseSnapshots.creating.value = false;
    mockUseSnapshots.reproducing.value = false;
    mockUseSnapshots.loadSnapshots.mockResolvedValue(undefined);
    mockUseSnapshots.resetFilters.mockResolvedValue(undefined);
    mockUseSnapshots.selectSnapshot.mockResolvedValue(undefined);
    mockUseSnapshots.submitSnapshot.mockResolvedValue("snap_new_001");
    mockUseSnapshots.reproduce.mockResolvedValue("wf_repro_001");

    mockElMessageBox.confirm.mockResolvedValue("confirm");

    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const mountPanel = (options = {}): VueWrapper<any> => {
    return shallowMount(SnapshotWorkbench, {
      global: {
        plugins: [pinia, router],
        ...options,
      },
    });
  };

  // 1. 组件挂载与初始化
  describe("组件挂载", () => {
    it("组件能正确挂载", async () => {
      const wrapper = mountPanel();
      await flushPromises();
      expect(wrapper.exists()).toBe(true);
      expect(wrapper.find(".snapshot-workbench").exists()).toBe(true);
    });

    it("挂载时调用 loadSnapshots", async () => {
      mountPanel();
      await flushPromises();
      expect(mockUseSnapshots.loadSnapshots).toHaveBeenCalled();
    });
  });

  // 2. 面板行为
  describe("面板行为", () => {
    it("点击刷新调用 loadSnapshots", async () => {
      const wrapper = mountPanel();
      await wrapper.vm.handleRefresh();
      expect(mockUseSnapshots.loadSnapshots).toHaveBeenCalled();
    });

    it("筛选变更回到第 1 页并调用 loadSnapshots", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.currentPage.value = 3;
      await wrapper.vm.handleFilterChange();
      expect(mockUseSnapshots.currentPage.value).toBe(1);
      expect(mockUseSnapshots.loadSnapshots).toHaveBeenCalled();
    });

    it("重置筛选调用 resetFilters", async () => {
      const wrapper = mountPanel();
      await wrapper.vm.handleResetFilters();
      expect(mockUseSnapshots.resetFilters).toHaveBeenCalled();
    });

    it("分页变更调用 loadSnapshots", async () => {
      const wrapper = mountPanel();
      await wrapper.vm.handlePageChange();
      expect(mockUseSnapshots.loadSnapshots).toHaveBeenCalled();
    });

    it("选择快照调用 selectSnapshot", async () => {
      const wrapper = mountPanel();
      await wrapper.vm.handleSelectSnapshot("snap-001-abcdefgh");
      expect(mockUseSnapshots.selectSnapshot).toHaveBeenCalledWith(
        "snap-001-abcdefgh",
      );
    });

    it("关闭详情调用 clearCurrent", () => {
      const wrapper = mountPanel();
      wrapper.vm.handleCloseDetail();
      expect(mockUseSnapshots.clearCurrent).toHaveBeenCalled();
    });
  });

  // 3. 创建快照
  describe("创建快照", () => {
    it("提交成功 → ElMessage.success + 关闭对话框 + selectSnapshot", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.submitSnapshot.mockResolvedValueOnce("snap_new_001");
      await wrapper.vm.handleCreateConfirm({
        config: { lr: 0.001 },
        dataset_versions: ["dataset://phm2010/v1"],
      });
      expect(mockUseSnapshots.submitSnapshot).toHaveBeenCalledWith({
        config: { lr: 0.001 },
        dataset_versions: ["dataset://phm2010/v1"],
      });
      expect(mockElMessage.success).toHaveBeenCalledWith(
        "snapshotPanel.msgCreateSuccess",
      );
      expect(mockUseSnapshots.selectSnapshot).toHaveBeenCalledWith(
        "snap_new_001",
      );
      expect(wrapper.vm.createDialogVisible).toBe(false);
    });

    it("提交失败 → ElMessage.error 并显示错误信息", async () => {
      const wrapper = mountPanel();
      const apiErr = new Error("创建失败") as Error & { response?: unknown };
      apiErr.response = { data: { message: "存储空间不足" } };
      mockUseSnapshots.submitSnapshot.mockRejectedValueOnce(apiErr);
      await wrapper.vm.handleCreateConfirm({
        config: {},
        dataset_versions: [],
      });
      expect(mockElMessage.error).toHaveBeenCalledWith("存储空间不足");
    });

    it("提交失败（无 response 信息）→ 显示原始错误", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.submitSnapshot.mockRejectedValueOnce(new Error("boom"));
      await wrapper.vm.handleCreateConfirm({
        config: {},
        dataset_versions: [],
      });
      expect(mockElMessage.error).toHaveBeenCalledWith("Error: boom");
    });
  });

  // 4. 复现交互
  describe("复现交互", () => {
    it("点击复现 → confirm 确认 → reproduce → ElMessage.success", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.currentSnapshot.value = makeSnapshot();
      mockElMessageBox.confirm.mockResolvedValueOnce("confirm");
      await wrapper.vm.handleReproduce();
      expect(mockElMessageBox.confirm).toHaveBeenCalled();
      expect(mockUseSnapshots.reproduce).toHaveBeenCalledWith(
        "snap-001-abcdefgh",
      );
      expect(mockElMessage.success).toHaveBeenCalled();
    });

    it("用户取消确认时不调用 reproduce", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.currentSnapshot.value = makeSnapshot();
      mockElMessageBox.confirm.mockRejectedValueOnce("cancel");
      await wrapper.vm.handleReproduce();
      expect(mockUseSnapshots.reproduce).not.toHaveBeenCalled();
    });

    it("currentSnapshot 为 null 时复现按钮不触发任何调用", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.currentSnapshot.value = null;
      await wrapper.vm.handleReproduce();
      expect(mockElMessageBox.confirm).not.toHaveBeenCalled();
      expect(mockUseSnapshots.reproduce).not.toHaveBeenCalled();
    });

    it("reproduce 失败（不支持复现）→ ElMessage.warning", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.currentSnapshot.value = makeSnapshot();
      mockUseSnapshots.reproduce.mockRejectedValueOnce(
        new Error("该快照不支持一键复现"),
      );
      await wrapper.vm.handleReproduce();
      expect(mockElMessage.warning).toHaveBeenCalledWith(
        "snapshotPanel.msgReproduceNotSupported",
      );
    });

    it("reproduce 失败（其他错误）→ ElMessage.error", async () => {
      const wrapper = mountPanel();
      mockUseSnapshots.currentSnapshot.value = makeSnapshot();
      mockUseSnapshots.reproduce.mockRejectedValueOnce(
        new Error("workflow 服务不可用"),
      );
      await wrapper.vm.handleReproduce();
      expect(mockElMessage.error).toHaveBeenCalledWith(
        "snapshotPanel.msgReproduceFailed",
      );
    });
  });
});
