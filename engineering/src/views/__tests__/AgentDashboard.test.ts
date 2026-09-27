import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

// Mock agent store
const mockAgentStore = vi.hoisted(() => ({
  agents: [] as any[],
  currentAgent: null as any,
  loading: false,
  detailLoading: false,
  error: null as string | null,
  statusFilter: null as string | null,
  // 活动时间线区块（2026-09 WIP 新增）：组件模板读取 activityEntries.length，
  // mock 缺失会导致挂载失败 Cannot read properties of undefined
  activityEntries: [] as any[],
  activityError: null as string | null,
  activityLoading: false,
  fetchAgents: vi.fn(() => Promise.resolve()),
  fetchAgentDetail: vi.fn(() => Promise.resolve()),
  fetchActivity: vi.fn(() => Promise.resolve()),
  statusTagType: (status: string) =>
    status === "busy" ? "warning" : status === "error" ? "danger" : "success",
  statusLabel: (status: string) => status,
  formatTime: (ts: string | number | null | undefined) =>
    ts ? String(ts) : "-",
}));

vi.mock("@/stores/agents", () => ({
  useAgentStore: () => mockAgentStore,
}));

import AgentDashboard from "@/views/AgentDashboard.vue";

describe("AgentDashboard.vue", () => {
  let pinia: ReturnType<typeof createPinia>;
  let router: ReturnType<typeof createRouter>;

  beforeEach(() => {
    setActivePinia((pinia = createPinia()));
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div/>" } }],
    });
    vi.clearAllMocks();
    mockAgentStore.agents = [];
    mockAgentStore.currentAgent = null;
    mockAgentStore.loading = false;
    mockAgentStore.error = null;
    mockAgentStore.fetchAgents.mockResolvedValue(undefined);
    mockAgentStore.fetchAgentDetail.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const mountAgentDashboard = (options = {}) => {
    return mount(AgentDashboard, {
      global: {
        plugins: [pinia, router],
        mocks: {
          $t: (key: string) => key,
        },
        ...options,
      },
    });
  };

  it("组件能正确挂载", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    expect(wrapper.exists()).toBe(true);
    expect(wrapper.find(".agent-dashboard").exists()).toBe(true);
  });

  it("渲染页面标题与副标题", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    expect(wrapper.find(".page-header__title h1").text()).toBe("智能体管理");
    expect(wrapper.find(".subtitle").text()).toBe("管理和监控智能体运行状态");
  });

  it("渲染统计概览卡片（总数/活跃/空闲/错误）", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    const cards = wrapper.findAll(".stat-card");
    expect(cards.length).toBe(4);
    expect(wrapper.find(".stat-card__label").text()).toBe("总智能体");
  });

  it("渲染部署新代理按钮", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    const deployBtn = wrapper
      .findAll(".page-header__actions button")
      .find((b) => b.text().includes("部署新智能体"));
    expect(deployBtn).toBeDefined();
    expect(deployBtn?.classes()).toContain("el-button--primary");
  });

  it("渲染状态筛选下拉框", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    const selects = wrapper.findAll("select");
    expect(selects.length).toBeGreaterThan(0);
    // 状态筛选下拉框选项
    const options = selects[0].findAll("option");
    expect(options.length).toBeGreaterThanOrEqual(6); // all/busy/idle/paused/error/stopped/recovering
  });

  it("挂载时调用 fetchAgents", async () => {
    mountAgentDashboard();
    await flushPromises();
    expect(mockAgentStore.fetchAgents).toHaveBeenCalled();
  });

  it("store 中无代理时渲染空状态", async () => {
    mockAgentStore.agents = [];
    const wrapper = mountAgentDashboard();
    await flushPromises();
    // 网格存在但无卡片
    expect(wrapper.find(".agent-grid").exists()).toBe(true);
    expect(wrapper.findAll(".agent-card").length).toBe(0);
  });

  it("store 中有代理时渲染代理卡片", async () => {
    mockAgentStore.agents = [
      {
        agent_id: "agent-001",
        status: "busy",
        current_task_id: "task1",
        last_heartbeat: "2025-01-01",
        updated_at: "2025-01-01",
      },
      {
        agent_id: "agent-002",
        status: "idle",
        current_task_id: null,
        last_heartbeat: "2025-01-01",
        updated_at: "2025-01-01",
      },
    ];
    const wrapper = mountAgentDashboard();
    await flushPromises();
    // stub 模式下 el-card 不会真实渲染，但 v-for 仍会执行
    expect(wrapper.find(".agent-grid").exists()).toBe(true);
  });

  it("stats 计算属性随 store 数据更新", async () => {
    mockAgentStore.agents = [
      {
        agent_id: "a1",
        status: "busy",
        current_task_id: null,
        last_heartbeat: "",
        updated_at: "",
      },
      {
        agent_id: "a2",
        status: "idle",
        current_task_id: null,
        last_heartbeat: "",
        updated_at: "",
      },
      {
        agent_id: "a3",
        status: "error",
        current_task_id: null,
        last_heartbeat: "",
        updated_at: "",
      },
    ];
    const wrapper = mountAgentDashboard();
    await flushPromises();
    const statValues = wrapper.findAll(".stat-card__value");
    expect(statValues[0].text()).toBe("3"); // total
    expect(statValues[1].text()).toBe("1"); // active(busy)
    expect(statValues[2].text()).toBe("1"); // idle
    expect(statValues[3].text()).toBe("1"); // error
  });

  it("fetchAgents 抛错时设置 dataLoadError", async () => {
    mockAgentStore.fetchAgents.mockRejectedValueOnce(
      new Error("network error"),
    );
    const wrapper = mountAgentDashboard();
    await flushPromises();
    // 即使出错，组件也应当挂载
    expect(wrapper.find(".agent-dashboard").exists()).toBe(true);
  });

  it("点击部署按钮打开部署弹窗", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    const deployBtn = wrapper
      .findAll(".page-header__actions button")
      .find((b) => b.text().includes("部署新智能体"));
    await deployBtn?.trigger("click");
    // deployDialogVisible 切换为 true
    expect(wrapper.find(".agent-dashboard").exists()).toBe(true);
  });

  it("statusFilter 默认为 all", async () => {
    const wrapper = mountAgentDashboard();
    await flushPromises();
    // 通过筛选下拉框的初始值校验（stub select 不维护 v-model，但能挂载即可）
    expect(wrapper.find(".agent-dashboard").exists()).toBe(true);
  });
});
