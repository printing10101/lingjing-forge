/**
 * 浏览器标签页标题拼接规则：有 meta.title 时输出「页面名 - 灵境制造」，否则仅品牌名。
 * 独立成模块便于单测（router/index.ts 引入即产生带守卫的路由实例，不宜在测试中加载）。
 */
export const APP_BASE_TITLE = "灵境制造";

export function buildDocumentTitle(
  metaTitle: unknown,
  baseTitle: string = APP_BASE_TITLE,
): string {
  if (typeof metaTitle === "string" && metaTitle.length > 0) {
    return `${metaTitle} - ${baseTitle}`;
  }
  return baseTitle;
}
