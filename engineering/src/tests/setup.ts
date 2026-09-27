// Vitest setup file for frontend test utilities and mocks.

import { config } from "@vue/test-utils";
import { vi } from "vitest";
import { createI18n } from "vue-i18n";
import zhCN from "@/locales/zh-CN";
import en from "@/locales/en";

// 全局 mock Tauri invoke（前端组件可能调用）
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(() => Promise.resolve({})),
}));

// 全局 mock window.matchMedia（Element Plus 依赖）
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addListener: vi.fn(),
    removeListener: vi.fn(),
  }));
}

// Mock localStorage for happy-dom environment
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
    get length() {
      return Object.keys(store).length;
    },
    key: (index: number) => Object.keys(store)[index] || null,
  };
})();

Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
});

// Mock scrollIntoView for happy-dom environment
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function (
    _options?: ScrollIntoViewOptions,
  ) {
    // No-op in test environment
  };
}

// Mock Element Plus components globally
config.global.stubs = {
  ElDialog: {
    template: '<div class="el-dialog"><slot /></div>',
  },
  ElButton: {
    template:
      "<button class=\"el-button\" :disabled=\"disabled\" :class=\"{ 'el-button--primary': type === 'primary', 'el-button--danger': type === 'danger', 'is-loading': loading }\" @click=\"$emit('click', $event)\"><slot /></button>",
    props: ["type", "loading", "disabled", "size", "text", "icon", "circle"],
    emits: ["click"],
  },
  ElUpload: {
    // :accept 渲染：测试断言 .el-upload 的 accept 属性（如 '.step,.stp'）
    template:
      '<div class="el-upload" :accept="accept"><slot /><slot name="tip" /></div>',
    props: ["autoUpload", "limit", "accept", "fileList", "drag"],
  },
  ElIcon: {
    template: '<span class="el-icon"><slot /></span>',
  },
  ElProgress: {
    template: '<div class="el-progress"></div>',
    props: ["percentage", "status", "strokeWidth", "indeterminate"],
  },
  ElAlert: {
    // 渲染 title、关闭按钮与默认 slot：mount 场景断言文本/关闭行为/详情内容需要真实内容
    template:
      '<div class="el-alert"><span class="el-alert__title">{{ title }}</span><button class="el-alert__closebtn" @click="$emit(\'close\')" /><slot /></div>',
    props: ["title", "type", "closable", "showIcon"],
    emits: ["close"],
  },
  ElForm: {
    template: '<form class="el-form"><slot /></form>',
    props: ["labelWidth", "size"],
  },
  ElFormItem: {
    template: '<div class="el-form-item"><slot /></div>',
    props: ["label"],
  },
  ElSelect: {
    // @change 显式 emit：父组件 @change 是组件自定义事件（非原生 DOM 事件）
    template:
      '<select class="el-select" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value); $emit(\'change\')"><slot /></select>',
    props: ["modelValue"],
    emits: ["update:modelValue", "change"],
  },
  ElOption: {
    // 渲染 class 与 value：测试按 .el-option + attributes('value') 断言选项
    template: '<option class="el-option" :value="value">{{ label }}</option>',
    props: ["label", "value"],
  },
  ElRadioGroup: {
    template: '<div class="el-radio-group"><slot /></div>',
    props: ["modelValue", "size"],
  },
  ElRadio: {
    template: '<label class="el-radio"><slot /></label>',
    props: ["value"],
  },
  ElRadioButton: {
    template: '<label class="el-radio-button"><slot /></label>',
    props: ["value"],
  },
  ElTabs: {
    template: '<div class="el-tabs"><slot /></div>',
    props: ["modelValue"],
  },
  ElTabPane: {
    // data-name 透传：测试按 [data-name="overview"] 定位 Tab 内容
    template: '<div class="el-tab-pane" :data-name="name"><slot /></div>',
    props: ["label", "name"],
  },
  ElTable: {
    template: '<table class="el-table"><slot /></table>',
    props: ["data", "height", "stripe", "size"],
  },
  ElTableColumn: {
    // slot 提供 row 默认与 $index：防模板 #default="{ row }" 解构 undefined
    // （row.status 崩溃）与 $index 缺失（localConditions[undefined] 崩溃）
    template:
      '<th class="el-table-column"><slot :row="row" :$index="index" /></th>',
    props: ["prop", "label", "width", "minWidth"],
    data: () => ({ row: {}, index: 0 }),
  },
  ElEmpty: {
    template: '<div class="el-empty"><slot /></div>',
    props: ["description", "imageSize"],
  },
  ElResult: {
    template: '<div class="el-result"><slot name="extra" /></div>',
    props: ["icon", "title", "subTitle"],
  },
  ElDescriptions: {
    template: '<dl class="el-descriptions"><slot /></dl>',
    props: ["column", "border", "size"],
  },
  ElDescriptionsItem: {
    template: '<div class="el-descriptions-item"><slot /></div>',
    props: ["label"],
  },
  ElCheckbox: {
    template:
      '<label class="el-checkbox"><input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" /><span><slot /></span></label>',
    props: ["modelValue"],
    emits: ["update:modelValue", "change"],
  },
  ElSlider: {
    template:
      '<div class="el-slider"><input type="range" :value="modelValue" @input="$emit(\'update:modelValue\', Number($event.target.value))" /></div>',
    props: ["modelValue", "min", "max", "step", "disabled"],
    emits: ["update:modelValue", "input", "change"],
  },
  // ElTag 缺桩会导致大量组件测试报 "Failed to resolve component"
  ElTag: {
    template:
      '<span class="el-tag" :data-type="type" :data-effect="effect"><slot /></span>',
    props: ["type", "effect", "closable", "size", "color"],
    emits: ["close"],
  },
  // Dropdown/Tooltip 家族：通知面板、文件菜单等头部组件的弹层内容需渲染
  ElTooltip: {
    template: '<div class="el-tooltip"><slot /></div>',
    props: ["content", "placement"],
  },
  ElDropdown: {
    template: '<div class="el-dropdown"><slot /><slot name="dropdown" /></div>',
    props: ["trigger", "placement"],
    emits: ["command"],
  },
  ElDropdownMenu: {
    template: '<div class="el-dropdown-menu"><slot /></div>',
  },
  ElDropdownItem: {
    template: '<div class="el-dropdown-item"><slot /></div>',
    props: ["command", "divided", "disabled"],
  },
  ElDivider: {
    template: '<hr class="el-divider" />',
  },
  ElAvatar: {
    template: '<span class="el-avatar"><slot /></span>',
    props: ["size"],
  },
  ElInput: {
    template:
      '<input class="el-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @change="$emit(\'change\')" />',
    props: [
      "modelValue",
      "type",
      "placeholder",
      "disabled",
      "readonly",
      "clearable",
      "size",
    ],
    emits: ["update:modelValue", "input", "change", "clear"],
  },
  ElInputNumber: {
    template:
      '<input class="el-input-number" :value="modelValue" @input="$emit(\'update:modelValue\', Number($event.target.value))" />',
    props: ["modelValue", "min", "max", "step", "disabled"],
    emits: ["update:modelValue", "input", "change"],
  },
  ElRow: {
    template: '<div class="el-row"><slot /></div>',
    props: ["gutter", "type", "justify", "align"],
  },
  ElCol: {
    template: '<div class="el-col"><slot /></div>',
    props: ["span", "offset", "push", "pull", "xs", "sm", "md", "lg", "xl"],
  },
  ElCard: {
    // 对齐真实结构：header/body 包装 div（测试按 .el-card__header 定位按钮）
    template:
      '<div class="el-card"><div class="el-card__header"><slot name="header" /></div><div class="el-card__body"><slot /></div></div>',
    props: ["shadow", "bodyStyle"],
  },
  ElSkeleton: {
    template: '<div class="el-skeleton"><slot /><slot name="template" /></div>',
    props: ["animated", "loading", "rows"],
  },
  ElCollapseTransition: {
    template: '<div class="el-collapse-transition"><slot /></div>',
  },
  // ECharts 封装组件（Radar 等）：chart 渲染在 happy-dom 下不可用，仅验证容器挂载
  Radar: {
    template: '<div class="echarts-radar" />',
    props: ["option", "initOptions", "theme", "autoresize"],
  },
};

// 挂载与生产一致的 i18n（真实 locale 消息；构造调用置于 localStorage mock 之后，
// 因为 createI18n 本身无副作用，但 locales 模块仅含纯数据）
const testI18n = createI18n({
  legacy: false,
  locale: "zh-CN",
  fallbackLocale: "zh-CN",
  messages: { "zh-CN": zhCN, en },
});
config.global.plugins.push(testI18n);

// WebGL 上下文 stub（three.js / canvas 组件测试）
// happy-dom 无 WebGL 实现，getContext 返回 null 会导致 WebGLRenderer 构造崩溃。
// 链式 Proxy：任意方法返回自身（对象 truthy），满足 three.js 的句柄/状态查询协议。
const glContextStub = new Proxy(
  { canvas: null as HTMLCanvasElement | null },
  {
    get: (target, prop) => {
      if (prop === "canvas") return target.canvas;
      if (prop === "getContextAttributes") {
        return () => ({
          alpha: true,
          depth: true,
          stencil: true,
          antialias: true,
          premultipliedAlpha: true,
          preserveDrawingBuffer: true,
        });
      }
      if (!(prop in target)) {
        (target as Record<string | symbol, unknown>)[prop] = () =>
          glContextStub;
      }
      return (target as Record<string | symbol, unknown>)[prop];
    },
  },
);
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function (_contextId?: string) {
    return glContextStub as unknown as CanvasRenderingContext2D | null;
  } as typeof HTMLCanvasElement.prototype.getContext;
}
