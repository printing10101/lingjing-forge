"""工作流 YAML 模板系统.

提供内置模板加载、自定义模板注册、模板 → WorkflowSpec 转换。

模板格式见 ``builtin/`` 目录下的 YAML 文件。每个模板是一个独立 YAML，
顶层结构对齐 :class:`app.contracts.task.WorkflowSpec`：

    name: <str>                       # 工作流名称（必填）
    version: <str>                    # 语义化版本，默认 1.0.0
    description: <str>                # 模板描述（可选，仅元信息）
    inputs:                           # 工作流级输入 artifact
      <name>:
        type: dataset|model|report|metrics|file
        uri: <str>
        metadata: { ... }
    outputs:                          # 工作流级输出引用 ${node.output}
      <name>: ${node_id.output_name}
    edges:
      - upstream: <node_id>
        downstream: <node_id>
    nodes:
      - node_id: <str>
        task_type: <str>              # 必须在 TaskRegistry 中已注册
        params: { ... }
        inputs:                       # 节点输入 artifact 引用
          <name>: ${upstream_node.output_name}
        retry: <int>                  # 默认 0
        timeout_seconds: <int>        # 默认 3600
    metadata:
      max_concurrent: <int>           # 可选，runner 并发上限
      tags: [<str>, ...]
"""

from app.workflow.templates.loader import (
    TemplateNotFoundError,
    WorkflowTemplate,
    list_builtin_templates,
    load_builtin_template,
    load_template_from_file,
    template_to_spec,
)

__all__ = [
    "TemplateNotFoundError",
    "WorkflowTemplate",
    "list_builtin_templates",
    "load_builtin_template",
    "load_template_from_file",
    "template_to_spec",
]
