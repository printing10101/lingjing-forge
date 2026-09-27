import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, VueWrapper } from "@vue/test-utils";
import { createI18n } from "vue-i18n";
import HeaderSearch from "@/components/layout/HeaderSearch.vue";
import zhCN from "@/locales/zh-CN";

const pushSpy = vi.hoisted(() => vi.fn());
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushSpy }),
}));

// 图标用真实组件：element-plus 图标是轻量 SVG 组件，happy-dom 可直接渲染

const i18n = createI18n({
  legacy: false,
  locale: "zh-CN",
  messages: { "zh-CN": zhCN },
});

async function mountSearch() {
  // attachTo 使 focus()/activeElement 在 happy-dom 中真实生效
  const wrapper = mount(HeaderSearch, {
    global: { plugins: [i18n] },
    attachTo: document.body,
  });
  const input = wrapper.find('[data-testid="header-search-input"]');
  await input.trigger("focus");
  return { wrapper, input };
}

describe("HeaderSearch 全局快速搜索", () => {
  let wrapper: VueWrapper | null = null;

  beforeEach(() => {
    pushSpy.mockClear();
    document.body.innerHTML = "";
  });

  it("聚焦后面板展示功能页面与快捷操作两组", async () => {
    ({ wrapper } = await mountSearch());
    const panel = wrapper.find('[data-testid="header-search-panel"]');
    expect(panel.exists()).toBe(true);
    // 页面项来自 navGroups，快捷操作来自工程文件命令
    expect(
      wrapper.find('[data-testid="search-item-page-/simulation"]').exists(),
    ).toBe(true);
    expect(wrapper.find('[data-testid="search-item-cmd-new"]').exists()).toBe(
      true,
    );
  });

  it("输入关键词后仅保留匹配项", async () => {
    ({ wrapper } = await mountSearch());
    const input = wrapper.find('[data-testid="header-search-input"]');
    await input.setValue("仿真");
    expect(
      wrapper.find('[data-testid="search-item-page-/simulation"]').exists(),
    ).toBe(true);
    expect(wrapper.find('[data-testid="search-item-cmd-new"]').exists()).toBe(
      false,
    );
  });

  it("无匹配时展示空态文案", async () => {
    ({ wrapper } = await mountSearch());
    const input = wrapper.find('[data-testid="header-search-input"]');
    await input.setValue("不存在的功能zzz");
    expect(wrapper.find('[data-testid="header-search-empty"]').exists()).toBe(
      true,
    );
  });

  it("Enter 执行当前选中项：跳转对应页面", async () => {
    ({ wrapper } = await mountSearch());
    const input = wrapper.find('[data-testid="header-search-input"]');
    await input.setValue("仿真");
    await input.trigger("keydown", { key: "Enter" });
    expect(pushSpy).toHaveBeenCalledWith("/simulation");
  });

  it("点击快捷操作项时向外转发 file-command", async () => {
    ({ wrapper } = await mountSearch());
    await wrapper.find('[data-testid="search-item-cmd-new"]').trigger("click");
    expect(wrapper.emitted("file-command")?.[0]).toEqual(["new"]);
    expect(pushSpy).not.toHaveBeenCalled();
  });

  it("Escape 关闭面板", async () => {
    ({ wrapper } = await mountSearch());
    const input = wrapper.find('[data-testid="header-search-input"]');
    await input.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[data-testid="header-search-panel"]').exists()).toBe(
      false,
    );
  });

  it("点击面板外部关闭面板", async () => {
    ({ wrapper } = await mountSearch());
    document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="header-search-panel"]').exists()).toBe(
      false,
    );
  });

  it("Ctrl+K 全局快捷键聚焦输入框并展开面板", async () => {
    ({ wrapper } = await mountSearch());
    // 先 Escape 收起，模拟"面板未展开"的初始状态
    const input = wrapper.find('[data-testid="header-search-input"]');
    await input.trigger("keydown", { key: "Escape" });
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", ctrlKey: true }),
    );
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="header-search-panel"]').exists()).toBe(
      true,
    );
    expect(document.activeElement?.contains(input.element) ?? false).toBe(true);
  });
});
