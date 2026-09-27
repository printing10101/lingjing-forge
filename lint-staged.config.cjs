// 根级 lint-staged：提交时对暂存文件做格式/静态检查（前端在 engineering/ 有独立工具链，
// 其 eslint/prettier 仅作用于 engineering 内文件，未在此根级挂钩，避免跨包解析依赖）。
module.exports = {
  // Python：ruff（黑盒化/接线后全仓已 ruff 现代化；pyproject.toml 提供 [tool.ruff] 配置）
  // 范围对齐 CI 门禁（pr.yml 仅 `ruff check app/`）：tests/ 等目录存在刻意的
  // sys.path 注入式导入（E402），且不在 CI lint 范围内，不做提交期检查。
  'engineering/python/app/**/*.py': ['ruff check --fix', 'ruff format'],
  // Rust：cargo fmt（不编译，仅格式化）。
  // 注意 husky 从仓库根运行 lint-staged，实际生效的是本文件；根目录既无 src-tauri/
  // 也无根级 .rs，原 `'*.rs': ['cargo fmt --']` 永远匹配不到任何文件。规则改为指向两个
  // 真实 workspace，并使用函数任务以避免 lint-staged 追加文件名——实测字符串任务会追加、
  // 函数任务不会，而 cargo fmt 按 crate 工作、不接受逐文件参数。
  // rust/compute 是虚拟 workspace（无自身 target），需 --all 才会遍历成员 crate。
  'engineering/src-tauri/**/*.rs': () =>
    'cargo fmt --manifest-path engineering/src-tauri/Cargo.toml',
  'rust/**/*.rs': () =>
    'cargo fmt --manifest-path rust/compute/Cargo.toml --all'
}
