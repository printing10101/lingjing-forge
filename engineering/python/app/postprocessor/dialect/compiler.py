"""方言编译器：声明 + 模板 → 参数化 BasePostProcessor 子类。

对应 docs/development/postprocessor-方言声明化设计.md §3.3-§3.5：
- 解析 extends 继承链（当前仅一层：extends 内置方言）
- 模板方法 → Jinja2 渲染函数替换（签名与基类方法一致，调用方零感知）
- 未声明模板的方法继承基类实现
- 可选 hooks（代码钩子，默认无，远期预留）

模板安全：Jinja2 受限命名空间（白名单上下文 + fmt/comment 过滤器），
模板只读处理器公开状态，不暴露任意 Python。
"""

from __future__ import annotations

import functools
import inspect
import logging
from pathlib import Path
from typing import Any
from collections.abc import Callable

from jinja2 import Environment, TemplateError, pass_context

from app.postprocessor.base import BasePostProcessor
from app.postprocessor.dialect.declaration import DialectDeclaration

logger = logging.getLogger(__name__)


class DialectCompileError(Exception):
    """方言编译失败。"""


# 内置基类方言映射（extends 解析用）


def _load_builtin_dialect_classes() -> dict[str, type[BasePostProcessor]]:
    """加载内置方言类（与 PostProcessorRegistry 内置注册对齐）。"""
    from app.postprocessor import (  # noqa: PLC0415  # 延迟导入避免循环
        fagor,
        fanuc,
        gsk,
        heidenhain,
        hnc,
        knd,
        mitsubishi,
        siemens,
        xmachine,
    )

    return {
        "fanuc_0i": fanuc.FanucPostProcessor,
        "siemens_840d": siemens.SiemensPostProcessor,
        "heidenhain_tnc": heidenhain.HeidenhainPostProcessor,
        "gsk_980_25i": gsk.GSKPostProcessor,
        "hnc_848_22": hnc.HNCPostProcessor,
        "knd_1000_2000_3000": knd.KNDPostProcessor,
        "mitsubishi_m70_m80": mitsubishi.MitsubishiPostProcessor,
        "fagor_8055": fagor.FagorPostProcessor,
        "xmachine_xm100": xmachine.XMachineXM100PostProcessor,
    }


class DialectCompiler:
    """把方言声明编译为可实例化的 BasePostProcessor 子类。

    编译产物：
    - 纯声明（无模板、无 hooks）→ 直接返回基类（参数由 params 在实例化时传入）
    - 含模板 → 动态生成子类，模板方法替换为渲染函数
    - 含 hooks → 预留（当前报错，避免半成品）
    """

    def __init__(self) -> None:
        self._env = Environment(
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # 白名单过滤器：模板只能用这些，不暴露任意 Python 函数
        self._env.filters["fmt"] = _fmt_filter
        self._env.filters["comment"] = _comment_filter

    # 编译入口

    def compile(self, declaration: DialectDeclaration) -> type[BasePostProcessor]:
        """编译声明为方言类。

        Args:
            declaration: 已加载并校验的方言声明

        Returns:
            可实例化的 BasePostProcessor 子类（params 自动合并进实例化）

        Raises:
            DialectCompileError: 基类解析失败 / hooks 加载失败 / 模板构造失败
        """
        base_cls = self._resolve_base(declaration)

        if not declaration.templates and not declaration.hooks:
            # 纯参数声明：复用基类，但注入 params 合并的 __init__
            cls = self._build_params_class(declaration, base_cls)
            logger.info("方言编译完成（纯参数）: %s (extends=%s)", declaration.id, declaration.extends)
            return cls

        return self._build_template_class(declaration, base_cls)

    def _build_params_class(
        self,
        declaration: DialectDeclaration,
        base_cls: type[BasePostProcessor],
    ) -> type[BasePostProcessor]:
        """生成带 params 注入的子类（无模板覆盖，仅参数声明）。"""
        namespace: dict[str, Any] = {
            "CONTROLLER_ID": declaration.id,
            "CONTROLLER_NAME": declaration.name,
        }
        return self._with_params_init(declaration, base_cls, namespace)

    def _with_params_init(
        self,
        declaration: DialectDeclaration,
        base_cls: type[BasePostProcessor],
        namespace: dict[str, Any],
    ) -> type[BasePostProcessor]:
        """把 params 合并逻辑注入子类 __init__。

        params 语义与 ConfigLoader 一致：声明 params 作为覆盖层。
        顶层标量参数（safe_z_height / decimal_places / rapid_feed）提升为
        构造参数（这些是 BasePostProcessor.__init__ 的位置参数）；其余
        深合并进 config（供 _init_from_config / limiter 使用）。
        """
        from app.postprocessor._loader import _deep_merge

        params = declaration.params or {}
        # 提升为构造参数的白名单（对应 BasePostProcessor.__init__ 签名）
        scalar_keys = {"safe_z_height", "decimal_places", "rapid_feed"}
        scalar_params = {k: v for k, v in params.items() if k in scalar_keys}

        def __init__(self: BasePostProcessor, *args: Any, **kwargs: Any) -> None:
            # 构造参数：调用方显式传入优先，否则用 params 的标量值
            for key, value in scalar_params.items():
                if key not in kwargs:
                    kwargs[key] = value
            # config：深合并 params 的其余部分 + 标量（保持 config 完整，供 limiter 等读取）
            config = kwargs.get("config") or {}
            merged_config = _deep_merge(dict(config), params)
            if merged_config:
                kwargs["config"] = merged_config
            base_cls.__init__(self, *args, **kwargs)

        cls = type(
            f"CompiledDialect_{declaration.id.replace('-', '_')}",
            (base_cls,),
            {**namespace, "__init__": __init__},
        )
        return cls

    def _resolve_base(self, declaration: DialectDeclaration) -> type[BasePostProcessor]:
        """解析 extends 基类。"""
        if declaration.extends is None:
            raise DialectCompileError(f"方言 '{declaration.id}' 未声明 extends；当前版本要求 extends 一个内置方言。")
        classes = _load_builtin_dialect_classes()
        base = classes.get(declaration.extends)
        if base is None:
            raise DialectCompileError(f"方言 '{declaration.id}' 的 extends='{declaration.extends}' 无法解析。")
        return base

    def _build_template_class(
        self,
        declaration: DialectDeclaration,
        base_cls: type[BasePostProcessor],
    ) -> type[BasePostProcessor]:
        """生成模板/hooks 方法被替换的动态子类（含 params 注入）。

        方法优先级：hooks（代码钩子）> 模板（Jinja2）> 基类继承。
        """
        namespace: dict[str, Any] = {}

        # 1. hooks 方法（代码钩子，最高优先级）：module.path:ClassName
        if declaration.hooks:
            hook_methods = self._load_hook_methods(declaration, base_cls)
            namespace.update(hook_methods)
            logger.info(
                "方言 %s 加载 hooks: %s (方法=%s)",
                declaration.id,
                declaration.hooks,
                sorted(k for k in hook_methods if k.startswith("format_")),
            )

        # 2. 模板方法（Jinja2，次优先级；hooks 同名方法不覆盖）
        for method_name, template_path in declaration.templates.items():
            if method_name in namespace:
                logger.info(
                    "方言 %s 方法 %s 由 hooks 提供，跳过模板覆盖",
                    declaration.id,
                    method_name,
                )
                continue
            renderer = self._create_renderer(declaration, method_name, template_path)
            namespace[method_name] = renderer

        # CONTROLLER_ID / CONTROLLER_NAME 从声明注入（供模板/外部读取）
        namespace["CONTROLLER_ID"] = declaration.id
        namespace["CONTROLLER_NAME"] = declaration.name

        dialect_cls = self._with_params_init(declaration, base_cls, namespace)
        logger.info(
            "方言编译完成: %s (extends=%s, 模板方法=%s)",
            declaration.id,
            declaration.extends,
            sorted(k for k in namespace if k.startswith("format_")),
        )
        return dialect_cls

    def _load_hook_methods(
        self,
        declaration: DialectDeclaration,
        base_cls: type[BasePostProcessor],
    ) -> dict[str, Callable[..., Any]]:
        """加载 hooks entrypoint，提取其 format_* 方法与私有辅助方法。

        hooks 支持两种写法：
        - 单入口字符串 ``module.path:ClassName``（如 ``plugins.my_dialect.hooks:MyHooks``）
        - 多入口列表（Phase E hooks 模式，from_yaml 已归一化为字符串列表）

        提取规则：
        - ``format_*`` 方法：作为方言方法挂到编译子类（同名后声明覆盖先声明）；
        - 单下划线私有辅助方法（如 ``_program_number_safe``）：随 hooks 一并注入，
          使 hooks 自包含；与基类已有方法同名时不注入（防止遮蔽框架内部方法，
          如 _fmt / _next_block），仅记录警告。
        hooks 类自身不实例化（方法以方言实例为 self 调用，可访问 _fmt 等）。
        """
        if not declaration.hooks:
            return {}

        entries = declaration.hooks if isinstance(declaration.hooks, list) else [declaration.hooks]
        hook_methods: dict[str, Callable[..., Any]] = {}
        import importlib

        for entrypoint in entries:
            module_path, _, class_name = entrypoint.partition(":")
            if not module_path or not class_name:
                raise DialectCompileError(
                    f"方言 '{declaration.id}' 的 hooks 格式错误（应为 module.path:ClassName）: {entrypoint}"
                )
            try:
                module = importlib.import_module(module_path)
                hook_cls = getattr(module, class_name)
            except (ImportError, AttributeError) as e:
                raise DialectCompileError(f"方言 '{declaration.id}' 的 hooks 加载失败: {entrypoint} ({e})") from e

            entry_methods: dict[str, Callable[..., Any]] = {}
            helper_methods: dict[str, Callable[..., Any]] = {}
            for name, attr in inspect.getmembers(hook_cls, callable):
                if not name.startswith("__"):
                    # 去绑定：提取原始函数（hooks 方法以方言实例为 self）
                    if name.startswith("format_"):
                        entry_methods[name] = attr
                    elif name.startswith("_"):
                        helper_methods[name] = attr
            if not entry_methods:
                raise DialectCompileError(
                    f"方言 '{declaration.id}' 的 hooks 类 '{class_name}' 未定义任何 format_* 方法。"
                )
            # 同名方法：后声明的 hooks 类覆盖先声明的（声明顺序即优先级）
            hook_methods.update(entry_methods)
            # 私有辅助方法仅注入基类没有的名字（防遮蔽框架内部方法）
            for hname, hfn in helper_methods.items():
                if hasattr(base_cls, hname):
                    logger.warning(
                        "方言 %s 的 hooks 辅助方法 %s 与基类方法同名，忽略注入（防遮蔽）",
                        declaration.id,
                        hname,
                    )
                    continue
                if hname in hook_methods and hname not in entry_methods:
                    continue
                hook_methods.setdefault(hname, hfn)
        return hook_methods

    # 模板渲染器构造

    def _create_renderer(
        self,
        declaration: DialectDeclaration,
        method_name: str,
        template_path: Path,
    ) -> Callable[..., str]:
        """构造一个模板渲染函数，替换方言类的指定方法。

        渲染函数签名与基类方法一致（通过 functools.wraps 复制），
        调用方（如 golden 测试）用完全相同的参数调用，行为透明。
        """
        template = self._env.from_string(template_path.read_text(encoding="utf-8"))
        base_method = self._find_base_method(declaration, method_name)

        if base_method is None:
            raise DialectCompileError(
                f"方言 '{declaration.id}' 模板方法 '{method_name}' 在基类 '{declaration.extends}' 的 MRO 中不存在。"
            )

        signature = inspect.signature(base_method)

        @functools.wraps(base_method)
        def renderer(*args: Any, **kwargs: Any) -> str:
            # 绑定参数：self 已由 Python 传入（renderer 是类上的实例方法）
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            params = {k: v for k, v in bound.arguments.items() if k != "self"}
            context = _build_template_context(declaration, params, args[0] if args else None)
            try:
                return template.render(**context)
            except TemplateError as e:
                logger.error(
                    "方言模板渲染失败 (dialect=%s, method=%s): %s",
                    declaration.id,
                    method_name,
                    e,
                )
                raise DialectCompileError(f"方言 '{declaration.id}' 模板 '{method_name}' 渲染失败: {e}") from e

        return renderer

    def _find_base_method(
        self,
        declaration: DialectDeclaration,
        method_name: str,
    ) -> Callable[..., Any] | None:
        """在基类 MRO 中查找指定方法。"""
        classes = _load_builtin_dialect_classes()
        base = classes.get(declaration.extends or "")
        if base is None:
            return None
        for cls in base.__mro__:
            if method_name in cls.__dict__:
                return cls.__dict__[method_name]
        return None


# 模板上下文与白名单过滤器


def _build_template_context(
    declaration: DialectDeclaration,
    method_params: dict[str, Any],
    processor: BasePostProcessor | None,
) -> dict[str, Any]:
    """构造模板上下文（白名单）。

    可用变量：
    - 方法参数（按名，如 ``program_number``）
    - ``dialect``：声明元信息（id/name/version/extends）
    - ``controller_id`` / ``controller_name``：便捷别名
    - ``decimal_places``：格式化精度
    - ``pp``：处理器实例（模板可读 safe_z_height / rapid_feed / _fmt 等公开状态）
    """
    context: dict[str, Any] = {
        "dialect": {
            "id": declaration.id,
            "name": declaration.name,
            "version": declaration.version,
            "extends": declaration.extends,
        },
        "controller_id": declaration.id,
        "controller_name": declaration.name,
        "decimal_places": getattr(processor, "decimal_places", 3) if processor else 3,
        "pp": processor,
    }
    context.update(method_params)
    return context


@pass_context
def _fmt_filter(ctx: dict[str, Any], value: float) -> str:
    """等价 _format_mixin._fmt：按 decimal_places 格式化数值（从模板上下文读取精度）。"""
    decimal_places = ctx.get("decimal_places", 3)
    return f"{float(value):.{decimal_places}f}"


def _comment_filter(text: str) -> str:
    """等价 _format_mixin._comment：生成 G-code 注释行。"""
    return f"; {text}"


__all__ = ["DialectCompiler", "DialectCompileError"]
