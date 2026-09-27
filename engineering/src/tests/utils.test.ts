import { describe, it, expect, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";

describe("vitest 基础设施验证", () => {
  describe("vitest 断言工作", () => {
    it("expect.toBe 断言正常", () => {
      expect(1 + 1).toBe(2);
      expect("hello").toBe("hello");
    });

    it("expect.toEqual 断言正常", () => {
      expect({ a: 1, b: 2 }).toEqual({ a: 1, b: 2 });
    });

    it("expect.toBeTruthy/toBeFalsy 断言正常", () => {
      expect(true).toBeTruthy();
      expect(false).toBeFalsy();
      expect(0).toBeFalsy();
      expect(1).toBeTruthy();
    });
  });

  describe("vi.fn() mock 工作", () => {
    it("mock 函数可记录调用", () => {
      const mockFn = vi.fn();
      mockFn("arg1", "arg2");
      expect(mockFn).toHaveBeenCalledTimes(1);
      expect(mockFn).toHaveBeenCalledWith("arg1", "arg2");
    });

    it("mock 函数可指定返回值", () => {
      const mockFn = vi.fn(() => 42);
      expect(mockFn()).toBe(42);
      expect(mockFn()).toBe(42);
      expect(mockFn).toHaveBeenCalledTimes(2);
    });

    it("mock 函数可使用 mockReturnValue", () => {
      const mockFn = vi.fn();
      mockFn.mockReturnValue("mocked");
      expect(mockFn()).toBe("mocked");
    });
  });

  describe("Tauri invoke mock 工作", () => {
    it("invoke 已被 mock 为返回空对象的 Promise", async () => {
      const result = await invoke("some_command");
      expect(result).toEqual({});
    });

    it("invoke 是 vi.fn 实例", () => {
      expect(vi.isMockFunction(invoke)).toBe(true);
    });

    it("invoke 可被测试覆盖返回值", async () => {
      vi.mocked(invoke).mockResolvedValueOnce({ custom: "data" });
      const result = await invoke("custom_command");
      expect(result).toEqual({ custom: "data" });
    });
  });
});
