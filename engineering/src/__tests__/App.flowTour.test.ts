/**
 * W10.2 制造流程引导测试。
 *
 * 验证点：
 * 1. App 挂载两个 Tour 实例（全局引导 + 制造流程引导），storageKey 不同；
 * 2. 流程引导 8 步：首尾居中（无 target），中间步骤覆盖
 *    建模/工艺/仿真/刀轨/飞轮/审批的侧边栏入口（a[href] 选择器）；
 * 3. 链式触发：全局引导 finish 后（且流程引导未完成时）自动启动流程引导；
 * 4. 完成标记：流程引导 finish 后写入 flow_tour_completed_v1。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import Tour from "@/components/Onboarding/Tour.vue";
import App from "@/App.vue";

vi.mock("vue-i18n", () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/", name: "home" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({ isAuthenticated: true }),
}));

vi.mock("@/stores/version", () => ({
  useVersionStore: () => ({
    fetchVersionInfo: vi.fn(() => Promise.resolve({})),
    checkConsistency: vi.fn(),
  }),
}));

vi.mock("@/stores/project", () => ({
  useProjectStore: () => ({
    projectName: "demo",
    isModified: false,
  }),
}));

vi.mock("@/composables/useBackendStatus", () => ({
  useBackendStatus: () => ({
    state: { status: "running" },
    tauriMode: false,
  }),
}));

// 重型子组件打桩（App 只做编排，不参与断言）
vi.mock("@/components/AppLayout.vue", () => ({
  default: { template: '<div class="app-layout-stub" />' },
}));
vi.mock("@/components/SplashScreen.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/step_import/StepImportDialog.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/dxf_import/DxfImportDialog.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/ErrorConflictDialog.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/BackendStartupDialog.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/AppFileDialogs.vue", () => ({
  default: { template: "<div />" },
}));

const FLOW_KEY = "flow_tour_completed_v1";

function mountApp() {
  return mount(App, {
    global: {
      provide: { locale: { value: "zh-cn" } },
      stubs: {
        "el-config-provider": { template: "<div><slot /></div>" },
        "el-tag": { template: "<span><slot /></span>" },
      },
    },
  });
}

let wrapper: ReturnType<typeof mountApp>;

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  wrapper?.unmount();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("W10.2 制造流程引导", () => {
  it("挂载两个 Tour 实例且 storageKey 不同", () => {
    wrapper = mountApp();
    const tours = wrapper.findAllComponents(Tour);
    expect(tours.length).toBe(2);
    expect(tours[0].props("storageKey")).toBe("tour_progress_v1");
    expect(tours[1].props("storageKey")).toBe("flow_tour_progress_v1");
  });

  it("流程引导 8 步：首尾居中，中间覆盖六个业务入口", () => {
    wrapper = mountApp();
    const flowTour = wrapper.findAllComponents(Tour)[1];
    const steps = flowTour.props("steps") as Array<{
      title: string;
      target?: string;
    }>;

    expect(steps.length).toBe(8);
    // 首尾居中（无 target）
    expect(steps[0].target).toBeUndefined();
    expect(steps[7].target).toBeUndefined();
    // 中间步骤目标为侧边栏 router-link 渲染出的 a[href]
    const targets = steps.slice(1, 7).map((s) => s.target);
    expect(targets).toEqual([
      '.sidebar-nav a[href="/nl-modeling"]',
      '.sidebar-nav a[href="/process-planning"]',
      '.sidebar-nav a[href="/simulation"]',
      '.sidebar-nav a[href="/toolpath-editor"]',
      '.sidebar-nav a[href="/flywheel-dashboard"]',
      '.sidebar-nav a[href="/approval-dashboard"]',
    ]);
  });

  it("全局引导 finish 后链式启动流程引导", async () => {
    wrapper = mountApp();
    const tours = wrapper.findAllComponents(Tour);

    tours[0].vm.$emit("finish");
    await flushPromises();
    vi.advanceTimersByTime(1000);
    await flushPromises();

    // 流程引导遮罩出现在 body（Tour teleport 到 body）
    expect(document.querySelector(".tour-overlay")).not.toBeNull();
  });

  it("流程引导已完成时不再重复启动", async () => {
    localStorage.setItem("tour_completed_v1", "true");
    localStorage.setItem(FLOW_KEY, "true");
    wrapper = mountApp();
    const tours = wrapper.findAllComponents(Tour);

    tours[0].vm.$emit("finish");
    await flushPromises();
    vi.advanceTimersByTime(2000);
    await flushPromises();

    expect(document.querySelector(".tour-overlay")).toBeNull();
  });

  it("老用户（全局引导已完成）启动时补放流程引导", async () => {
    localStorage.setItem("tour_completed_v1", "true");
    wrapper = mountApp();

    await flushPromises();
    vi.advanceTimersByTime(2000);
    await flushPromises();

    expect(document.querySelector(".tour-overlay")).not.toBeNull();
  });

  it("流程引导 finish 写入完成标记", async () => {
    wrapper = mountApp();
    const tours = wrapper.findAllComponents(Tour);

    tours[1].vm.$emit("finish");
    await flushPromises();

    expect(localStorage.getItem(FLOW_KEY)).toBe("true");
  });
});
