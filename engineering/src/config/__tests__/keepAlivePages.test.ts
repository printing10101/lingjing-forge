import { describe, it, expect } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { KEEP_ALIVE_PAGES } from "../keepAlivePages";

// vitest 根目录是 engineering/，file 相对 src/ 解析
const srcDir = resolve(process.cwd(), "src");

describe("keepAlivePages 白名单", () => {
  it("组件 name 唯一（include 列表按名匹配，重复无意义）", () => {
    const names = KEEP_ALIVE_PAGES.map((page) => page.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it("每个 name 与文件名一致且文件存在（拼写错误会让缓存静默失效）", () => {
    for (const page of KEEP_ALIVE_PAGES) {
      expect(page.name).toBe(basename(page.file, ".vue"));
      expect(
        existsSync(resolve(srcDir, page.file)),
        `文件不存在: ${page.file}`,
      ).toBe(true);
    }
  });

  it("目标 SFC 未用 defineOptions 覆盖组件名（name 依赖文件名推断的前提）", () => {
    for (const page of KEEP_ALIVE_PAGES) {
      const content = readFileSync(resolve(srcDir, page.file), "utf-8");
      expect(
        content.includes("defineOptions"),
        `${page.file} 定义了 defineOptions，name 推断假设失效`,
      ).toBe(false);
    }
  });
});
