"""方言注册表：扫描本地插件目录，编译声明并注册到 PostProcessorRegistry。

对应 docs/development/postprocessor-方言声明化设计.md §3.2/§3.5：
- 扫描 ``<plugin_root>/*/dialect.yaml``
- 解析 extends 继承链（当前一层），编译为方言类
- 注册到 ``PostProcessorRegistry``（register 钩子已存在，registry.py:83）
- 查询优先级：方言注册表优先，未命中回退内置类 → ``load_from_config`` 调用方零改动
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.postprocessor.base import BasePostProcessor
from app.postprocessor.dialect.compiler import DialectCompiler, DialectCompileError
from app.postprocessor.dialect.declaration import (
    DialectDeclaration,
    DialectDeclarationError,
)
from app.postprocessor.registry import PostProcessorRegistry
from app.postprocessor.dialect._lifecycle import (
    DialectLifecycleStage,
    next_stage_after_failure,
    next_stage_after_success,
)

logger = logging.getLogger(__name__)

# 默认本地方言插件根目录（相对仓库根；可通过构造参数覆盖）
DEFAULT_DIALECT_PLUGIN_DIR = "postprocessor-plugins"


class DialectRegistry:
    """本地方言插件注册表。

    生命周期：
    - ``discover()``：扫描插件目录，加载并校验声明
    - ``compile_all()``：编译全部声明为方言类
    - ``register_to()``：注册到 PostProcessorRegistry（含参数默认值）

    使用示例::

        registry = DialectRegistry(plugin_root="postprocessor-plugins")
        count = registry.discover()
        registry.compile_all()
        registry.register_to(PostProcessorRegistry())
    """

    def __init__(
        self,
        plugin_root: str | Path | None = None,
        compiler: DialectCompiler | None = None,
    ) -> None:
        self.plugin_root = Path(plugin_root) if plugin_root else Path(DEFAULT_DIALECT_PLUGIN_DIR)
        self.compiler = compiler or DialectCompiler()
        self._declarations: dict[str, DialectDeclaration] = {}
        self._compiled_classes: dict[str, type[BasePostProcessor]] = {}
        self._compile_errors: dict[str, str] = {}
        self._stages: dict[str, DialectLifecycleStage] = {}

    # 发现 / 编译 / 注册

    def discover(self) -> list[str]:
        """扫描插件目录下的方言声明，返回发现的方言 id 列表。

        单目录声明失败仅记录错误并跳过（与其他插件失败降级策略一致），
        不阻断整体发现。
        """
        self._declarations.clear()
        self._compile_errors.clear()

        if not self.plugin_root.exists():
            logger.warning("方言插件目录不存在: %s", self.plugin_root)
            return []

        found: list[str] = []
        for item in sorted(self.plugin_root.iterdir()):
            if not item.is_dir():
                continue
            declaration_path = item / "dialect.yaml"
            if not declaration_path.exists():
                logger.debug("跳过非方言目录: %s（无 dialect.yaml）", item)
                continue
            try:
                declaration = DialectDeclaration.from_yaml(declaration_path)
            except DialectDeclarationError as e:
                logger.error("方言声明加载失败: %s", e)
                self._compile_errors[item.name] = str(e)
                continue
            self._declarations[declaration.id] = declaration
            self._stages[declaration.id] = DialectLifecycleStage.DISCOVERED
            found.append(declaration.id)
            logger.info("发现方言: %s v%s (extends=%s)", declaration.id, declaration.version, declaration.extends)

        return found

    def compile_all(self) -> dict[str, type[BasePostProcessor]]:
        """编译全部已发现声明为方言类。

        hooks 模块的导入路径解析：插件目录本身作为包根加入 ``sys.path``
        （如 ``heidenhain_tnc640.hooks`` 相对插件根解析，PEP 420 命名空间
        包，无需 ``__init__.py``）。已存在则不重复插入。

        Returns:
            {方言 id: 编译后的类}（仅成功项）

        Raises:
            DialectCompileError: 任一声明编译失败（fail-fast，声明错误必须暴露）
        """
        # hooks 模块以插件根为包根导入（如 heidenhain_tnc640.hooks），
        # 编译前确保插件根在 sys.path 上（PEP 420 命名空间包，无需 __init__.py）
        plugin_root_str = str(self.plugin_root.resolve())
        if plugin_root_str not in sys.path:
            sys.path.insert(0, plugin_root_str)

        for dialect_id, declaration in self._declarations.items():
            try:
                self._compiled_classes[dialect_id] = self.compiler.compile(declaration)
                self._stages[dialect_id] = next_stage_after_success(
                    self._stages.get(dialect_id, DialectLifecycleStage.DISCOVERED), "compile"
                )
            except DialectCompileError as e:
                logger.error("方言编译失败: %s", e)
                self._compile_errors[dialect_id] = str(e)
                self._stages[dialect_id] = next_stage_after_failure(
                    self._stages.get(dialect_id, DialectLifecycleStage.DISCOVERED)
                )
                raise
        return dict(self._compiled_classes)

    def register_to(self, target: PostProcessorRegistry | None = None) -> int:
        """把编译后的方言注册到 PostProcessorRegistry。

        Args:
            target: 目标注册表；None 时使用 PostProcessorRegistry 单例

        Returns:
            成功注册的方言数

        Raises:
            DialectCompileError: 存在未编译的声明（应先调用 compile_all）
        """
        registry = target or PostProcessorRegistry()

        # 确保已编译
        uncompile = [i for i in self._declarations if i not in self._compiled_classes]
        if uncompile:
            raise DialectCompileError(f"存在未编译的方言声明: {uncompile}。建议操作：先调用 compile_all()。")

        count = 0
        for dialect_id, cls in self._compiled_classes.items():
            # 方言注册优先：覆盖同 id 的内置注册（register 允许覆盖）
            registry.register(dialect_id, cls)
            self._stages[dialect_id] = next_stage_after_success(
                self._stages.get(dialect_id, DialectLifecycleStage.COMPILED), "register"
            )
            count += 1
            logger.info("方言已注册到 PostProcessorRegistry: %s -> %s", dialect_id, cls.__name__)
        return count

    # 查询

    def list_dialects(self) -> list[str]:
        """列出已发现（含编译失败）的方言 id。"""
        return sorted(self._declarations.keys())

    def get_declaration(self, dialect_id: str) -> DialectDeclaration | None:
        return self._declarations.get(dialect_id)

    def unregister(self, dialect_id: str, target: PostProcessorRegistry | None = None) -> bool:
        """卸载方言（P4-2 生命周期：REGISTERED → UNREGISTERED）。"""
        registry = target or PostProcessorRegistry()
        if hasattr(registry, "unregister"):
            try:
                registry.unregister(dialect_id)
            except Exception as exc:
                logger.warning("方言卸载失败: %s", exc)
                return False
        else:
            logger.warning("PostProcessorRegistry 不支持 unregister，仅更新状态")
        self._stages[dialect_id] = next_stage_after_success(
            self._stages.get(dialect_id, DialectLifecycleStage.REGISTERED), "unregister"
        )
        logger.info("方言已卸载: %s", dialect_id)
        return True

    def lifecycle_status(self, dialect_id: str) -> str:
        """查询方言生命周期状态（P4-2）。"""
        stage = self._stages.get(dialect_id)
        return stage.value if stage else "unknown"

    def get_compile_errors(self) -> dict[str, str]:
        """返回 {方言 id/目录名: 错误信息}（含加载与编译失败）。"""
        return dict(self._compile_errors)


def load_dialects(
    plugin_root: str | Path | None = None,
    target: PostProcessorRegistry | None = None,
) -> int:
    """便捷入口：发现 + 编译 + 注册一次完成。

    Args:
        plugin_root: 本地方言插件根目录（默认 postprocessor-plugins/）
        target: 目标注册表（默认 PostProcessorRegistry 单例）

    Returns:
        成功注册的方言数
    """
    registry = DialectRegistry(plugin_root=plugin_root)
    registry.discover()
    registry.compile_all()
    return registry.register_to(target)


__all__ = ["DEFAULT_DIALECT_PLUGIN_DIR", "DialectRegistry", "load_dialects"]
