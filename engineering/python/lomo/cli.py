"""lomo SDK 命令行工具。

基于 typer 实现，复用 :mod:`lomo` SDK 同步 API，提供与 HTTP API 一致的
命令行接口。所有命令的退出码遵循 POSIX 惯例：``0`` 成功，``1`` 业务错误，
``2`` 参数错误。

子命令结构::

    lomo config    show | set | path
    lomo workflow  validate | run | list | status | cancel | delete | subscribe
    lomo dataset   list | create | get | versions | commit | read | deprecate | lineage
    lomo snapshot  list | create | get | reproduce
    lomo train     run         # 高级封装：提交训练工作流 + 订阅事件 + 自动建快照
    lomo predict   run         # 高级封装：基于 snapshot 提交预测工作流 + 等待结果

全局选项（可置于子命令前）::

    --base-url URL     后端服务地址（默认读取 LOMO_BASE_URL 或 ~/.lomo/config.toml）
    --token TOKEN      Bearer token（默认读取 LOMO_TOKEN 或 ~/.lomo/config.toml）
    --output FORMAT    输出格式：table（默认）| json | raw
    --timeout SECONDS  请求超时秒数（默认 30）

train / predict 是"高级命令"：在 workflow + snapshot 资源之上封装 dataset→workflow→snapshot
全流程，自动订阅事件流直到工作流终止态（completed/failed/cancelled），无需手动 `workflow subscribe`。

配置文件::

    ~/.lomo/config.toml 由 ``lomo config set`` 写入，结构::

        base_url = "http://127.0.0.1:8000"
        token = "xxx"

    环境变量优先级高于配置文件：LOMO_BASE_URL / LOMO_TOKEN。

示例::

    # 配置
    lomo config set --base-url http://127.0.0.1:8000 --token secret

    # 数据集操作
    lomo dataset create --name phm2010 --schema schema.json --owner alice
    lomo dataset list --owner alice --output json
    lomo dataset commit <dataset_id> --records records.json --version 1.0.0

    # 工作流
    lomo workflow validate --spec workflow.yaml
    lomo workflow run --spec workflow.yaml --owner alice
    lomo workflow subscribe <run_id>

    # 快照
    lomo snapshot create --model-uri model://ltc/1.0.0 --by alice --notes "首次复现"
    lomo snapshot reproduce <snapshot_id>

    # 高级封装：train / predict（自动订阅事件流 + 终态后退出）
    lomo train run --spec ltc_train_eval.yaml --owner alice --notes "ltc v1.0.0"
    lomo predict run --snapshot-id <snapshot_id> --input-file predict_input.json --owner alice
"""

from __future__ import annotations

import json as _json
import os
import time
from pathlib import Path
from typing import Any, Optional

import typer

# tomllib 在 Python 3.11+ 内置（仅读取）；写入采用手动格式化避免引入 tomli_w 依赖。
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None  # type: ignore[assignment]

from lomo.client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, LomoClient
from lomo.exceptions import LomoError

# 常量与配置文件

CONFIG_DIR = Path.home() / ".lomo"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# 退出码
EXIT_OK = 0
EXIT_BUSINESS_ERROR = 1
EXIT_PARAM_ERROR = 2


def _load_config_file() -> dict[str, str]:
    """读取 ~/.lomo/config.toml。文件不存在时返回空 dict。"""
    if not CONFIG_FILE.exists() or tomllib is None:
        return {}
    try:
        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)
        # 仅保留字符串字段，过滤非法类型
        return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _save_config_file(base_url: Optional[str], token: Optional[str]) -> None:
    """写入 ~/.lomo/config.toml（仅写入非 None 字段）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if base_url is not None:
        lines.append(f"base_url = {_toml_string(base_url)}")
    if token is not None:
        lines.append(f"token = {_toml_string(token)}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_string(value: str) -> str:
    """将字符串转义为 TOML 基本字符串字面量。"""
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _mask_token(token: Optional[str]) -> str:
    """脱敏 token 用于展示。"""
    if not token:
        return "(unset)"
    if len(token) <= 8:
        return "*" * len(token)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]


# 输出渲染


def _print_data(data: Any, output_format: str) -> None:
    """按指定格式输出数据到 stdout。

    - table: rich 表格（dict/list 渲染为表格；标量直接打印）
    - json:  紧凑 JSON
    - raw:   原始 Python repr（调试用）
    """
    if output_format == "raw":
        typer.echo(repr(data))
        return
    if output_format == "json":
        typer.echo(_json.dumps(data, ensure_ascii=False, default=str, indent=2))
        return
    # table
    _print_table(data)


def _print_table(data: Any) -> None:
    """rich 表格输出。"""
    try:
        from rich.console import Console
        from rich.table import Table
    except ModuleNotFoundError:  # pragma: no cover - rich 是声明依赖
        # 无 rich 时降级为 JSON 输出
        typer.echo(_json.dumps(data, ensure_ascii=False, default=str, indent=2))
        return

    console = Console()
    if data is None:
        console.print("[dim](no data)[/dim]")
        return
    if isinstance(data, (str, int, float, bool)):
        console.print(str(data))
        return
    if isinstance(data, dict):
        # 单 dict：键值对表格
        table = Table(show_header=False, box=None)
        table.add_column("Key", style="cyan", no_wrap=True)
        table.add_column("Value")
        for k, v in data.items():
            table.add_row(str(k), _format_value(v))
        console.print(table)
        return
    if isinstance(data, list):
        if not data:
            console.print("[dim](empty list)[/dim]")
            return
        # list[dict]：列名取并集（保持插入顺序）
        if all(isinstance(item, dict) for item in data):
            headers: list[str] = []
            seen: set[str] = set()
            for item in data:
                for k in item.keys():
                    if k not in seen:
                        seen.add(k)
                        headers.append(k)
            table = Table(show_lines=True)
            for h in headers:
                table.add_column(h, overflow="fold")
            for item in data:
                table.add_row(*[_format_value(item.get(h, "")) for h in headers])
            console.print(table)
            return
        # list[scalar]：单列
        table = Table(show_header=False, box=None)
        table.add_column("Value")
        for item in data:
            table.add_row(_format_value(item))
        console.print(table)
        return
    # 其他类型降级为 JSON
    typer.echo(_json.dumps(data, ensure_ascii=False, default=str, indent=2))


def _format_value(v: Any) -> str:
    """表格单元格值格式化。"""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return _json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, bool):
        return "✓" if v else "✗"
    return str(v)


def _read_json_file(path: str) -> Any:
    """从文件读取 JSON（支持 .json）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except OSError as e:
        raise typer.BadParameter(f"无法读取文件 {path}: {e}") from e
    except _json.JSONDecodeError as e:
        raise typer.BadParameter(f"文件 {path} 不是合法 JSON: {e}") from e


def _read_spec_file(path: str) -> dict[str, Any]:
    """读取 WorkflowSpec 文件（.json / .yaml / .yml）。"""
    p = Path(path)
    if not p.exists():
        raise typer.BadParameter(f"文件不存在: {path}")
    suffix = p.suffix.lower()
    if suffix == ".json":
        return _read_json_file(path)  # type: ignore[return-value]
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ModuleNotFoundError as e:
            raise typer.BadParameter("读取 YAML 需要 PyYAML（已声明在 requirements.txt）") from e
        try:
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            raise typer.BadParameter(f"YAML 解析失败 {path}: {e}") from e
        if not isinstance(data, dict):
            raise typer.BadParameter(f"WorkflowSpec 必须是 dict，实际为 {type(data).__name__}")
        return data
    raise typer.BadParameter(f"不支持的 spec 文件格式: {suffix}（仅支持 .json/.yaml/.yml）")


# 全局状态与客户端构造


class _State:
    """全局 CLI 状态，通过 ctx.obj 传递。"""

    base_url: Optional[str]
    token: Optional[str]
    output: str
    timeout: float

    def __init__(self) -> None:
        self.base_url = None
        self.token = None
        self.output = "table"
        self.timeout = DEFAULT_TIMEOUT

    def resolve(self) -> tuple[Optional[str], Optional[str]]:
        """解析最终 base_url / token（环境变量 > 命令行选项 > 配置文件 > 默认值）。"""
        cfg = _load_config_file()
        base_url = os.getenv("LOMO_BASE_URL") or self.base_url or cfg.get("base_url") or DEFAULT_BASE_URL
        token = os.getenv("LOMO_TOKEN") or self.token or cfg.get("token")
        return base_url, token

    def make_client(self) -> LomoClient:
        base_url, token = self.resolve()
        return LomoClient(
            base_url=base_url,
            token=token,
            timeout=self.timeout,
        )


def _handle_lomo_error(e: LomoError) -> None:
    """统一异常处理：输出到 stderr 并退出。"""
    msg = str(e)
    code = getattr(e, "code", None)
    prefix = f"[code={code}] " if code is not None else ""
    typer.echo(f"错误: {prefix}{msg}", err=True)
    detail = getattr(e, "detail", None)
    if detail:
        typer.echo(f"详情: {detail}", err=True)
    suggestion = getattr(e, "suggestion", None)
    if suggestion:
        typer.echo(f"建议: {suggestion}", err=True)
    raise typer.Exit(code=EXIT_BUSINESS_ERROR)


# 主 app 与子命令组

app = typer.Typer(
    name="lomo",
    help="灵境制造 SDK 命令行工具 — 通过 HTTP API 操作后端资源。",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

workflow_app = typer.Typer(
    name="workflow",
    help="工作流操作（校验 / 运行 / 状态 / 订阅 / 取消 / 删除）。",
    no_args_is_help=True,
)
dataset_app = typer.Typer(
    name="dataset",
    help="数据集操作（CRUD / 版本管理 / 流式读取 / 血缘）。",
    no_args_is_help=True,
)
snapshot_app = typer.Typer(
    name="snapshot",
    help="实验快照操作（列表 / 创建 / 详情 / 一键复现）。",
    no_args_is_help=True,
)
config_app = typer.Typer(
    name="config",
    help="CLI 配置管理（base_url / token）。",
    no_args_is_help=True,
)
train_app = typer.Typer(
    name="train",
    help="高级命令：提交训练工作流 + 订阅事件 + 自动产出快照。",
    no_args_is_help=True,
)
predict_app = typer.Typer(
    name="predict",
    help="高级命令：基于快照提交预测工作流并等待结果。",
    no_args_is_help=True,
)

app.add_typer(workflow_app, name="workflow")
app.add_typer(dataset_app, name="dataset")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(config_app, name="config")
app.add_typer(train_app, name="train")
app.add_typer(predict_app, name="predict")


@app.callback()
def _main_callback(
    ctx: typer.Context,
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        "-B",
        help="后端服务地址（覆盖配置文件与环境变量）",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        "-T",
        help="Bearer token（覆盖配置文件与环境变量）",
    ),
    output: str = typer.Option(
        "table",
        "--output",
        "-o",
        help="输出格式：table | json | raw",
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT,
        "--timeout",
        help="请求超时秒数",
    ),
) -> None:
    """全局选项。可置于任意子命令前。"""
    state = _State()
    state.base_url = base_url
    state.token = token
    state.output = output if output in ("table", "json", "raw") else "table"
    state.timeout = timeout
    ctx.obj = state


# config 子命令


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """显示当前配置（token 脱敏）。"""
    state: _State = ctx.obj
    base_url, token = state.resolve()
    cfg = _load_config_file()
    typer.echo(f"配置文件: {CONFIG_FILE}")
    typer.echo(f"  存在: {'是' if CONFIG_FILE.exists() else '否'}")
    typer.echo(f"base_url: {base_url}")
    typer.echo(f"token:    {_mask_token(token)}")
    typer.echo(f"timeout:  {state.timeout}s")
    typer.echo(f"output:   {state.output}")
    if cfg:
        typer.echo(f"配置文件字段: {list(cfg.keys())}")


@config_app.command("set")
def config_set(
    base_url: Optional[str] = typer.Option(None, "--base-url", "-B", help="后端服务地址"),
    token: Optional[str] = typer.Option(None, "--token", "-T", help="Bearer token"),
) -> None:
    """写入配置到 ~/.lomo/config.toml（仅写入提供的字段）。"""
    if base_url is None and token is None:
        raise typer.BadParameter("至少提供 --base-url 或 --token 之一")
    _save_config_file(base_url, token)
    typer.echo(f"已写入 {CONFIG_FILE}")
    if base_url is not None:
        typer.echo(f"  base_url = {base_url}")
    if token is not None:
        typer.echo(f"  token    = {_mask_token(token)}")


@config_app.command("path")
def config_path() -> None:
    """显示配置文件路径。"""
    typer.echo(str(CONFIG_FILE))


# workflow 子命令


@workflow_app.command("validate")
def workflow_validate(
    ctx: typer.Context,
    spec: str = typer.Option(..., "--spec", "-s", help="WorkflowSpec 文件路径（.json/.yaml/.yml）"),
) -> None:
    """校验 WorkflowSpec（不启动运行）。"""
    state: _State = ctx.obj
    spec_data = _read_spec_file(spec)
    try:
        with state.make_client() as client:
            result = client.workflows.validate(spec_data)
        _print_data(result, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@workflow_app.command("run")
def workflow_run(
    ctx: typer.Context,
    spec: str = typer.Option(..., "--spec", "-s", help="WorkflowSpec 文件路径"),
    inputs: Optional[str] = typer.Option(None, "--inputs", "-i", help="运行时输入 artifact JSON 文件"),
    owner: Optional[str] = typer.Option(None, "--owner", help="运行发起者 ID"),
) -> None:
    """提交工作流运行。"""
    state: _State = ctx.obj
    spec_data = _read_spec_file(spec)
    inputs_data = _read_json_file(inputs) if inputs else None
    try:
        with state.make_client() as client:
            run_id = client.workflows.run(spec_data, inputs=inputs_data, owner_id=owner)
        _print_data({"workflow_run_id": run_id}, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@workflow_app.command("list")
def workflow_list(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", help="返回条数上限（1-1000）"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
    owner: Optional[str] = typer.Option(None, "--owner", help="按 owner 过滤"),
    status: Optional[str] = typer.Option(None, "--status", help="按状态过滤"),
) -> None:
    """列出工作流运行记录。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.workflows.list(limit=limit, offset=offset, owner_id=owner, status=status)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@workflow_app.command("status")
def workflow_status(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="workflow_run_id"),
) -> None:
    """获取工作流运行状态。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.workflows.get_status(run_id)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@workflow_app.command("cancel")
def workflow_cancel(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="workflow_run_id"),
) -> None:
    """取消工作流运行。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            ok = client.workflows.cancel(run_id)
        _print_data({"cancelled": ok}, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@workflow_app.command("delete")
def workflow_delete(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="workflow_run_id"),
) -> None:
    """删除工作流运行记录。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.workflows.delete(run_id)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@workflow_app.command("subscribe")
def workflow_subscribe(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="workflow_run_id"),
) -> None:
    """订阅工作流事件流（SSE，持续到 workflow_completed/failed/cancelled）。"""
    state: _State = ctx.obj
    try:
        # 不使用 with（流式订阅期间需要保持连接）
        client = state.make_client()
        try:
            for ev in client.workflows.subscribe(run_id):
                _print_data(ev, state.output)
                event_type = ev.get("event") if isinstance(ev, dict) else None
                if event_type in ("workflow_completed", "workflow_failed", "workflow_cancelled"):
                    break
        finally:
            client.close()
    except LomoError as e:
        _handle_lomo_error(e)


# dataset 子命令


@dataset_app.command("list")
def dataset_list(
    ctx: typer.Context,
    owner: Optional[str] = typer.Option(None, "--owner", help="按 owner 过滤"),
    status: Optional[str] = typer.Option(None, "--status", help="按状态过滤"),
    limit: int = typer.Option(100, "--limit", help="返回条数上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
) -> None:
    """列出数据集。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.datasets.list(owner_id=owner, status=status, limit=limit, offset=offset)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("create")
def dataset_create(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", "-n", help="数据集名称"),
    schema: str = typer.Option(..., "--schema", help="schema JSON 文件路径"),
    owner: str = typer.Option(..., "--owner", help="所有者 ID"),
    description: str = typer.Option("", "--description", "-d", help="可选描述"),
) -> None:
    """创建数据集（初始 DRAFT 状态）。"""
    state: _State = ctx.obj
    schema_data = _read_json_file(schema)
    try:
        with state.make_client() as client:
            data = client.datasets.create(name=name, schema=schema_data, owner_id=owner, description=description)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("get")
def dataset_get(
    ctx: typer.Context,
    dataset_id: str = typer.Argument(..., help="dataset_id"),
) -> None:
    """获取数据集详情。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.datasets.get(dataset_id)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("versions")
def dataset_versions(
    ctx: typer.Context,
    dataset_id: str = typer.Argument(..., help="dataset_id"),
) -> None:
    """列出数据集的所有版本。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.datasets.list_versions(dataset_id)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("commit")
def dataset_commit(
    ctx: typer.Context,
    dataset_id: str = typer.Argument(..., help="dataset_id"),
    records: Optional[str] = typer.Option(None, "--records", "-r", help="records JSON 文件路径"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="semver 版本号（默认自动递增）"),
    lineage: Optional[str] = typer.Option(None, "--lineage", help="血缘记录 JSON 文件路径"),
) -> None:
    """提交一个不可变版本。"""
    state: _State = ctx.obj
    records_data = _read_json_file(records) if records else None
    lineage_data = _read_json_file(lineage) if lineage else None
    try:
        with state.make_client() as client:
            data = client.datasets.commit_version(
                dataset_id,
                records=records_data,
                version=version,
                lineage=lineage_data,
            )
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("read")
def dataset_read(
    ctx: typer.Context,
    dataset_id: str = typer.Argument(..., help="dataset_id"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="版本号（默认最新）"),
    batch_size: int = typer.Option(1000, "--batch-size", help="批量大小"),
) -> None:
    """流式读取数据集版本内容（每行一个 JSON 对象）。"""
    state: _State = ctx.obj
    try:
        client = state.make_client()
        try:
            for row in client.datasets.read(dataset_id, version=version, batch_size=batch_size):
                # 流式读取固定输出 JSONL（便于管道处理）
                typer.echo(_json.dumps(row, ensure_ascii=False, default=str))
        finally:
            client.close()
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("deprecate")
def dataset_deprecate(
    ctx: typer.Context,
    dataset_id: str = typer.Argument(..., help="dataset_id"),
    version: str = typer.Argument(..., help="要废弃的版本号"),
) -> None:
    """废弃某版本（不可逆）。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.datasets.deprecate(dataset_id, version)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@dataset_app.command("lineage")
def dataset_lineage(
    ctx: typer.Context,
    target_uri: str = typer.Argument(..., help="目标资源 URI，如 dataset://phm2010/1.0.0"),
    direction: str = typer.Option("upstream", "--direction", "-d", help="upstream | downstream | visualize"),
    depth: int = typer.Option(10, "--depth", help="遍历深度 1-50"),
) -> None:
    """查询血缘图。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.datasets.get_lineage(target_uri, direction=direction, depth=depth)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


# snapshot 子命令


@snapshot_app.command("list")
def snapshot_list(
    ctx: typer.Context,
    created_by: Optional[str] = typer.Option(None, "--by", help="按创建者过滤"),
    git_sha: Optional[str] = typer.Option(None, "--git-sha", help="按 git SHA 过滤"),
    model_uri: Optional[str] = typer.Option(None, "--model-uri", help="按模型 URI 过滤"),
    detail: bool = typer.Option(False, "--detail", help="返回完整字段"),
) -> None:
    """列出实验快照。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.snapshots.list(created_by=created_by, git_sha=git_sha, model_uri=model_uri, detail=detail)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@snapshot_app.command("create")
def snapshot_create(
    ctx: typer.Context,
    model_uri: str = typer.Option(..., "--model-uri", "-m", help="模型 URI，如 model://ltc/1.0.0"),
    by: str = typer.Option(..., "--by", help="创建者 ID"),
    config: Optional[str] = typer.Option(None, "--config", help="实验配置 JSON 文件路径"),
    dataset_versions: Optional[str] = typer.Option(None, "--dataset-versions", help="数据集版本 URI 列表 JSON 文件"),
    metrics: Optional[str] = typer.Option(None, "--metrics", help="指标 JSON 文件路径"),
    notes: str = typer.Option("", "--notes", help="备注"),
) -> None:
    """创建实验快照（后端自动采集 git_sha 与 environment）。"""
    state: _State = ctx.obj
    config_data = _read_json_file(config) if config else None
    dv_data = _read_json_file(dataset_versions) if dataset_versions else None
    metrics_data = _read_json_file(metrics) if metrics else None
    try:
        with state.make_client() as client:
            data = client.snapshots.create(
                model_uri=model_uri,
                created_by=by,
                config=config_data,
                dataset_versions=dv_data,
                metrics=metrics_data,
                notes=notes,
            )
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@snapshot_app.command("get")
def snapshot_get(
    ctx: typer.Context,
    snapshot_id: str = typer.Argument(..., help="snapshot_id"),
) -> None:
    """获取快照详情。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            data = client.snapshots.get(snapshot_id)
        _print_data(data, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


@snapshot_app.command("reproduce")
def snapshot_reproduce(
    ctx: typer.Context,
    snapshot_id: str = typer.Argument(..., help="snapshot_id"),
) -> None:
    """根据快照一键复现：重建 WorkflowSpec 并启动新工作流运行。"""
    state: _State = ctx.obj
    try:
        with state.make_client() as client:
            run_id = client.snapshots.reproduce(snapshot_id)
        _print_data({"workflow_run_id": run_id, "snapshot_id": snapshot_id}, state.output)
    except LomoError as e:
        _handle_lomo_error(e)


# 高级命令 train / predict：封装 dataset workflow snapshot 全流程

# 工作流终态事件（与后端 WorkflowEvent.event_type 枚举对齐）
_WORKFLOW_TERMINAL_EVENTS = (
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
)


def _subscribe_until_terminal(
    client: LomoClient,
    run_id: str,
    output_format: str,
    wait_timeout: Optional[float],
) -> tuple[str, dict[str, Any]]:
    """订阅工作流事件流，直到收到终态事件或超时。

    参数:
        client: 已建立的 LomoClient。
        run_id: workflow_run_id。
        output_format: table / json / raw。table 模式仅 echo 进度行；
            json 模式每行输出一个事件 JSON；raw 模式直接输出原始事件 dict。
        wait_timeout: 最长等待秒数；None 表示无限等待。

    返回:
        (final_event_type, final_event_data)。
        final_event_type 为 ``""`` 表示流自然结束但未收到终态事件。

    抛出:
        LomoTimeoutError: 超过 wait_timeout 仍未到达终态。
    """
    deadline = (time.monotonic() + wait_timeout) if wait_timeout else None
    final_event_type = ""
    final_event_data: dict[str, Any] = {}

    for ev in client.workflows.subscribe(run_id):
        if deadline is not None and time.monotonic() > deadline:
            from lomo.exceptions import LomoTimeoutError

            raise LomoTimeoutError(
                f"等待工作流 {run_id} 完成超时（{wait_timeout}s）；可用 `lomo workflow status {run_id}` 查看当前状态"
            )

        ev_type = ev.get("event") if isinstance(ev, dict) else None
        ev_data = ev.get("data", {}) if isinstance(ev, dict) else {}

        # 进度输出
        if output_format == "raw":
            _print_data(ev, "raw")
        elif output_format == "json":
            typer.echo(_json.dumps(ev, ensure_ascii=False))
        else:  # table
            if ev_type in _WORKFLOW_TERMINAL_EVENTS:
                status = ev_data.get("status", ev_type) if isinstance(ev_data, dict) else ev_type
                typer.echo(f"[event] {ev_type}  status={status}")
            elif ev_type in ("node_started", "node_completed", "node_failed", "node_skipped"):
                node_id = ev_data.get("node_id", "?") if isinstance(ev_data, dict) else "?"
                typer.echo(f"[event] {ev_type:<18} node={node_id}")
            elif ev_type:
                typer.echo(f"[event] {ev_type}")

        if ev_type in _WORKFLOW_TERMINAL_EVENTS:
            final_event_type = ev_type
            final_event_data = ev_data if isinstance(ev_data, dict) else {}
            break

    return final_event_type, final_event_data


def _extract_model_uri(workflow_status: dict[str, Any], output_key: str) -> Optional[str]:
    """从 workflow status 的 outputs 中提取 model_uri。

    支持两种结构:
        1. ``{"outputs": {"model_uri": "model://ltc/1.0.0"}}``（spec.outputs 中声明的 key）
        2. ``{"outputs": {"model": {"uri": "model://ltc/1.0.0"}}}``（artifact 形式）

    若 output_key 指定的字段不存在，返回 None。
    """
    outputs = workflow_status.get("outputs") if isinstance(workflow_status, dict) else None
    if not isinstance(outputs, dict):
        return None

    # 直接命中
    val = outputs.get(output_key)
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        uri = val.get("uri") or val.get("model_uri")
        if isinstance(uri, str):
            return uri

    # 兜底：扫描所有 outputs 字段，寻找第一个 model:// 开头的字符串
    for v in outputs.values():
        if isinstance(v, str) and v.startswith("model://"):
            return v
        if isinstance(v, dict):
            uri = v.get("uri") or v.get("model_uri")
            if isinstance(uri, str) and uri.startswith("model://"):
                return uri
    return None


@train_app.command("run")
def train_run(
    ctx: typer.Context,
    spec: str = typer.Option(
        ...,
        "--spec",
        "-s",
        help="训练 WorkflowSpec 文件路径（.json / .yaml / .yml）",
    ),
    owner: str = typer.Option(
        ...,
        "--owner",
        "-O",
        help="发起人 ID（同时作为默认快照创建者）",
    ),
    inputs: Optional[str] = typer.Option(
        None,
        "--inputs",
        "-i",
        help="工作流输入文件路径（.json / .yaml / .yml），对应 workflow.run(inputs=...)",
    ),
    notes: str = typer.Option(
        "",
        "--notes",
        "-n",
        help="快照备注（仅在自动创建快照时生效）",
    ),
    by: Optional[str] = typer.Option(
        None,
        "--by",
        help="快照创建者（默认与 --owner 相同）",
    ),
    no_snapshot: bool = typer.Option(
        False,
        "--no-snapshot",
        help="跳过自动快照创建（仅跑工作流，不产出 snapshot）",
    ),
    model_uri_key: str = typer.Option(
        "model_uri",
        "--model-uri-key",
        help="从工作流 outputs 提取 model_uri 的字段名（默认 'model_uri'）",
    ),
    wait_timeout: float = typer.Option(
        3600.0,
        "--wait-timeout",
        help="等待工作流完成的最长秒数（默认 3600s；超时后事件流订阅会被中断）",
    ),
    validate_only: bool = typer.Option(
        False,
        "--validate-only",
        help="仅校验 spec，不提交运行",
    ),
) -> None:
    """高级封装：提交训练工作流 + 订阅事件 + 自动产出快照。

    流程:
        1. 解析并校验 WorkflowSpec（``client.workflows.validate``）
        2. 提交运行（``client.workflows.run``），返回 workflow_run_id
        3. 订阅事件流直到终态（completed / failed / cancelled）
        4. 成功时从工作流 outputs 提取 model_uri，自动创建快照
           （除非 ``--no-snapshot``）

    退出码:
        0: 工作流成功完成
        1: 工作流失败 / 取消 / 快照创建失败

    示例::

        lomo train run --spec ltc_train_eval.yaml --owner alice --notes "ltc v1.0.0"
        lomo train run --spec train.json --owner alice --no-snapshot --output json
    """
    state: _State = ctx.obj
    spec_data = _read_spec_file(spec)
    inputs_data = _read_json_file(inputs) if inputs else None
    snapshot_by = by or owner

    try:
        with state.make_client() as client:
            # 1. 校验 spec
            validation = client.workflows.validate(spec_data)
            if isinstance(validation, dict) and validation.get("errors"):
                typer.echo("WorkflowSpec 校验失败：", err=True)
                _print_data(validation, state.output)
                raise typer.Exit(code=EXIT_BUSINESS_ERROR)
            if validate_only:
                typer.echo("WorkflowSpec 校验通过（--validate-only）")
                _print_data(validation, state.output)
                return

            # 2. 提交运行
            run_id = client.workflows.run(spec=spec_data, inputs=inputs_data, owner_id=owner)
            if not run_id:
                typer.echo("后端未返回 workflow_run_id", err=True)
                raise typer.Exit(code=EXIT_BUSINESS_ERROR)
            typer.echo(f"已提交工作流运行：run_id={run_id}")
            typer.echo(f"订阅事件流（超时 {wait_timeout}s）...")

            # 3. 等待终态
            final_event, _final_data = _subscribe_until_terminal(client, run_id, state.output, wait_timeout)

            # 4. 拉取最终 status
            status = client.workflows.get_status(run_id)
            overall_status = status.get("status") if isinstance(status, dict) else final_event

            if final_event != "workflow_completed":
                typer.echo(
                    f"工作流未成功完成：final_event={final_event or '(stream closed)'} status={overall_status}",
                    err=True,
                )
                typer.echo(
                    f"详情可用 `lomo workflow status {run_id}` 查看",
                    err=True,
                )
                _print_data(
                    {"workflow_run_id": run_id, "final_event": final_event, "status": overall_status},
                    state.output,
                )
                raise typer.Exit(code=EXIT_BUSINESS_ERROR)

            typer.echo(f"工作流成功完成：status={overall_status}")

            # 5. 自动创建快照
            if no_snapshot:
                typer.echo("已跳过快照创建（--no-snapshot）")
                _print_data(
                    {"workflow_run_id": run_id, "status": overall_status, "snapshot_id": None},
                    state.output,
                )
                return

            model_uri = _extract_model_uri(status, model_uri_key)
            if not model_uri:
                typer.echo(
                    f"无法从工作流 outputs 提取 model_uri "
                    f"（key={model_uri_key!r}）；请用 `lomo snapshot create` 手动创建快照",
                    err=True,
                )
                _print_data(
                    {"workflow_run_id": run_id, "status": overall_status, "snapshot_id": None, "model_uri": None},
                    state.output,
                )
                raise typer.Exit(code=EXIT_BUSINESS_ERROR)

            snapshot = client.snapshots.create(
                model_uri=model_uri,
                created_by=snapshot_by,
                notes=notes or f"由 train run 自动创建 (run_id={run_id})",
            )
            snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
            typer.echo(f"已创建快照：snapshot_id={snapshot_id}")

            _print_data(
                {
                    "workflow_run_id": run_id,
                    "status": overall_status,
                    "model_uri": model_uri,
                    "snapshot_id": snapshot_id,
                },
                state.output,
            )
    except LomoError as e:
        _handle_lomo_error(e)


@predict_app.command("run")
def predict_run(
    ctx: typer.Context,
    owner: str = typer.Option(
        ...,
        "--owner",
        "-O",
        help="发起人 ID",
    ),
    snapshot_id: Optional[str] = typer.Option(
        None,
        "--snapshot-id",
        help="实验快照 ID（与 --model-uri 二选一；提供时从快照取 model_uri）",
    ),
    model_uri: Optional[str] = typer.Option(
        None,
        "--model-uri",
        "-m",
        help="直接指定 model_uri（与 --snapshot-id 二选一）",
    ),
    spec: Optional[str] = typer.Option(
        None,
        "--spec",
        "-s",
        help="预测 WorkflowSpec 文件路径（.json / .yaml / .yml）；不提供时使用 snapshot.reproduce 内置 spec",
    ),
    input_file: Optional[str] = typer.Option(
        None,
        "--input-file",
        "-i",
        help="预测输入数据文件路径（.json / .yaml / .yml），对应 workflow.run(inputs=...)",
    ),
    wait_timeout: float = typer.Option(
        1800.0,
        "--wait-timeout",
        help="等待工作流完成的最长秒数（默认 1800s）",
    ),
) -> None:
    """高级封装：基于快照提交预测工作流并等待结果。

    模型来源（二选一）:
        --snapshot-id: 从快照详情中提取 model_uri
        --model-uri:   直接指定 model_uri（优先级高于 --snapshot-id）

    spec 来源:
        --spec:       使用用户提供的预测 WorkflowSpec（推荐，可注入 inputs）
        （不提供）:   调用 ``snapshot.reproduce`` 走快照内置 spec；
                      此时 --input-file 会被忽略并给出警告。

    退出码:
        0: 预测工作流成功完成
        1: 工作流失败 / 取消 / 参数错误

    示例::

        lomo predict run --snapshot-id <id> --input-file predict.json --owner alice
        lomo predict run --model-uri model://ltc/1.0.0 --spec predict.yaml --owner alice
    """
    state: _State = ctx.obj

    if not snapshot_id and not model_uri:
        typer.echo("必须提供 --snapshot-id 或 --model-uri 之一", err=True)
        raise typer.Exit(code=EXIT_PARAM_ERROR)

    try:
        with state.make_client() as client:
            # 1. 解析 model_uri
            effective_model_uri = model_uri
            if not effective_model_uri:
                assert snapshot_id is not None  # 由上面参数校验保证
                snapshot = client.snapshots.get(snapshot_id)
                if isinstance(snapshot, dict):
                    effective_model_uri = snapshot.get("model_uri") or (snapshot.get("config", {}) or {}).get(
                        "model_uri"
                    )
                if not effective_model_uri:
                    typer.echo(
                        f"无法从快照 {snapshot_id} 提取 model_uri",
                        err=True,
                    )
                    raise typer.Exit(code=EXIT_BUSINESS_ERROR)

            typer.echo(f"预测模型：model_uri={effective_model_uri}")

            # 2. 提交工作流
            if spec:
                spec_data = _read_spec_file(spec)
                inputs_data = _read_json_file(input_file) if input_file else None
                run_id = client.workflows.run(spec=spec_data, inputs=inputs_data, owner_id=owner)
            else:
                if not snapshot_id:
                    typer.echo(
                        "未提供 --spec 时必须提供 --snapshot-id 以调用 snapshot.reproduce",
                        err=True,
                    )
                    raise typer.Exit(code=EXIT_PARAM_ERROR)
                if input_file:
                    typer.echo(
                        "警告：未提供 --spec 时使用 snapshot.reproduce 内置 spec，--input-file 将被忽略",
                        err=True,
                    )
                run_id = client.snapshots.reproduce(snapshot_id)

            if not run_id:
                typer.echo("后端未返回 workflow_run_id", err=True)
                raise typer.Exit(code=EXIT_BUSINESS_ERROR)

            typer.echo(f"已提交预测工作流：run_id={run_id}")
            typer.echo(f"订阅事件流（超时 {wait_timeout}s）...")

            # 3. 等待终态
            final_event, _final_data = _subscribe_until_terminal(client, run_id, state.output, wait_timeout)

            # 4. 拉取最终 status（含 outputs）
            status = client.workflows.get_status(run_id)
            overall_status = status.get("status") if isinstance(status, dict) else final_event
            outputs = status.get("outputs", {}) if isinstance(status, dict) else {}

            if final_event != "workflow_completed":
                typer.echo(
                    f"预测工作流未成功完成：final_event={final_event or '(stream closed)'} status={overall_status}",
                    err=True,
                )
                typer.echo(
                    f"详情可用 `lomo workflow status {run_id}` 查看",
                    err=True,
                )
                _print_data(
                    {
                        "workflow_run_id": run_id,
                        "final_event": final_event,
                        "status": overall_status,
                        "outputs": outputs,
                    },
                    state.output,
                )
                raise typer.Exit(code=EXIT_BUSINESS_ERROR)

            typer.echo(f"预测完成：status={overall_status}")
            _print_data(
                {
                    "workflow_run_id": run_id,
                    "status": overall_status,
                    "model_uri": effective_model_uri,
                    "outputs": outputs,
                },
                state.output,
            )
    except LomoError as e:
        _handle_lomo_error(e)


# 入口


def main() -> None:
    """CLI 入口函数。"""
    app()


if __name__ == "__main__":
    main()
