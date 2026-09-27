import { describe, it, expect, vi, beforeEach } from "vitest";
import http from "@/utils/http";
import {
  listProviders,
  getRegistryStatus,
  getActiveProvider,
  getProviderTypes,
  getCapabilities,
  getProvider,
  listProviderModels,
  checkProviderHealth,
  previewAutoDetect,
  importDetected,
  createProvider,
  updateProvider,
  deleteProvider,
  activateProvider,
  setProviderEnabled,
  testProvider,
  getRouterStatus,
  getRoutingStrategies,
  PROVIDER_TYPE_META,
  ROUTING_STRATEGY_META,
} from "@/api/llmProviders";
import { API_CONFIG } from "@/config/api";
import type {
  LLMProvider,
  RegistryStatus,
  DetectPreviewResponse,
  HealthCheckResult,
  ModelInfo,
  RouterStatus,
  RoutingStrategyInfo,
  ProviderTypeInfo,
  ProviderCapability,
  ChatTestRequest,
  ChatTestResponse,
  ProviderUpsertRequest,
} from "@/types/llmProvider";

vi.mock("@/utils/http", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const BASE = API_CONFIG.LLM_PROVIDERS;

function makeProvider(overrides: Partial<LLMProvider> = {}): LLMProvider {
  return {
    provider_id: "ollama-1",
    name: "Ollama 本地",
    provider_type: "ollama",
    base_url: "http://127.0.0.1:11434",
    api_key_set: false,
    default_model: "llama3",
    timeout: 30000,
    max_retries: 3,
    retry_delay: 500,
    enabled: true,
    is_active: true,
    priority: 1,
    capabilities: ["chat", "streaming"],
    extra: {},
    ...overrides,
  };
}

describe("llmProviders API", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  describe("listProviders", () => {
    it("直接返回数组时正确解析", async () => {
      const providers = [
        makeProvider(),
        makeProvider({ provider_id: "openai-1" }),
      ];
      vi.mocked(http.get).mockResolvedValueOnce({ data: providers });

      const result = await listProviders();

      expect(http.get).toHaveBeenCalledWith(BASE);
      expect(result).toEqual(providers);
      expect(result).toHaveLength(2);
    });

    it("返回 { data: [...] } 结构时正确解析", async () => {
      const providers = [makeProvider()];
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: providers } });

      const result = await listProviders();

      expect(result).toEqual(providers);
    });

    it("返回 { providers: [...] } 结构时正确解析", async () => {
      const providers = [makeProvider()];
      vi.mocked(http.get).mockResolvedValueOnce({ data: { providers } });

      const result = await listProviders();

      expect(result).toEqual(providers);
    });

    it("返回空对象时返回空数组", async () => {
      vi.mocked(http.get).mockResolvedValueOnce({ data: {} });

      const result = await listProviders();

      expect(result).toEqual([]);
    });

    it("返回 null 时返回空数组", async () => {
      vi.mocked(http.get).mockResolvedValueOnce({ data: null });

      const result = await listProviders();

      expect(result).toEqual([]);
    });

    it("网络错误时抛出异常", async () => {
      const error = new Error("网络错误");
      vi.mocked(http.get).mockRejectedValueOnce(error);

      await expect(listProviders()).rejects.toThrow("网络错误");
    });
  });

  describe("getRegistryStatus", () => {
    it("从 resp.data.data 提取状态", async () => {
      const status: RegistryStatus = {
        total: 5,
        enabled: 3,
        active_count: 1,
        local_count: 4,
        cloud_count: 1,
        active_provider_id: "ollama-1",
        encryption_available: true,
        db_path: "/data/registry.db",
      };
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: status } });

      const result = await getRegistryStatus();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/status`);
      expect(result).toEqual(status);
    });

    it("回退到 resp.data", async () => {
      const status: RegistryStatus = {
        total: 1,
        enabled: 1,
        active_count: 1,
        local_count: 1,
        cloud_count: 0,
        active_provider_id: null,
        encryption_available: false,
        db_path: "/tmp.db",
      };
      vi.mocked(http.get).mockResolvedValueOnce({ data: status });

      const result = await getRegistryStatus();

      expect(result).toEqual(status);
    });

    it("请求失败时抛出异常", async () => {
      vi.mocked(http.get).mockRejectedValueOnce(new Error("500"));

      await expect(getRegistryStatus()).rejects.toThrow("500");
    });
  });

  describe("getActiveProvider", () => {
    it("返回当前激活的 Provider", async () => {
      const provider = makeProvider({ is_active: true });
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: provider } });

      const result = await getActiveProvider();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/active`);
      expect(result).toEqual(provider);
    });

    it("无激活 Provider 时返回 null", async () => {
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: null } });

      const result = await getActiveProvider();

      expect(result).toBeNull();
    });
  });

  describe("getProviderTypes", () => {
    it("返回 Provider 类型列表", async () => {
      const types: ProviderTypeInfo[] = [
        PROVIDER_TYPE_META.ollama,
        PROVIDER_TYPE_META.openai,
      ];
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: types } });

      const result = await getProviderTypes();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/types`);
      expect(result).toEqual(types);
    });
  });

  describe("getCapabilities", () => {
    it("返回能力标签列表", async () => {
      const caps: ProviderCapability[] = ["chat", "streaming", "vision"];
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: caps } });

      const result = await getCapabilities();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/capabilities`);
      expect(result).toEqual(caps);
    });
  });

  describe("getProvider", () => {
    it("使用 encodeURIComponent 编码 providerId", async () => {
      const provider = makeProvider();
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: provider } });

      const result = await getProvider("ollama/1");

      expect(http.get).toHaveBeenCalledWith(`${BASE}/ollama%2F1`);
      expect(result).toEqual(provider);
    });

    it("Provider 不存在时返回 null", async () => {
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: null } });

      const result = await getProvider("not-exist");

      expect(result).toBeNull();
    });

    it("普通 ID 正常工作", async () => {
      const provider = makeProvider();
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: provider } });

      await getProvider("ollama-1");

      expect(http.get).toHaveBeenCalledWith(`${BASE}/ollama-1`);
    });
  });

  describe("listProviderModels", () => {
    it("返回指定 Provider 的模型列表", async () => {
      const models: ModelInfo[] = [
        { id: "llama3", name: "Llama 3", owned_by: "meta" },
        { id: "qwen2", name: "Qwen 2", owned_by: "alibaba" },
      ];
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: models } });

      const result = await listProviderModels("ollama-1");

      expect(http.get).toHaveBeenCalledWith(`${BASE}/ollama-1/models`);
      expect(result).toEqual(models);
    });

    it("当 data.data 为空时回退到空数组", async () => {
      vi.mocked(http.get).mockResolvedValueOnce({ data: {} });

      const result = await listProviderModels("ollama-1");

      expect(result).toEqual([]);
    });

    it("当 resp.data 为 null 时返回空数组", async () => {
      vi.mocked(http.get).mockResolvedValueOnce({ data: null });

      const result = await listProviderModels("ollama-1");

      expect(result).toEqual([]);
    });
  });

  describe("checkProviderHealth", () => {
    it("返回健康检查结果", async () => {
      const health: HealthCheckResult = {
        provider_id: "ollama-1",
        healthy: true,
        latency_ms: 42,
        status: "ok",
        checked_at: "2025-01-01T00:00:00Z",
      };
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: health } });

      const result = await checkProviderHealth("ollama-1");

      expect(http.get).toHaveBeenCalledWith(`${BASE}/ollama-1/health`);
      expect(result).toEqual(health);
    });

    it("回退到 resp.data", async () => {
      const health: HealthCheckResult = {
        provider_id: "ollama-1",
        healthy: false,
        latency_ms: null,
        status: "error",
        error: "connection refused",
        checked_at: "2025-01-01T00:00:00Z",
      };
      vi.mocked(http.get).mockResolvedValueOnce({ data: health });

      const result = await checkProviderHealth("ollama-1");

      expect(result).toEqual(health);
    });
  });

  describe("previewAutoDetect", () => {
    it("返回探测预览结果", async () => {
      const preview: DetectPreviewResponse = {
        detected: [],
        scanned_count: 5,
        detected_count: 0,
        duration_ms: 1234,
      };
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: preview } });

      const result = await previewAutoDetect();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/detect/preview`);
      expect(result).toEqual(preview);
    });
  });

  describe("importDetected", () => {
    it("返回导入结果", async () => {
      const importResult = { imported: 2, activated: "ollama-1" };
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: importResult },
      });

      const result = await importDetected();

      expect(http.post).toHaveBeenCalledWith(`${BASE}/detect/import`);
      expect(result).toEqual(importResult);
    });

    it("activated 为 null 时正确返回", async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { imported: 0, activated: null } },
      });

      const result = await importDetected();

      expect(result.imported).toBe(0);
      expect(result.activated).toBeNull();
    });
  });

  describe("createProvider", () => {
    it("成功创建 Provider", async () => {
      const payload: ProviderUpsertRequest = {
        provider_id: "new-1",
        name: "New",
        provider_type: "ollama",
      };
      const created = makeProvider({ provider_id: "new-1" });
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: created } });

      const result = await createProvider(payload);

      expect(http.post).toHaveBeenCalledWith(BASE, payload);
      expect(result).toEqual(created);
    });

    it("创建失败时抛出异常", async () => {
      const payload: ProviderUpsertRequest = {
        provider_id: "dup",
        name: "Dup",
        provider_type: "ollama",
      };
      vi.mocked(http.post).mockRejectedValueOnce(new Error("已存在"));

      await expect(createProvider(payload)).rejects.toThrow("已存在");
    });
  });

  describe("updateProvider", () => {
    it("使用 PUT 方法更新 Provider", async () => {
      const payload = { name: "Updated", api_key: "secret" };
      const updated = makeProvider({ name: "Updated" });
      vi.mocked(http.put).mockResolvedValueOnce({ data: { data: updated } });

      const result = await updateProvider("ollama-1", payload);

      expect(http.put).toHaveBeenCalledWith(`${BASE}/ollama-1`, payload);
      expect(result).toEqual(updated);
    });

    it("api_key 为 null 时也允许更新", async () => {
      const payload = { api_key: null } as never;
      const updated = makeProvider();
      vi.mocked(http.put).mockResolvedValueOnce({ data: { data: updated } });

      await updateProvider("ollama-1", payload);

      expect(http.put).toHaveBeenCalledWith(`${BASE}/ollama-1`, {
        api_key: null,
      });
    });

    it("编码特殊字符的 providerId", async () => {
      const updated = makeProvider();
      vi.mocked(http.put).mockResolvedValueOnce({ data: { data: updated } });

      await updateProvider("id with space", { name: "x" });

      expect(http.put).toHaveBeenCalledWith(`${BASE}/id%20with%20space`, {
        name: "x",
      });
    });
  });

  describe("deleteProvider", () => {
    it("调用 DELETE 方法删除 Provider", async () => {
      vi.mocked(http.delete).mockResolvedValueOnce({ data: {} });

      await deleteProvider("ollama-1");

      expect(http.delete).toHaveBeenCalledWith(`${BASE}/ollama-1`);
    });

    it("删除失败时抛出异常", async () => {
      vi.mocked(http.delete).mockRejectedValueOnce(new Error("403"));

      await expect(deleteProvider("ollama-1")).rejects.toThrow("403");
    });

    it("编码特殊字符", async () => {
      vi.mocked(http.delete).mockResolvedValueOnce({ data: {} });

      await deleteProvider("id/slash");

      expect(http.delete).toHaveBeenCalledWith(`${BASE}/id%2Fslash`);
    });
  });

  describe("activateProvider", () => {
    it("调用 POST 激活指定 Provider", async () => {
      const activated = makeProvider({ is_active: true });
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: activated } });

      const result = await activateProvider("ollama-1");

      expect(http.post).toHaveBeenCalledWith(`${BASE}/ollama-1/activate`);
      expect(result).toEqual(activated);
    });
  });

  describe("setProviderEnabled", () => {
    it("启用 Provider", async () => {
      const enabled = makeProvider({ enabled: true });
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: enabled } });

      const result = await setProviderEnabled("ollama-1", true);

      expect(http.post).toHaveBeenCalledWith(`${BASE}/ollama-1/enable`, {
        enabled: true,
      });
      expect(result).toEqual(enabled);
    });

    it("禁用 Provider", async () => {
      const disabled = makeProvider({ enabled: false });
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: disabled } });

      await setProviderEnabled("ollama-1", false);

      expect(http.post).toHaveBeenCalledWith(`${BASE}/ollama-1/enable`, {
        enabled: false,
      });
    });
  });

  describe("testProvider", () => {
    it("返回测试调用结果", async () => {
      const payload: ChatTestRequest = {
        messages: [{ role: "user", content: "hi" }],
        max_tokens: 100,
      };
      const response: ChatTestResponse = {
        provider_id: "ollama-1",
        content: "hello",
        model: "llama3",
        finish_reason: "stop",
        usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 },
        latency_ms: 120,
      };
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: response } });

      const result = await testProvider("ollama-1", payload);

      expect(http.post).toHaveBeenCalledWith(`${BASE}/ollama-1/test`, payload);
      expect(result).toEqual(response);
    });

    it("回退到 resp.data", async () => {
      const response: ChatTestResponse = {
        provider_id: "ollama-1",
        content: "hello",
        model: "llama3",
        finish_reason: "stop",
        usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 },
        latency_ms: 120,
      };
      vi.mocked(http.post).mockResolvedValueOnce({ data: response });

      const result = await testProvider("ollama-1", { messages: [] });

      expect(result).toEqual(response);
    });
  });

  describe("getRouterStatus", () => {
    it("返回路由器状态", async () => {
      const status: RouterStatus = {
        current_strategy: "active_only",
        active_provider_id: "ollama-1",
        available_providers: 3,
        total_latency_samples: 100,
        cache_hit_rate: 0.5,
        fallback_chain: ["ollama-1", "openai-1"],
      };
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: status } });

      const result = await getRouterStatus();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/router/status`);
      expect(result).toEqual(status);
    });
  });

  describe("getRoutingStrategies", () => {
    it("返回路由策略列表", async () => {
      const strategies: RoutingStrategyInfo[] = [
        { value: "active_only", label: "仅激活", description: "desc" },
      ];
      vi.mocked(http.get).mockResolvedValueOnce({ data: { data: strategies } });

      const result = await getRoutingStrategies();

      expect(http.get).toHaveBeenCalledWith(`${BASE}/router/strategies`);
      expect(result).toEqual(strategies);
    });
  });

  describe("PROVIDER_TYPE_META 常量", () => {
    it("包含所有 provider 类型", () => {
      // 2026-09-13：新增 llama（llama-server 独立端口 8081）后共 13 类
      expect(Object.keys(PROVIDER_TYPE_META)).toHaveLength(13);
      expect(PROVIDER_TYPE_META.ollama).toBeDefined();
      expect(PROVIDER_TYPE_META.openai).toBeDefined();
      expect(PROVIDER_TYPE_META.anthropic).toBeDefined();
      expect(PROVIDER_TYPE_META.gemini).toBeDefined();
      expect(PROVIDER_TYPE_META.openai_compatible).toBeDefined();
      expect(PROVIDER_TYPE_META.llama).toBeDefined();
    });

    it("ollama 类型字段正确", () => {
      const ollama = PROVIDER_TYPE_META.ollama;
      expect(ollama.value).toBe("ollama");
      expect(ollama.label).toBe("Ollama");
      expect(ollama.category).toBe("local");
      expect(ollama.default_base_url).toBe("http://127.0.0.1:11434");
      expect(ollama.needs_api_key).toBe(false);
      expect(ollama.default_capabilities).toContain("chat");
    });

    it("openai 类型字段正确", () => {
      const openai = PROVIDER_TYPE_META.openai;
      expect(openai.category).toBe("cloud");
      expect(openai.needs_api_key).toBe(true);
      expect(openai.default_base_url).toBe("https://api.openai.com/v1");
      expect(openai.default_capabilities).toContain("function_calling");
      expect(openai.default_capabilities).toContain("vision");
    });

    it("openai_compatible 默认 base_url 为空字符串", () => {
      expect(PROVIDER_TYPE_META.openai_compatible.default_base_url).toBe("");
      expect(PROVIDER_TYPE_META.openai_compatible.needs_api_key).toBe(false);
    });

    it("所有条目都有必需字段", () => {
      for (const key of Object.keys(PROVIDER_TYPE_META)) {
        const meta = PROVIDER_TYPE_META[key as keyof typeof PROVIDER_TYPE_META];
        expect(meta.value).toBe(key);
        expect(typeof meta.label).toBe("string");
        expect(["local", "cloud"]).toContain(meta.category);
        expect(Array.isArray(meta.default_capabilities)).toBe(true);
        expect(typeof meta.needs_api_key).toBe("boolean");
        expect(typeof meta.description).toBe("string");
      }
    });
  });

  describe("ROUTING_STRATEGY_META 常量", () => {
    it("包含 6 种路由策略", () => {
      expect(ROUTING_STRATEGY_META).toHaveLength(6);
    });

    it("每个策略有正确的字段", () => {
      for (const strategy of ROUTING_STRATEGY_META) {
        expect(typeof strategy.value).toBe("string");
        expect(typeof strategy.label).toBe("string");
        expect(typeof strategy.description).toBe("string");
      }
    });

    it("包含 active_only 策略", () => {
      const active = ROUTING_STRATEGY_META.find(
        (s) => s.value === "active_only",
      );
      expect(active).toBeDefined();
      expect(active?.label).toBe("仅激活 Provider");
    });

    it("包含 local_first 与 cloud_first 策略", () => {
      expect(ROUTING_STRATEGY_META.some((s) => s.value === "local_first")).toBe(
        true,
      );
      expect(ROUTING_STRATEGY_META.some((s) => s.value === "cloud_first")).toBe(
        true,
      );
    });
  });
});
