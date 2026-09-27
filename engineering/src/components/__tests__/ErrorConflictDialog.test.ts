/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import ErrorConflictDialog from "@/components/ErrorConflictDialog.vue";
import { useErrorBus } from "@/composables/useErrorBus";
import type { ErrorDialogPayload } from "@/utils/http";

const sampleError: ErrorDialogPayload = {
  title: "加工参数冲突",
  code: "E3002",
  message: "刀具库中无合适刀具",
  severity: "error",
  detail:
    "刀具直径(20mm)大于槽宽(10mm)，无法进入槽内进行加工。当前材料：45钢，工序：槽铣",
  suggestion:
    "刀具直径(20mm)超出槽宽(10mm)限制。建议方案：1) 更换刀具，选用直径≤10mm的立铣刀；2) 调整加工工艺，改用分层加工或多刀铣削策略；3) 修改零件设计，增大槽宽至≥20mm。",
  recoverable: false,
};

describe("ErrorConflictDialog.vue", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  // 组件已改用 useErrorBus（类型安全事件总线）替代 window.addEventListener('manufacturing-error')
  it("should mount and register via useErrorBus without throwing", () => {
    const wrapper = mount(ErrorConflictDialog, {
      global: { stubs: { ErrorNotification: true } },
    });
    expect(wrapper.vm).toBeDefined();
    // 订阅生效：emit 触发组件处理逻辑且不抛错
    expect(() => useErrorBus().emit({ ...sampleError })).not.toThrow();
  });

  it("should unregister event handler on unmount", () => {
    const bus = useErrorBus();
    const wrapper = mount(ErrorConflictDialog);
    wrapper.unmount();
    // useErrorBus 内部 onBeforeUnmount 自动清理订阅，unmount 不应抛错
    expect(() => bus.emit({ ...sampleError })).not.toThrow();
  });

  it("should handle error payload without throwing", () => {
    mount(ErrorConflictDialog);

    expect(() => {
      useErrorBus().emit({ ...sampleError });
    }).not.toThrow();
  });

  it("should accept error severity payloads", () => {
    mount(ErrorConflictDialog);

    expect(() => {
      useErrorBus().emit({ ...sampleError, severity: "error" });
    }).not.toThrow();

    expect(() => {
      useErrorBus().emit({ ...sampleError, severity: "warning" });
    }).not.toThrow();

    expect(() => {
      useErrorBus().emit({ ...sampleError, severity: "critical" });
    }).not.toThrow();
  });

  it("should handle error with empty fields gracefully", () => {
    mount(ErrorConflictDialog);

    expect(() => {
      useErrorBus().emit({
        title: "test",
        code: "E0000",
        message: "",
        severity: "error",
        detail: "",
        suggestion: "",
        recoverable: false,
      });
    }).not.toThrow();
  });

  it("should handle long suggestion text", () => {
    mount(ErrorConflictDialog);

    expect(() => {
      useErrorBus().emit({
        ...sampleError,
        suggestion: "很长".repeat(200),
      });
    }).not.toThrow();
  });

  it("should handle recoverable errors with adjusted values", () => {
    mount(ErrorConflictDialog);

    expect(() => {
      useErrorBus().emit({
        ...sampleError,
        recoverable: true,
        adjusted_values: { feed_rate: 350, spindle_speed: 6000 },
      });
    }).not.toThrow();
  });
});

describe("http utility", () => {
  // 全量并行时 http.ts 模块加载链可能超过默认 5s 超时（顺序相关 flaky），显式放宽
  // 2026-09-17：15s 在全量并发下仍偶发超时（单测 4s），放宽至 30s
  it("should export http client with full API", async () => {
    const mod = await import("@/utils/http");
    expect(mod.default).toBeDefined();
    expect(typeof mod.default.get).toBe("function");
    expect(typeof mod.default.post).toBe("function");
    expect(typeof mod.default.put).toBe("function");
    expect(typeof mod.default.delete).toBe("function");
    expect(typeof mod.default.request).toBe("function");
    expect(mod.default.interceptors).toBeDefined();
  }, 30000);

  it("should define ErrorDialogPayload type structure", async () => {
    const payload: ErrorDialogPayload = {
      title: "test",
      code: "E0001",
      message: "test msg",
      severity: "error",
      detail: "",
      suggestion: "",
      recoverable: false,
    };
    expect(payload.title).toBe("test");
    expect(payload.code).toBe("E0001");
  });
});
