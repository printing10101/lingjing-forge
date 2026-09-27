import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, VueWrapper } from "@vue/test-utils";
import ParamSliderPanel from "@/components/nl2cad/ParamSliderPanel.vue";

// Mock vue-i18n
vi.mock("vue-i18n", () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

// Mock @/api/nl2cad
const mockRegenerateModelParams = vi.hoisted(() => vi.fn());
vi.mock("@/api/nl2cad", () => ({
  regenerateModelParams: (...args: unknown[]) =>
    mockRegenerateModelParams(...args),
}));

// ElSlider 替身：原生 input range，change 时上报数值。
// 组件显式 import { ElMessage, ElSlider }，模板里的 <el-slider> 解析到本替身
//（无 Wrapper 二次包装，事件直连）；vi.hoisted 保证先于 mock 工厂初始化。
const SliderStub = vi.hoisted(() => ({
  template:
    '<input class="el-slider" type="range" :min="min" :max="max" :step="step" :disabled="disabled" :value="modelValue" @change="$emit(\'update:modelValue\', Number($event.target.value))" />',
  props: [
    "modelValue",
    "min",
    "max",
    "step",
    "disabled",
    "size",
    "showTooltip",
  ],
  emits: ["update:modelValue"],
}));

vi.mock("element-plus", () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  ElSlider: SliderStub,
}));

const SCRIPT =
  'length = 50\nwidth = 30\nresult = cq.Workplane("XY").box(length, width)';

describe("ParamSliderPanel", () => {
  let wrapper: VueWrapper;

  function mountPanel(props?: {
    script?: string;
    parameters?: Record<string, number>;
  }) {
    return mount(ParamSliderPanel, {
      props: {
        script: props?.script ?? SCRIPT,
        parameters: props?.parameters ?? { length: 50, width: 30 },
      },
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    mockRegenerateModelParams.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
  });

  it("renders one slider per parameter", () => {
    wrapper = mountPanel();
    const rows = wrapper.findAll(".param-slider-panel__row");
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain("length");
    expect(wrapper.text()).toContain("width");
  });

  it("renders nothing when parameters are empty (honest degradation)", () => {
    wrapper = mount(ParamSliderPanel, {
      props: { script: SCRIPT, parameters: {} },
    });
    expect(wrapper.find(".param-slider-panel").exists()).toBe(false);
  });

  it("derives slider bounds from current value (±50%)", () => {
    wrapper = mountPanel();
    // 第一个参数是 length=50 → min=25、max=75
    const slider = wrapper.find("input.el-slider");
    expect(slider.attributes("min")).toBe("25");
    expect(slider.attributes("max")).toBe("75");
  });

  it("debounces and calls regenerate API, then emits regenerated", async () => {
    wrapper = mountPanel();
    mockRegenerateModelParams.mockResolvedValue({
      model_path: "/tmp/model_v2.step",
      params: { length: 60, width: 30 },
    });

    const slider = wrapper.find("input.el-slider");
    await slider.setValue(60);
    expect(mockRegenerateModelParams).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(500);
    expect(mockRegenerateModelParams).toHaveBeenCalledTimes(1);
    expect(mockRegenerateModelParams).toHaveBeenCalledWith({
      script: SCRIPT,
      params: { length: 60, width: 30 },
    });
    expect(wrapper.emitted("regenerated")?.[0]?.[0]).toBe("/tmp/model_v2.step");
  });

  it("coalesces rapid slider changes into one API call", async () => {
    wrapper = mountPanel();
    mockRegenerateModelParams.mockResolvedValue({
      model_path: "/tmp/m.step",
      params: { length: 70, width: 30 },
    });

    const slider = wrapper.find("input.el-slider");
    await slider.setValue(60);
    await vi.advanceTimersByTimeAsync(100);
    await slider.setValue(70);
    await vi.advanceTimersByTimeAsync(500);

    expect(mockRegenerateModelParams).toHaveBeenCalledTimes(1);
  });

  it("re-queues changes made while a request is in flight (no silent drop)", async () => {
    wrapper = mountPanel();
    let resolveFirst!: (v: {
      model_path: string;
      params: Record<string, number>;
    }) => void;
    mockRegenerateModelParams.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    const secondCall = {
      model_path: "/tmp/m2.step",
      params: { length: 60, width: 45 },
    };
    mockRegenerateModelParams.mockImplementationOnce(() =>
      Promise.resolve(secondCall),
    );

    const sliders = wrapper.findAll("input.el-slider");
    // 第 1 次拖动 → 防抖后发出第 1 个请求（挂起不返回）
    await sliders[0].setValue(60);
    await vi.advanceTimersByTimeAsync(500);
    expect(mockRegenerateModelParams).toHaveBeenCalledTimes(1);

    // 请求 in-flight 期间滑杆值再次变化（真实浏览器存在禁用生效前的
    // 亚帧窗口，DOM 层 :disabled 挡住的部分不在本测试范围）：
    // 直接驱动组件状态 + scheduleRegenerate，防抖到期后应早返回且
    // 不得清除脏标记
    const vm = wrapper.vm as unknown as {
      entries: Array<{ value: number }>;
      scheduleRegenerate: () => void;
    };
    vm.entries[1].value = 45;
    vm.scheduleRegenerate();
    await vi.advanceTimersByTimeAsync(500);
    expect(mockRegenerateModelParams).toHaveBeenCalledTimes(1);

    // 第 1 个请求返回后，脏变更自动重新排队发出，且携带最新滑杆值。
    // 先清微任务让 await 续体（finally → scheduleRegenerate）跑完，
    // 再推进定时器触发重排队的防抖
    resolveFirst({
      model_path: "/tmp/m1.step",
      params: { length: 60, width: 30 },
    });
    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(500);
    await Promise.resolve();
    await Promise.resolve();
    expect(mockRegenerateModelParams).toHaveBeenCalledTimes(2);
    expect(mockRegenerateModelParams).toHaveBeenLastCalledWith({
      script: SCRIPT,
      params: { length: 60, width: 45 },
    });
    // 第 1 次 emit 是挂起请求的返回，第 2 次才是重排队请求的返回
    expect(wrapper.emitted("regenerated")?.[1]?.[0]).toBe("/tmp/m2.step");
  });

  it("shows error message but does not throw when API fails", async () => {
    const { ElMessage } = await import("element-plus");
    wrapper = mountPanel();
    mockRegenerateModelParams.mockRejectedValue(new Error("boom"));

    const slider = wrapper.find("input.el-slider");
    await slider.setValue(60);
    await vi.advanceTimersByTimeAsync(500);

    expect(ElMessage.error).toHaveBeenCalled();
    expect(wrapper.emitted("regenerated")).toBeUndefined();
  });
});
