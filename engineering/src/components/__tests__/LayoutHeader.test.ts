import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, VueWrapper } from "@vue/test-utils";
import LayoutHeader from "@/components/layout/LayoutHeader.vue";
import zhCN from "@/locales/zh-CN";
import en from "@/locales/en";

vi.mock("vue-i18n", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

const pushSpy = vi.hoisted(() => vi.fn());
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushSpy }),
}));

// 搜索面板是独立组件且有专项测试，这里打桩聚焦通知行为
vi.mock("@/components/layout/HeaderSearch.vue", () => ({
  default: {
    name: "HeaderSearch",
    template: '<div class="mock-header-search" />',
  },
}));

vi.mock("@/components/BackendStatusIndicator.vue", () => ({
  default: { name: "BackendStatusIndicator", template: "<div />" },
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({
    user: { username: "operator" },
    userRole: "operator",
    isAdmin: () => false,
    logout: vi.fn(),
  }),
}));

vi.mock("@/config/api", () => ({
  API_CONFIG: { V1: "/api/v1" },
  buildApiPath: (_v: string, p: string) => `/api/v1${p}`,
}));

vi.mock("@/utils/error-handler", () => ({
  extractErrorMessage: vi.fn((e: unknown) => String(e)),
}));

// 通知数据由 http.get 返回，用例内按需覆盖
const mockHttpGet = vi.hoisted(() => vi.fn());
vi.mock("@/utils/http", () => ({
  default: { get: mockHttpGet },
}));

// 仅拦截命令式 API；模板里的 el-* 由 src/tests/setup.ts 的全局 stub 渲染
vi.mock("element-plus", () => ({
  ElMessageBox: { confirm: vi.fn() },
}));

vi.mock("@element-plus/icons-vue", () => ({
  Refresh: { template: "<i />" },
  Bell: { template: "<i />" },
  Folder: { template: "<i />" },
  DocumentAdd: { template: "<i />" },
  FolderOpened: { template: "<i />" },
  Document: { template: "<i />" },
  CopyDocument: { template: "<i />" },
  Download: { template: "<i />" },
  Upload: { template: "<i />" },
  DocumentCopy: { template: "<i />" },
  SwitchButton: { template: "<i />" },
  ArrowDown: { template: "<i />" },
  QuestionFilled: { template: "<i />" },
  Guide: { template: "<i />" },
  MapLocation: { template: "<i />" },
}));

function mountHeader(): VueWrapper {
  return mount(LayoutHeader, {
    props: { projectName: "", isModified: false },
  });
}

function resolveNotifications(
  items: Array<{ title: string; priority: string; created_at: number }>,
) {
  mockHttpGet.mockResolvedValue({ data: { code: 0, data: items } });
}

/** 挂载并等通知请求落地：两个微任务后模板已完成首次响应式渲染 */
async function mountWithNotifications(
  items: Parameters<typeof resolveNotifications>[0],
) {
  resolveNotifications(items);
  const wrapper = mountHeader();
  await wrapper.vm.$nextTick();
  await wrapper.vm.$nextTick();
  return wrapper;
}

describe("LayoutHeader 通知中心", () => {
  beforeEach(() => {
    mockHttpGet.mockReset();
  });

  it("无通知时展示空态且不亮红点", async () => {
    const wrapper = await mountWithNotifications([]);
    expect(wrapper.find('[data-testid="notification-empty"]').exists()).toBe(
      true,
    );
    expect(wrapper.find(".notification-dot").exists()).toBe(false);
  });

  it("存在未读时亮红点，「全部已读」后红点消失", async () => {
    const wrapper = await mountWithNotifications([
      { title: "任务完成", priority: "low", created_at: Date.now() / 1000 },
      { title: "设备告警", priority: "high", created_at: Date.now() / 1000 },
    ]);
    expect(wrapper.find(".notification-dot").exists()).toBe(true);

    await wrapper.find(".el-button").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".notification-dot").exists()).toBe(false);
  });

  it("点击单条通知即标记已读，读完后红点消失", async () => {
    const wrapper = await mountWithNotifications([
      { title: "任务完成", priority: "low", created_at: Date.now() / 1000 },
    ]);
    expect(wrapper.find(".notification-dot").exists()).toBe(true);

    await wrapper.find(".notification-item").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".notification-dot").exists()).toBe(false);
  });
});

describe("LayoutHeader 通知轮询与角色标签", () => {
  beforeEach(() => {
    mockHttpGet.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("每 60s 轮询一次通知，组件卸载后停止", async () => {
    vi.useFakeTimers();
    resolveNotifications([]);
    const wrapper = mountHeader();

    await vi.advanceTimersByTimeAsync(0); // 初始拉取落地
    expect(mockHttpGet).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(mockHttpGet).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(mockHttpGet).toHaveBeenCalledTimes(3);

    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(mockHttpGet).toHaveBeenCalledTimes(3);
  });

  it("已知角色在两种语言下都有文案", () => {
    const zhRoles = (zhCN.appLayout as { roles: Record<string, string> }).roles;
    const enRoles = (en.appLayout as { roles: Record<string, string> }).roles;
    for (const role of ["admin", "operator", "viewer", "guest"]) {
      expect(zhRoles[role]).toBeTruthy();
      expect(enRoles[role]).toBeTruthy();
    }
  });

  it("未知角色回退显示原始标识而非空白", async () => {
    // vue-i18n mock 的 t 直接返回 key 本身，命中 roleLabel 的回退分支
    const wrapper = await mountWithNotifications([]);
    expect(wrapper.find(".user-role-tag").text()).toBe("operator");
  });
});
