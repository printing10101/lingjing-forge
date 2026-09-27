import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, VueWrapper } from "@vue/test-utils";
import NLInputPanel from "@/components/nl2cad/NLInputPanel.vue";

// Mock vue-i18n
vi.mock("vue-i18n", () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

// Mock @element-plus/icons-vue
vi.mock("@element-plus/icons-vue", () => ({
  ChatDotRound: { name: "ChatDotRound", template: "<i />" },
  User: { name: "User", template: "<i />" },
  Check: { name: "Check", template: "<i />" },
  Edit: { name: "Edit", template: "<i />" },
  View: { name: "View", template: "<i />" },
  Download: { name: "Download", template: "<i />" },
  Box: { name: "Box", template: "<i />" },
  Promotion: { name: "Promotion", template: "<i />" },
}));

// Mock @/api/nl2cad
const mockExtractParams = vi.hoisted(() => vi.fn());
const mockGenerateModel = vi.hoisted(() => vi.fn());
vi.mock("@/api/nl2cad", () => ({
  extractParams: (...args: unknown[]) => mockExtractParams(...args),
  generateModel: (...args: unknown[]) => mockGenerateModel(...args),
}));

// Mock element-plus
vi.mock("element-plus", () => ({
  ElInput: {
    template:
      '<textarea class="el-input" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ["modelValue", "type", "rows", "placeholder", "disabled"],
    emits: ["update:modelValue"],
  },
  ElButton: {
    template:
      '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
    props: ["type", "loading", "disabled", "icon", "size"],
    emits: ["click"],
  },
  ElIcon: {
    template: '<span class="el-icon"><slot /></span>',
    props: ["size"],
  },
  ElProgress: {
    template: '<div class="el-progress" />',
    props: ["percentage", "color", "strokeWidth"],
  },
  ElTag: {
    template:
      '<span class="el-tag" @click="$emit(\'click\', $event)"><slot /></span>',
    props: ["size", "type"],
    emits: ["click"],
  },
  ElDialog: {
    template:
      '<div class="el-dialog" v-if="modelValue"><slot /><slot name="footer" /></div>',
    props: ["modelValue", "title", "width"],
    emits: ["update:modelValue"],
  },
  ElForm: {
    template: '<form class="el-form"><slot /></form>',
    props: ["model", "labelWidth"],
  },
  ElFormItem: {
    template: '<div class="el-form-item"><slot /></div>',
    props: ["label"],
  },
  ElSelect: {
    template:
      '<select class="el-select" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
    props: ["modelValue"],
    emits: ["update:modelValue"],
  },
  ElOption: {
    template: '<option class="el-option" />',
    props: ["label", "value"],
  },
  ElInputNumber: {
    template: '<input class="el-input-number" />',
    props: ["modelValue", "min", "max"],
  },
  ElMessage: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

describe("NLInputPanel.vue", () => {
  let wrapper: VueWrapper<any>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockExtractParams.mockResolvedValue({
      params: {
        shape_type: "box",
        dimensions: { length: 10, width: 5, height: 3 },
        confidence: 0.9,
      },
    });
    mockGenerateModel.mockResolvedValue({
      model_path: "/tmp/model.stl",
      params: { shape_type: "box" },
    });
  });

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount();
    }
  });

  const mountComponent = () => {
    wrapper = mount(NLInputPanel, {
      global: {
        stubs: {
          transition: false,
        },
      },
    });
    return wrapper;
  };

  describe("组件挂载", () => {
    it("应该正确挂载组件", () => {
      mountComponent();
      expect(wrapper.exists()).toBe(true);
      expect(wrapper.find(".nl-input-panel").exists()).toBe(true);
    });

    it("应该渲染聊天容器", () => {
      mountComponent();
      expect(wrapper.find(".chat-container").exists()).toBe(true);
    });

    it("应该渲染欢迎消息", () => {
      mountComponent();
      expect(
        wrapper.findAll(".assistant-message").length,
      ).toBeGreaterThanOrEqual(1);
    });

    it("应该渲染输入区域", () => {
      mountComponent();
      expect(wrapper.find(".input-area").exists()).toBe(true);
    });

    it("应该渲染示例提示标签", () => {
      mountComponent();
      const tags = wrapper.findAll(".input-hints .el-tag");
      expect(tags.length).toBe(3);
    });
  });

  describe("formatTime 方法", () => {
    it("应返回字符串类型", () => {
      mountComponent();
      const result = wrapper.vm.formatTime(new Date());
      expect(typeof result).toBe("string");
    });
  });

  describe("handleSend 方法", () => {
    it("空输入不应发送", async () => {
      mountComponent();
      wrapper.vm.userInput = "   ";
      await wrapper.vm.handleSend();
      expect(mockExtractParams).not.toHaveBeenCalled();
      expect(wrapper.vm.messages.length).toBe(0);
    });

    it("loading 状态下不应重复发送", async () => {
      mountComponent();
      wrapper.vm.userInput = "一个长方体";
      wrapper.vm.loading = true;
      await wrapper.vm.handleSend();
      expect(mockExtractParams).not.toHaveBeenCalled();
    });

    it("成功时应添加用户消息和参数消息", async () => {
      mountComponent();
      wrapper.vm.userInput = "一个长方体";
      await wrapper.vm.handleSend();
      expect(mockExtractParams).toHaveBeenCalledWith({
        description: "一个长方体",
      });
      expect(wrapper.vm.messages.length).toBe(2);
      expect(wrapper.vm.messages[0].role).toBe("user");
      expect(wrapper.vm.messages[0].content).toBe("一个长方体");
      expect(wrapper.vm.messages[1].role).toBe("assistant");
      expect(wrapper.vm.messages[1].type).toBe("params");
    });

    it("成功后应清空 userInput", async () => {
      mountComponent();
      wrapper.vm.userInput = "一个长方体";
      await wrapper.vm.handleSend();
      expect(wrapper.vm.userInput).toBe("");
    });

    it("成功后 loading 应为 false", async () => {
      mountComponent();
      wrapper.vm.userInput = "一个长方体";
      await wrapper.vm.handleSend();
      expect(wrapper.vm.loading).toBe(false);
    });

    it("API 失败时应添加错误消息", async () => {
      mockExtractParams.mockRejectedValueOnce(new Error("网络错误"));
      mountComponent();
      wrapper.vm.userInput = "一个长方体";
      await wrapper.vm.handleSend();
      expect(wrapper.vm.messages.length).toBe(2);
      expect(wrapper.vm.messages[1].type).toBe("text");
      expect(wrapper.vm.messages[1].content).toBe(
        "nlInputPanel.errorUnderstand",
      );
      expect(wrapper.vm.loading).toBe(false);
    });

    it("API 返回空 params 时应使用空对象", async () => {
      mockExtractParams.mockResolvedValueOnce({});
      mountComponent();
      wrapper.vm.userInput = "一个长方体";
      await wrapper.vm.handleSend();
      expect(wrapper.vm.messages[1].params).toEqual({});
    });
  });

  describe("handleConfirmParams 方法", () => {
    it("params 为空时不应执行", async () => {
      mountComponent();
      await wrapper.vm.handleConfirmParams(undefined);
      expect(mockGenerateModel).not.toHaveBeenCalled();
    });

    it("成功时应添加模型消息并触发 model-generated 事件", async () => {
      mountComponent();
      // 先添加一条用户消息以便 handleConfirmParams 获取 description
      wrapper.vm.messages.push({
        id: "msg-1",
        role: "user",
        content: "一个长方体",
        timestamp: new Date(),
      });
      await wrapper.vm.handleConfirmParams({
        shape_type: "box",
        confidence: 0.9,
      });
      expect(mockGenerateModel).toHaveBeenCalled();
      expect(wrapper.vm.messages.length).toBe(2);
      expect(wrapper.vm.messages[1].type).toBe("model");
      expect(wrapper.vm.messages[1].modelPath).toBe("/tmp/model.stl");
      expect(wrapper.emitted("model-generated")).toBeTruthy();
      // 4 元组：(modelPath, params, script, scriptParams)——后两项供滑杆面板调参
      expect(wrapper.emitted("model-generated")![0]).toEqual([
        "/tmp/model.stl",
        { shape_type: "box" },
        "",
        {},
      ]);
      expect(wrapper.vm.loading).toBe(false);
    });

    it("API 失败时应添加错误消息", async () => {
      mockGenerateModel.mockRejectedValueOnce(new Error("生成失败"));
      mountComponent();
      await wrapper.vm.handleConfirmParams({ shape_type: "box" });
      const lastMsg = wrapper.vm.messages[wrapper.vm.messages.length - 1];
      expect(lastMsg.type).toBe("text");
      expect(lastMsg.content).toBe("nlInputPanel.errorGenerateFailed");
      expect(wrapper.vm.loading).toBe(false);
    });
  });

  describe("handleEditParams 方法", () => {
    it("应打开编辑对话框并填充 editParams", () => {
      mountComponent();
      const params = { shape_type: "box", dimensions: { length: 10 } };
      wrapper.vm.handleEditParams(params);
      expect(wrapper.vm.showParamDialog).toBe(true);
      expect(wrapper.vm.editParams.shape_type).toBe("box");
    });

    it("params 为空时不应打开对话框", () => {
      mountComponent();
      wrapper.vm.handleEditParams(undefined);
      expect(wrapper.vm.showParamDialog).toBe(false);
    });
  });

  describe("confirmEditedParams 方法", () => {
    it("应更新最后一条参数消息", () => {
      mountComponent();
      wrapper.vm.messages.push({
        id: "msg-1",
        role: "assistant",
        content: "",
        timestamp: new Date(),
        type: "params",
        params: { shape_type: "box" },
      });
      wrapper.vm.showParamDialog = true;
      wrapper.vm.confirmEditedParams({ shape_type: "cylinder" });
      expect(wrapper.vm.showParamDialog).toBe(false);
      expect(wrapper.vm.messages[0].params.shape_type).toBe("cylinder");
    });

    it("没有参数消息时不应抛出错误", () => {
      mountComponent();
      expect(() => {
        wrapper.vm.showParamDialog = true;
        wrapper.vm.confirmEditedParams({ shape_type: "box" });
      }).not.toThrow();
      expect(wrapper.vm.showParamDialog).toBe(false);
    });
  });

  describe("handleView3D 方法", () => {
    it("应触发 view-3d 事件", () => {
      mountComponent();
      wrapper.vm.handleView3D("/tmp/model.stl");
      expect(wrapper.emitted("view-3d")).toBeTruthy();
      expect(wrapper.emitted("view-3d")![0]).toEqual(["/tmp/model.stl"]);
    });

    it("modelPath 为空时不应触发事件", () => {
      mountComponent();
      wrapper.vm.handleView3D(undefined);
      expect(wrapper.emitted("view-3d")).toBeFalsy();
    });
  });

  describe("handleDownload 方法", () => {
    it("有 modelPath 时应触发下载", () => {
      mountComponent();
      const clickSpy = vi.fn();
      const originalCreate = document.createElement;
      vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
        const el = originalCreate.call(document, tag);
        el.click = clickSpy;
        return el;
      });
      wrapper.vm.handleDownload("/tmp/model.stl");
      expect(clickSpy).toHaveBeenCalled();
      vi.restoreAllMocks();
    });

    it("modelPath 为空时不应触发下载", () => {
      mountComponent();
      const createElementSpy = vi.spyOn(document, "createElement");
      wrapper.vm.handleDownload(undefined);
      expect(createElementSpy).not.toHaveBeenCalled();
      createElementSpy.mockRestore();
    });
  });

  describe("加载状态渲染", () => {
    it("loading 为 true 时应显示打字指示器", async () => {
      mountComponent();
      wrapper.vm.loading = true;
      await wrapper.vm.$nextTick();
      expect(wrapper.find(".typing-indicator").exists()).toBe(true);
    });

    it("loading 为 false 时不应显示打字指示器", async () => {
      mountComponent();
      wrapper.vm.loading = false;
      await wrapper.vm.$nextTick();
      expect(wrapper.find(".typing-indicator").exists()).toBe(false);
    });
  });
});
