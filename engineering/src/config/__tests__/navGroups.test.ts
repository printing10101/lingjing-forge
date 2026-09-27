import { describe, it, expect } from "vitest";
import { navGroups } from "../navGroups";
import zhCN from "@/locales/zh-CN";
import en from "@/locales/en";

/** 按点分路径取 locale 叶子值，取不到返回 undefined */
function getLocaleValue(
  messages: Record<string, unknown>,
  key: string,
): unknown {
  return key
    .split(".")
    .reduce<unknown>(
      (node, part) =>
        node && typeof node === "object"
          ? (node as Record<string, unknown>)[part]
          : undefined,
      messages,
    );
}

const allItems = navGroups.flatMap((group) => group.items);

describe("navGroups", () => {
  it("每个分组与菜单项的 labelKey 在中英文 locale 中都存在", () => {
    const labelKeys = [
      ...navGroups.map((group) => group.labelKey),
      ...allItems.map((item) => item.labelKey),
    ];
    for (const key of labelKeys) {
      expect(
        getLocaleValue(zhCN as unknown as Record<string, unknown>, key),
        `zh-CN 缺少 ${key}`,
      ).toBeTruthy();
      expect(
        getLocaleValue(en as unknown as Record<string, unknown>, key),
        `en 缺少 ${key}`,
      ).toBeTruthy();
    }
  });

  it("路径不重复且均为站内绝对路径", () => {
    const paths = allItems.map((item) => item.path);
    expect(new Set(paths).size).toBe(paths.length);
    for (const path of paths) {
      expect(path.startsWith("/")).toBe(true);
    }
  });

  it("labelKey 无重复（同一菜单文案不得挂两个入口）", () => {
    const keys = allItems.map((item) => item.labelKey);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("图标不重复（重复图标降低扫读辨识度）", () => {
    const icons = allItems.map((item) => item.icon);
    expect(new Set(icons).size).toBe(icons.length);
  });
});
