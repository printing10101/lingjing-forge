import { describe, it, expect } from "vitest";
import { buildDocumentTitle, APP_BASE_TITLE } from "../title";

describe("buildDocumentTitle", () => {
  it("有 meta.title 时输出「页面名 - 品牌名」", () => {
    expect(buildDocumentTitle("工艺规划")).toBe(`工艺规划 - ${APP_BASE_TITLE}`);
  });

  it("meta.title 缺失或非字符串时仅输出品牌名", () => {
    expect(buildDocumentTitle(undefined)).toBe(APP_BASE_TITLE);
    expect(buildDocumentTitle("")).toBe(APP_BASE_TITLE);
    expect(buildDocumentTitle(123)).toBe(APP_BASE_TITLE);
  });

  it("重定向路由（query 参数）不误伤标题", () => {
    expect(buildDocumentTitle("任务看板")).toBe(`任务看板 - ${APP_BASE_TITLE}`);
  });
});
