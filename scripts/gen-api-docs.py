#!/usr/bin/env python3
"""
API 文档自动生成系统

从 FastAPI 路由文件和 Pydantic 模型文件中自动提取 API 端点、请求参数、
响应模型等信息，并填充至 Markdown 模板生成开发者参考文档。

使用方式:
    python scripts/gen-api-docs.py              # 生成完整文档
    python scripts/gen-api-docs.py --dry-run    # 生成临时文档但不覆盖
    python scripts/gen-api-docs.py --validate   # 验证文档是否已同步

依赖: Python 标准库 (ast, pathlib, json, argparse)
"""

import ast
import argparse
import json
import sys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# 数据模型定义


@dataclass
class ParameterInfo:
    """路由参数信息"""

    name: str
    param_type: str
    is_required: bool
    default_value: str = ""
    description: str = ""
    location: str = "body"  # path, query, body


@dataclass
class ResponseInfo:
    """响应信息"""

    status_code: int
    description: str
    model_name: str = ""
    is_success: bool = True


@dataclass
class RouteInfo:
    """路由完整信息"""

    method: str
    path: str
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    parameters: list[ParameterInfo] = field(default_factory=list)
    responses: list[ResponseInfo] = field(default_factory=list)
    request_body_model: str = ""
    file_path: str = ""


@dataclass
class ModelFieldInfo:
    """Pydantic 模型字段信息"""

    name: str
    field_type: str
    is_required: bool
    default_value: str = ""
    description: str = ""
    constraints: list[str] = field(default_factory=list)
    example: str = ""


@dataclass
class PydanticModelInfo:
    """Pydantic 模型完整信息"""

    name: str
    fields: list[ModelFieldInfo] = field(default_factory=list)
    description: str = ""
    file_path: str = ""


# AST 解析器


class FastAPIRouteExtractor(ast.NodeVisitor):
    """FastAPI 路由提取器

    遍历 AST，识别 @router.get/post/put/delete 等装饰器，
    提取路由路径、参数、请求体、响应模型等信息。
    """

    ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

    def __init__(self):
        self.routes: list[RouteInfo] = []
        # 所有 APIRouter 定义：name -> {prefix, tags}
        self._routers: dict[str, dict] = {}
        # include_router 关系：(parent, child, include_prefix)
        self._includes: list[tuple[str, str, str | None]] = []
        # import 别名映射：alias -> (module, original_name)
        self._imports: dict[str, tuple[str, str]] = {}
        # 待生成的路由：(router_name, decorator, function_node)
        self._pending: list[tuple[str, ast.AST, ast.AsyncFunctionDef | ast.FunctionDef]] = []
        self._current_tags: list[str] = []

    def _extract_string(self, node: ast.AST) -> str | None:
        """从 AST 节点提取字符串值"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return ast.dump(node)
        return None

    def visit_Assign(self, node: ast.Assign):
        """识别任意 `NAME = APIRouter(...)` 定义（V2.7.0 后子路由不再重复声明 prefix，
        统一由聚合 router 声明 + include_router 传播，因此必须支持多 router 与任意命名）"""
        for target in node.targets:
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                if self._is_apirouter_call(node.value):
                    prefix = ""
                    tags: list[str] = []
                    for kw in node.value.keywords:
                        if kw.arg == "prefix":
                            prefix = self._extract_string(kw.value) or ""
                        elif kw.arg == "tags":
                            if isinstance(kw.value, ast.List):
                                tags = [self._extract_string(elt) or "" for elt in kw.value.elts]
                    self._routers[target.id] = {"prefix": prefix, "tags": tags}
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """识别 `PARENT.include_router(CHILD, prefix=...)` 前缀传播关系"""
        if isinstance(node.func, ast.Attribute) and node.func.attr == "include_router":
            parent = self._attr_root_name(node.func)
            if parent and node.args:
                child = self._attr_root_name(node.args[0])
                if child:
                    inc_prefix: str | None = None
                    for kw in node.keywords:
                        if kw.arg == "prefix":
                            inc_prefix = self._extract_string(kw.value)
                            break
                    self._includes.append((parent, child, inc_prefix))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """收集 import 别名：`from X import Y as Z` / `from X import Y`"""
        module = node.module or ""
        if node.level:
            module = ("." * node.level) + (module or "")
        for alias in node.names:
            self._imports[alias.asname or alias.name] = (module, alias.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        """收集 import 别名：`import X as Z`"""
        for alias in node.names:
            self._imports[alias.asname or alias.name] = (alias.name, "")
        self.generic_visit(node)

    def resolve_symbol(self, name: str, module: str) -> tuple[str, str]:
        """把符号名解析为全局键 (module, name)。优先查本文件 import 别名；
        相对导入（. / ..）基于当前模块路径展开。"""
        if name in self._imports:
            target_mod, target_name = self._imports[name]
            if target_mod.startswith("."):
                # 相对导入：按当前模块的包层级展开
                base_parts = module.split(".")
                up = len(target_mod) - len(target_mod.lstrip("."))
                pkg = base_parts[: len(base_parts) - up]
                rel = target_mod.lstrip(".")
                if rel:
                    pkg = pkg + rel.split(".")
                resolved_mod = ".".join(pkg) if pkg else module
                return (resolved_mod, target_name)
            return (target_mod, target_name)
        return (module, name)

    @staticmethod
    def _attr_root_name(node: ast.AST) -> str | None:
        """提取 `a.b.c` 链的根变量名（router 名）"""
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id
        return None

    @staticmethod
    def _is_apirouter_call(node: ast.Call) -> bool:
        """判断调用是否为 APIRouter(...) 构造"""
        if isinstance(node.func, ast.Name):
            return node.func.id == "APIRouter"
        return False

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """解析异步函数定义（FastAPI 端点）"""
        self._process_function(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """解析同步函数定义"""
        self._process_function(node)
        self.generic_visit(node)

    def _process_function(self, node: ast.AsyncFunctionDef | ast.FunctionDef):
        """收集函数装饰器信息（实际路由生成在 resolve() 阶段，保证
        include_router 前缀传播先于路由生成）"""
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in self.ROUTE_METHODS:
                continue
            router_name = self._attr_root_name(func)
            if router_name is None:
                continue
            self._pending.append((router_name, decorator, node))

    def resolve(self):
        """传播 include_router 前缀，生成全部路由。"""
        # 迭代传播直到稳定（父 子 孙 链）
        guard = 0
        changed = True
        while changed and guard < 10:
            changed = False
            guard += 1
            for parent, child, inc_prefix in self._includes:
                if parent not in self._routers or child not in self._routers:
                    continue
                parent_prefix = self._routers[parent]["prefix"] or ""
                child_prefix = self._routers[child]["prefix"] or ""
                if inc_prefix is not None:
                    # FastAPI: include_router(prefix=...) 覆盖子路由 prefix
                    new_prefix = parent_prefix + inc_prefix
                else:
                    new_prefix = parent_prefix + child_prefix
                if new_prefix != self._routers[child]["prefix"]:
                    self._routers[child]["prefix"] = new_prefix
                    changed = True

        for router_name, decorator, node in self._pending:
            route_info = self._extract_route_from_decorator(decorator, node, router_name)
            if route_info:
                self.routes.append(route_info)

    def _extract_route_from_decorator(
        self,
        decorator: ast.AST,
        node: ast.AsyncFunctionDef | ast.FunctionDef,
        prefix: str = "",
        tags: list[str] | None = None,
    ) -> RouteInfo | None:
        """从装饰器中提取路由信息（前缀由全局传播阶段解析后传入）"""
        if not isinstance(decorator, ast.Call):
            return None

        func = decorator.func
        method_name = None

        if isinstance(func, ast.Attribute):
            method_name = func.attr
            if method_name not in self.ROUTE_METHODS:
                return None

        if method_name is None:
            return None

        route_path = ""
        if decorator.args:
            route_path = self._extract_string(decorator.args[0]) or ""

        full_path = f"{prefix or ''}{route_path}"
        http_method = method_name.upper()

        route_info = RouteInfo(
            method=http_method,
            path=full_path,
            file_path="",
            tags=list(tags or []),
        )

        for kw in decorator.keywords:
            if kw.arg == "summary":
                route_info.summary = self._extract_string(kw.value) or ""
            elif kw.arg == "description":
                route_info.description = self._extract_string(kw.value) or ""
            elif kw.arg == "tags":
                if isinstance(kw.value, ast.List):
                    route_info.tags = [self._extract_string(elt) or "" for elt in kw.value.elts]

        if not route_info.summary and node.body:
            docstring = ast.get_docstring(node)
            if docstring:
                route_info.summary = docstring.split("\n")[0].strip()

        self._extract_parameters(node, route_info)
        self._extract_responses(route_info)

        return route_info

    def _extract_parameters(self, node: ast.AsyncFunctionDef | ast.FunctionDef, route_info: RouteInfo):
        """从函数参数中提取路由参数信息"""
        for arg in node.args.args:
            arg_name = arg.arg
            if arg_name == "self":
                continue

            annotation = arg.annotation
            arg_type = self._type_annotation_to_str(annotation) or "Any"

            is_required = True
            default_value = ""

            defaults_count = len(node.args.defaults)
            total_args = len(node.args.args)
            arg_index = node.args.args.index(arg)
            default_offset = total_args - defaults_count

            if arg_index >= default_offset:
                default_node = node.args.defaults[arg_index - default_offset]
                default_value = self._extract_default_value(default_node)
                if default_value != "":
                    is_required = False

            if default_value == "" and arg_type.startswith("Optional"):
                is_required = False

            location = "body"
            for kw in getattr(node.args, "kwonlyargs", []):
                if kw.arg == arg_name:
                    location = "query"
                    break

            path_params = re.findall(r"\{(\w+)\}", route_info.path)
            if arg_name in path_params:
                location = "path"
                is_required = True

            if arg_type in ("Request", "BackgroundTasks", "HTTPConnection"):
                continue

            route_info.parameters.append(
                ParameterInfo(
                    name=arg_name,
                    param_type=arg_type,
                    is_required=is_required,
                    default_value=default_value,
                    location=location,
                )
            )

    def _extract_default_value(self, node: ast.AST) -> str:
        """提取参数默认值"""
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.List):
            return "[]"
        if isinstance(node, ast.Dict):
            return "{}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                return f"{func.id}(...)"
            if isinstance(func, ast.Attribute):
                return f"{func.attr}(...)"
            return "..."
        return ""

    def _extract_responses(self, route_info: RouteInfo):
        """提取响应信息（基于代码分析）"""
        route_info.responses.append(
            ResponseInfo(
                status_code=200,
                description="成功响应",
                is_success=True,
            )
        )
        route_info.responses.append(
            ResponseInfo(
                status_code=400,
                description="请求参数错误",
                is_success=False,
            )
        )
        route_info.responses.append(
            ResponseInfo(
                status_code=404,
                description="资源未找到",
                is_success=False,
            )
        )
        route_info.responses.append(
            ResponseInfo(
                status_code=500,
                description="服务器内部错误",
                is_success=False,
            )
        )

    def _type_annotation_to_str(self, node: ast.AST | None) -> str | None:
        """将类型注解 AST 节点转换为字符串"""
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.Subscript):
            base = self._type_annotation_to_str(node.value)
            if isinstance(node.slice, ast.Tuple):
                args = ", ".join(self._type_annotation_to_str(elt) or "?" for elt in node.slice.elts)
                return f"{base}[{args}]"
            arg = self._type_annotation_to_str(node.slice) or "?"
            return f"{base}[{arg}]"
        if isinstance(node, ast.Attribute):
            value = self._type_annotation_to_str(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self._type_annotation_to_str(node.left)
            right = self._type_annotation_to_str(node.right)
            return f"{left} | {right}"
        return None


class PydanticModelExtractor(ast.NodeVisitor):
    """Pydantic 模型提取器

    遍历 AST，识别继承自 BaseModel 的类定义，
    提取字段名称、类型、默认值、约束等信息。
    """

    def __init__(self):
        self.models: list[PydanticModelInfo] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        """解析类定义"""
        is_pydantic = False
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == "BaseModel":
                is_pydantic = True
                break
            if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
                is_pydantic = True
                break

        if not is_pydantic:
            self.generic_visit(node)
            return

        model_info = PydanticModelInfo(name=node.name)

        docstring = ast.get_docstring(node)
        if docstring:
            model_info.description = docstring

        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                field_info = self._extract_field(item)
                if field_info:
                    model_info.fields.append(field_info)

        self.models.append(model_info)
        self.generic_visit(node)

    def _extract_field(self, node: ast.AnnAssign) -> ModelFieldInfo | None:
        """提取 Pydantic 模型字段信息"""
        field_name = node.target.id if isinstance(node.target, ast.Name) else None
        if not field_name:
            return None

        field_type = self._type_annotation_to_str(node.annotation) or "Any"

        is_required = True
        default_value = ""
        description = ""
        constraints: list[str] = []

        if node.value:
            is_required, default_value, description, constraints = self._parse_field_value(node.value)

        return ModelFieldInfo(
            name=field_name,
            field_type=field_type,
            is_required=is_required,
            default_value=default_value,
            description=description,
            constraints=constraints,
        )

    def _parse_field_value(self, value_node: ast.AST) -> tuple[bool, str, str, list[str]]:
        """解析字段值（处理 Field() 调用）"""
        if isinstance(value_node, ast.Call):
            func = value_node.func
            if isinstance(func, ast.Name) and func.id == "Field":
                return self._parse_field_call(value_node)
            if isinstance(func, ast.Attribute) and func.attr == "Field":
                return self._parse_field_call(value_node)

        default_str = self._extract_default_value(value_node)
        return False, default_str, "", []

    def _parse_field_call(self, call_node: ast.Call) -> tuple[bool, str, str, list[str]]:
        """解析 Field() 调用"""
        is_required = True
        default_value = ""
        description = ""
        constraints: list[str] = []

        positional_idx = 0
        if call_node.args:
            default_node = call_node.args[0]
            if isinstance(default_node, ast.Constant):
                if default_node.value is ...:
                    is_required = True
                else:
                    is_required = False
                    default_value = repr(default_node.value)

        for kw in call_node.keywords:
            if kw.arg == "default":
                if isinstance(kw.value, ast.Constant):
                    if kw.value.value is ...:
                        is_required = True
                    else:
                        is_required = False
                        default_value = repr(kw.value.value)
            elif kw.arg == "description":
                description = self._extract_string(kw.value) or ""
            elif kw.arg == "ge":
                val = self._extract_numeric(kw.value)
                if val is not None:
                    constraints.append(f"≥ {val}")
            elif kw.arg == "gt":
                val = self._extract_numeric(kw.value)
                if val is not None:
                    constraints.append(f"> {val}")
            elif kw.arg == "le":
                val = self._extract_numeric(kw.value)
                if val is not None:
                    constraints.append(f"≤ {val}")
            elif kw.arg == "lt":
                val = self._extract_numeric(kw.value)
                if val is not None:
                    constraints.append(f"< {val}")
            elif kw.arg == "min_length":
                val = self._extract_numeric(kw.value)
                if val is not None:
                    constraints.append(f"最小长度: {val}")
            elif kw.arg == "max_length":
                val = self._extract_numeric(kw.value)
                if val is not None:
                    constraints.append(f"最大长度: {val}")
            elif kw.arg == "pattern":
                val = self._extract_string(kw.value)
                if val is not None:
                    constraints.append(f"正则: `{val}`")
            elif kw.arg == "alias":
                val = self._extract_string(kw.value)
                if val is not None:
                    constraints.append(f"别名: {val}")

        return is_required, default_value, description, constraints

    def _extract_string(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _extract_numeric(self, node: ast.AST) -> float | int | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        return None

    def _extract_default_value(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            if node.value is ...:
                return "*(必填)*"
            return repr(node.value)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.List):
            return "[]"
        if isinstance(node, ast.Dict):
            return "{}"
        return "..."

    def _type_annotation_to_str(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.Subscript):
            base = self._type_annotation_to_str(node.value)
            if isinstance(node.slice, ast.Tuple):
                args = ", ".join(self._type_annotation_to_str(elt) or "?" for elt in node.slice.elts)
                return f"{base}[{args}]"
            arg = self._type_annotation_to_str(node.slice) or "?"
            return f"{base}[{arg}]"
        if isinstance(node, ast.Attribute):
            value = self._type_annotation_to_str(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self._type_annotation_to_str(node.left)
            right = self._type_annotation_to_str(node.right)
            return f"{left} | {right}"
        return None


# 文档生成器


class APIDocumentGenerator:
    """API 文档生成器

    将提取的路由和模型信息填充至 Markdown 模板，
    生成完整的 API 参考文档。
    """

    MARKER_START = "<!-- AUTO-GENERATED-CONTENT-START -->"
    MARKER_END = "<!-- AUTO-GENERATED-CONTENT-END -->"

    def __init__(self, routes: list[RouteInfo], models: list[PydanticModelInfo]):
        self.routes = routes
        self.models = models

    def generate_auto_content(self) -> str:
        """仅生成自动内容部分（用于填充模板占位区域）"""
        sections: list[str] = []
        sections.append(self._generate_all_routes())
        sections.append(self._generate_lnn_endpoints())
        sections.append(self._generate_wear_endpoints())
        sections.append(self._generate_models_table())
        return "\n\n".join(sections)

    def generate(self) -> str:
        """生成完整文档（不含模板时使用）"""
        sections: list[str] = []
        sections.append(self._generate_header())
        sections.append(self._generate_overview())
        sections.append(self._generate_auth_section())
        sections.append(self._generate_error_codes())
        sections.append(self.generate_auto_content())
        sections.append(self._generate_footer())
        return "\n\n".join(sections) + "\n"

    def _generate_header(self) -> str:
        # 版本号与 VERSION / app/version.py 保持一致（version_sync CI 门禁保障）
        return """# API 参考文档

> **自动生成**: 本文档由 `scripts/gen-api-docs.py` 自动生成
> 
> **最后更新**: 自动填充
> 
> **适用版本**: 灵境制造平台 v2.7.0

> **交叉引用**: 本文档为 API 端点总览，完整的请求/响应示例、错误码详解与认证流程见 [`docs/api/README.md`](./api/README.md)。两份文档遵循同一响应格式约定（见下文"响应格式约定"小节）。"""

    def _generate_overview(self) -> str:
        return """## 概述

灵境制造平台提供 RESTful API 接口，支持 AI 模型管理、刀具磨损预测、训练任务管理等功能。

### 基础信息

| 属性 | 值 |
|------|-----|
| 基础路径 | `/api/v1` |
| 数据格式 | JSON |
| 字符编码 | UTF-8 |

### 通用请求格式

所有 POST/PUT 请求的请求体必须使用 JSON 格式，并设置请求头：

```
Content-Type: application/json
```

### 响应格式约定

所有 API 响应遵循统一格式，字段定义与 `python/app/core/response.py` 实现保持一致：

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | `number` | 数值状态码，`0` 表示成功，非 `0` 表示错误（如 `1001`、`2001`） |
| `message` | `string` | 人类可读的状态描述 |
| `data` | `any` | 成功时为业务数据，错误时通常省略或为 `null` |
| `request_id` | `string` | 请求追踪标识，对应客户端 `X-Request-ID` |

> **注意**：代码内部 `ErrorCode` 保留字符串枚举（如 `SUCCESS`、`NOT_FOUND`）以保持向后兼容，但通过 `code_to_numeric()` 映射表统一转换为数值后返回给客户端。客户端应始终以数值 `code` 判断响应状态，不应依赖字符串枚举值。

### 通用响应格式

成功响应示例：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": { ... },
  "request_id": "uuid-string"
}
```

错误响应示例：

```json
{
  "code": 1001,
  "message": "资源未找到",
  "request_id": "uuid-string",
  "detail": "附加详情（可选）",
  "suggestion": "建议操作（可选）"
}
```"""

    def _generate_auth_section(self) -> str:
        return """## 认证与授权

当前版本 API 无需额外认证。生产环境部署时建议启用 API Key 或 JWT Token 认证机制。"""

    def _generate_error_codes(self) -> str:
        return """## 错误码参考

下表列出 `ErrorCode` 枚举与对应数值码的映射关系。客户端应以 `code`（数值）列为准。

| `ErrorCode` 枚举 | `code`（数值） | 说明 | 解决建议 |
|------------------|---------------|------|----------|
| `SUCCESS` | `0` | 操作成功 | - |
| `NOT_FOUND` | `1001` | 资源未找到 | 检查请求路径或资源标识是否正确 |
| `INVALID_REQUEST` | `1002` | 请求参数错误 | 检查请求参数格式和取值范围 |
| `UNAUTHORIZED` | `1003` | 未授权访问 | 检查认证凭据是否有效 |
| `FILE_NOT_FOUND` | `1008` | 文件不存在 | 检查文件路径是否正确 |
| `INTERNAL_ERROR` | `2001` | 服务器内部错误 | 检查服务器日志，联系技术支持 |
| `SERVICE_UNAVAILABLE` | `2002` | 服务不可用 | 检查服务状态，稍后重试 |
| `CAD_GENERATION_ERROR` | `7001` | CAD 生成失败 | 检查输入参数和模板配置 |"""

    def _generate_all_routes(self) -> str:
        """全量路由总览：按路径前缀分组的紧凑清单（覆盖所有已注册路由，
        避免重构后新增域路由遗漏在 lnn/wear 两个专题 section 之外）"""
        lines = ["## 全部 API 路由", "", "所有已注册路由的完整清单（按路径前缀分组）。", ""]

        groups: dict[str, list[RouteInfo]] = {}
        for route in self.routes:
            parts = route.path.strip("/").split("/")
            key = "/" + "/".join(parts[:2]) if len(parts) > 1 else "/" + parts[0]
            groups.setdefault(key, []).append(route)

        for key in sorted(groups):
            routes = sorted(groups[key], key=lambda r: (r.path, r.method))
            lines.append(f"### {key}")
            lines.append("")
            lines.append("| 方法 | 路径 | 说明 |")
            lines.append("|------|------|------|")
            for r in routes:
                summary = (r.summary or "").replace("|", "\\|").replace("\n", " ").strip()
                lines.append(f"| `{r.method}` | `{r.path}` | {summary} |")
            lines.append("")

        return "\n".join(lines)

    def _generate_lnn_endpoints(self) -> str:
        """生成 LNN 相关端点文档"""
        lnn_routes = [r for r in self.routes if "/lnn" in r.path]
        lnn_routes.sort(key=lambda r: r.path)

        lines = [
            "## LNN 模型 API",
            "",
            "LNN（Liquid Neural Network）模型管理接口，支持模型预测、训练、量化等功能。",
            "",
        ]

        for route in lnn_routes:
            lines.append(self._format_route(route))

        return "\n".join(lines)

    def _generate_wear_endpoints(self) -> str:
        """生成刀具磨损相关端点文档"""
        wear_routes = [r for r in self.routes if "/wear" in r.path]
        wear_routes.sort(key=lambda r: r.path)

        lines = [
            "## 刀具磨损预测 API",
            "",
            "刀具磨损预测和工艺优化接口，支持磨损曲线预测、剩余寿命评估等功能。",
            "",
        ]

        for route in wear_routes:
            lines.append(self._format_route(route))

        return "\n".join(lines)

    def _format_route(self, route: RouteInfo) -> str:
        """格式化单个路由为 Markdown"""
        lines: list[str] = []

        badge_color = self._get_method_color(route.method)
        lines.append(f"### `{route.method}` `{route.path}`")
        lines.append("")

        if route.summary:
            lines.append(f"**{route.summary}**")
            lines.append("")

        if route.parameters:
            path_params = [p for p in route.parameters if p.location == "path"]
            query_params = [p for p in route.parameters if p.location == "query"]
            body_params = [p for p in route.parameters if p.location == "body" and p.param_type not in ("Any",)]

            if path_params:
                lines.append("**路径参数：**")
                lines.append("")
                lines.append("| 参数名 | 类型 | 必填 | 说明 |")
                lines.append("|--------|------|------|------|")
                for p in path_params:
                    lines.append(
                        f"| `{p.name}` | `{p.param_type}` | {'是' if p.is_required else '否'} | {p.description} |"
                    )
                lines.append("")

            if query_params:
                lines.append("**查询参数：**")
                lines.append("")
                lines.append("| 参数名 | 类型 | 默认值 | 必填 | 说明 |")
                lines.append("|--------|------|--------|------|------|")
                for p in query_params:
                    default = p.default_value if p.default_value else "-"
                    lines.append(
                        f"| `{p.name}` | `{p.param_type}` | `{default}` | {'是' if p.is_required else '否'} | {p.description} |"
                    )
                lines.append("")

            if body_params:
                lines.append("**请求体参数：**")
                lines.append("")
                lines.append("| 参数名 | 类型 | 默认值 | 必填 | 说明 |")
                lines.append("|--------|------|--------|------|------|")
                for p in body_params:
                    default = p.default_value if p.default_value else "-"
                    lines.append(
                        f"| `{p.name}` | `{p.param_type}` | `{default}` | {'是' if p.is_required else '否'} | {p.description} |"
                    )
                lines.append("")

            if route.request_body_model:
                model_name = route.request_body_model
                lines.append(f"**请求体模型**: [{model_name}](#{model_name.lower()})")
                lines.append("")

        if route.responses:
            success_responses = [r for r in route.responses if r.is_success]
            error_responses = [r for r in route.responses if not r.is_success]

            lines.append("**响应：**")
            lines.append("")
            for resp in route.responses:
                model_str = f" ({resp.model_name})" if resp.model_name else ""
                lines.append(f"- **{resp.status_code}**: {resp.description}{model_str}")
            lines.append("")

        return "\n".join(lines)

    def _get_method_color(self, method: str) -> str:
        colors = {
            "GET": "green",
            "POST": "blue",
            "PUT": "orange",
            "DELETE": "red",
            "PATCH": "purple",
        }
        return colors.get(method, "gray")

    def _generate_models_table(self) -> str:
        """生成 Pydantic 模型定义表格"""
        lines = [
            "## 数据模型",
            "",
            "API 请求和响应使用的 Pydantic 模型定义。",
            "",
        ]

        if not self.models:
            lines.append("*暂无模型定义*")
            return "\n".join(lines)

        for model in self.models:
            lines.append(f"### `{model.name}`")
            lines.append("")
            if model.description:
                lines.append(f"{model.description}")
                lines.append("")

            if model.fields:
                lines.append("| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |")
                lines.append("|--------|------|------|--------|------|------|")
                for f in model.fields:
                    default = f.default_value if f.default_value else "-"
                    required = "是" if f.is_required else "否"
                    constraints = "; ".join(f.constraints) if f.constraints else "-"
                    lines.append(
                        f"| `{f.name}` | `{f.field_type}` | {required} | `{default}` | {f.description} | {constraints} |"
                    )
                lines.append("")

        return "\n".join(lines)

    def _generate_dataset_info(self) -> str:
        return """## 数据集相关信息

### Uniwear 数据集

| 属性 | 值 |
|------|-----|
| 本地路径 | `python/data/uniwear/` |
| NUAA 实验 | 9 个（TC4 材料） |
| PHM2010 实验 | 3 个（HRC52 材料） |

### Bosch CNC 数据集

| 属性 | 值 |
|------|-----|
| 用途 | 振动异常分类 |
| 格式 | CSV |"""

    def _generate_footer(self) -> str:
        return """---

*本文档由 API 文档自动生成系统生成，如有疑问请联系开发团队。*"""


# 主程序


def _module_name(file_path: Path, app_root: Path) -> str:
    """文件相对 engineering/python/ 的模块名：app.api.v1.lnn.routes_prediction"""
    rel = file_path.relative_to(app_root.parent)
    parts = list(rel.parts)
    parts[-1] = parts[-1][:-3] if parts[-1].endswith(".py") else parts[-1]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return ""
    return ".".join(parts)


def scan_api_files_global(
    dirs: list[Path],
    app_root: Path,
    extra_files: list[Path] | None = None,
) -> tuple[list[RouteInfo], list[PydanticModelInfo]]:
    """扫描多个目录 + 额外文件，收集 AST 状态后做跨文件 include_router 前缀传播。

    V2.7.0 解耦重构后路由采用「域注册器 → 聚合 router → 子路由」的多层 include_router
    架构（见 app/api/routers/*.py 与 app/api/v1/*/routes.py），子路由不再重复声明
    prefix，统一由聚合 router 声明。单文件静态扫描无法还原完整路径，因此这里
    收集所有文件的 router 定义 / include 关系 / import 别名，构建全局图后传播前缀。
    """
    all_files: list[Path] = []
    for d in dirs:
        if d.exists():
            all_files.extend(d.rglob("*.py"))
    if extra_files:
        all_files.extend(f for f in extra_files if f.exists())

    extractors: list[FastAPIRouteExtractor] = []
    module_names: list[str] = []
    models: list[PydanticModelInfo] = []
    file_paths: list[Path] = []

    for file_path in all_files:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as e:
            print(f"[警告] 解析文件失败 {file_path}: {e}", file=sys.stderr)
            continue

        route_extractor = FastAPIRouteExtractor()
        route_extractor.visit(tree)
        extractors.append(route_extractor)
        module_names.append(_module_name(file_path, app_root))
        file_paths.append(file_path)

        model_extractor = PydanticModelExtractor()
        model_extractor.visit(tree)
        for model in model_extractor.models:
            model.file_path = str(file_path.relative_to(app_root.parent))
            models.append(model)

    # 全局合并：router 注册表 / include 边 / pending 路由
    routers: dict[tuple[str, str], dict] = {}
    includes: list[tuple[tuple[str, str], tuple[str, str], str | None]] = []
    pending: list[tuple[tuple[str, str], ast.AST, ast.AsyncFunctionDef | ast.FunctionDef]] = []

    for ex, mod in zip(extractors, module_names):
        for name, cfg in ex._routers.items():
            key = (mod, name)
            own = cfg.get("prefix", "") or ""
            if key not in routers:
                routers[key] = {
                    "own": own,
                    "eff": own,
                    "tags": list(cfg.get("tags", [])),
                }
            else:
                # 同名冲突（各文件都有 router）：保留带 prefix 的定义
                if own and not routers[key]["own"]:
                    routers[key]["own"] = own
                    routers[key]["eff"] = own
        for parent, child, inc in ex._includes:
            pkey = ex.resolve_symbol(parent, mod)
            ckey = ex.resolve_symbol(child, mod)
            includes.append((pkey, ckey, inc))
        for rname, dec, node in ex._pending:
            key = ex.resolve_symbol(rname, mod)
            pending.append((key, dec, node))

    # 跨文件传播 include 前缀（幂等：child 基准用自身声明 own，不叠加已继承前缀）
    for _ in range(10):
        changed = False
        for pkey, ckey, inc in includes:
            if pkey in routers and ckey in routers:
                pp = routers[pkey]["eff"] or ""
                base = routers[ckey]["own"] or ""
                newp = (pp + inc) if inc is not None else (pp + base)
                if newp != routers[ckey]["eff"]:
                    routers[ckey]["eff"] = newp
                    changed = True
        if not changed:
            break

    # 生成路由（前缀已解析）
    routes: list[RouteInfo] = []
    for (mod, name), dec, node in pending:
        cfg = routers.get((mod, name), {})
        prefix = cfg.get("eff", "") or ""
        tags = cfg.get("tags", [])
        for ex in extractors:
            ri = ex._extract_route_from_decorator(dec, node, prefix=prefix, tags=tags)
            if ri:
                routes.append(ri)
                break

    # 全局 schema 模型文件
    schema_files = [
        app_root.parent / "models" / "schemas.py",
        app_root.parent / "models" / "validation.py",
    ]
    for schema_file in schema_files:
        if schema_file.exists():
            try:
                source = schema_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(schema_file))
                model_extractor = PydanticModelExtractor()
                model_extractor.visit(tree)
                for model in model_extractor.models:
                    model.file_path = str(schema_file.relative_to(app_root.parent.parent))
                    models.append(model)
            except SyntaxError as e:
                print(f"[警告] 解析模型文件失败 {schema_file}: {e}", file=sys.stderr)

    return routes, models


def scan_api_files(api_dir: Path) -> tuple[list[RouteInfo], list[PydanticModelInfo]]:
    """兼容包装：单目录扫描（保留旧签名，供外部调用）"""
    return scan_api_files_global([api_dir], api_dir.parent.parent)


def load_template(template_path: Path) -> str:
    """加载文档模板"""
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    return template_path.read_text(encoding="utf-8")


def generate_document(
    project_root: Path,
    template_path: Path | None = None,
    use_template: bool = True,
) -> str:
    """生成完整 API 文档"""
    # V2.7.0 解耦：工程侧代码位于 engineering/python/（原 python/）
    # 扫描范围与 scripts/check_api_docs_sync.py 保持一致（否则文档永远落后于门禁检查）：
    # app/api/v1 + rag + ai + simulation + projects + step_import + rules + main.py
    # 另加 app/api/routers/（域注册器，include_router 前缀传播的根节点）
    app_root = project_root / "engineering" / "python" / "app"
    api_dirs = [
        app_root / "api" / "v1",
        app_root / "api" / "routers",
        app_root / "rag",
        app_root / "ai",
        app_root / "simulation",
        app_root / "projects",
        app_root / "step_import",
        app_root / "rules",
    ]

    routes, models = scan_api_files_global(
        api_dirs,
        app_root,
        extra_files=[app_root / "main.py"],
    )

    if not routes:
        print("[警告] 未找到任何 API 路由定义", file=sys.stderr)

    generator = APIDocumentGenerator(routes, models)

    if use_template and template_path and template_path.exists():
        template_content = load_template(template_path)
        auto_content = generator.generate_auto_content()

        if APIDocumentGenerator.MARKER_START in template_content:
            start_idx = template_content.index(APIDocumentGenerator.MARKER_START)
            end_marker = APIDocumentGenerator.MARKER_END
            if end_marker in template_content:
                end_idx = template_content.index(end_marker)
                result = (
                    template_content[: start_idx + len(APIDocumentGenerator.MARKER_START)]
                    + "\n"
                    + auto_content
                    + "\n"
                    + template_content[end_idx:]
                )
                return result

        return generator.generate()
    else:
        return generator.generate()


def validate_document(project_root: Path, output_path: Path) -> bool:
    """验证文档是否已与代码同步"""
    print("[验证] 正在生成临时文档...")

    template_path = project_root / "docs" / "api-reference.md.tmpl"

    try:
        temp_doc = generate_document(project_root, template_path)
    except Exception as e:
        print(f"[错误] 文档生成失败: {e}", file=sys.stderr)
        return False

    if not output_path.exists():
        print("[验证] 文档文件不存在，需要生成初始文档")
        print(f"  请运行: python scripts/gen-api-docs.py")
        return False

    existing_doc = output_path.read_text(encoding="utf-8")

    if temp_doc.strip() == existing_doc.strip():
        print("[验证] API 文档已与代码同步")
        return True
    else:
        print("[验证] API 文档已过时，与代码不一致")
        print()
        print("差异摘要:")
        print(f"  临时文档行数: {len(temp_doc.splitlines())}")
        print(f"  当前文档行数: {len(existing_doc.splitlines())}")
        print()
        print("请运行以下命令更新文档:")
        print("  python scripts/gen-api-docs.py")
        return False


def dry_run(project_root: Path) -> None:
    """Dry run 模式：生成文档但不覆盖"""
    print("[Dry Run] 正在生成文档（不保存）...")

    doc_content = generate_document(project_root)

    print(f"[Dry Run] 文档生成完成，共 {len(doc_content.splitlines())} 行")
    print()
    print("文档预览（前 50 行）:")
    print("-" * 60)
    for i, line in enumerate(doc_content.splitlines()[:50]):
        print(line)
    if len(doc_content.splitlines()) > 50:
        print(f"... (共 {len(doc_content.splitlines())} 行，仅显示前 50 行)")


def main():
    parser = argparse.ArgumentParser(
        description="API 文档自动生成系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python scripts/gen-api-docs.py              生成完整文档
  python scripts/gen-api-docs.py --dry-run    预览文档但不保存
  python scripts/gen-api-docs.py --validate   验证文档是否已同步
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="生成文档但不保存到文件",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="验证文档是否已与代码同步",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出文件路径（默认: docs/api-reference.md）",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="项目根目录路径（默认: 当前工作目录）",
    )

    args = parser.parse_args()

    project_root = Path(args.project_root) if args.project_root else Path.cwd()
    output_path = Path(args.output) if args.output else project_root / "docs" / "api-reference.md"
    template_path = project_root / "docs" / "api-reference.md.tmpl"

    if args.dry_run:
        dry_run(project_root)
        sys.exit(0)

    if args.validate:
        success = validate_document(project_root, output_path)
        sys.exit(0 if success else 1)

    try:
        print("[生成] 正在扫描 API 文件...")
        doc_content = generate_document(project_root, template_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(doc_content, encoding="utf-8")

        print(f"[生成] 文档已生成: {output_path}")
        print(f"[生成] 共 {len(doc_content.splitlines())} 行")
        print("[生成] 完成")

    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 文档生成失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
