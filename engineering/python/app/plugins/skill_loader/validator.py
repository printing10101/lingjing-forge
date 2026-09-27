"""技能文件解析器和验证器。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MarkdownSkillParser:
    """Markdown 技能文件解析器。"""

    YAML_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    CODE_BLOCK_PATTERN = re.compile(r"```(\w+)\s*\n(.*?)\n```", re.DOTALL)
    HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)

    @classmethod
    def parse(cls, file_path: str) -> dict[str, Any] | None:
        """解析技能文件。"""
        path = Path(file_path)
        if not path.exists():
            logger.warning("Skill file not found: %s", file_path)
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            logger.error(
                "Failed to read skill file %s: %s",
                file_path,
                e,
                exc_info=True,
            )
            return None

        result: dict[str, Any] = {
            "metadata": {},
            "code_blocks": [],
            "body": "",
            "raw_content": content,
        }

        fm_match = cls.YAML_FRONTMATTER_PATTERN.match(content)
        if fm_match:
            result["metadata"] = cls._parse_frontmatter(fm_match.group(1))
            body_start = fm_match.end()
        else:
            legacy_meta = cls._parse_legacy_metadata(content)
            if legacy_meta:
                result["metadata"] = legacy_meta
                body_start = cls._find_body_start(content)
            else:
                body_start = 0

        result["body"] = content[body_start:].strip()
        result["code_blocks"] = cls.CODE_BLOCK_PATTERN.findall(content)

        if not result["metadata"].get("skill_id"):
            result["metadata"]["skill_id"] = path.stem
        if not result["metadata"].get("name"):
            result["metadata"]["name"] = result["metadata"]["skill_id"]

        return result

    @classmethod
    def _parse_frontmatter(cls, fm_text: str) -> dict[str, Any]:
        """解析 YAML frontmatter。"""
        metadata: dict[str, Any] = {}
        for line in fm_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if value.startswith("[") and value.endswith("]"):
                    items = value[1:-1].split(",")
                    metadata[key] = [item.strip().strip("\"'") for item in items if item.strip()]
                elif value.lower() == "true":
                    metadata[key] = True
                elif value.lower() == "false":
                    metadata[key] = False
                elif value == "":
                    metadata[key] = None
                else:
                    try:
                        metadata[key] = int(value)
                    except ValueError:
                        try:
                            metadata[key] = float(value)
                        except ValueError:
                            metadata[key] = value.strip("\"'")
        return metadata

    @classmethod
    def _parse_legacy_metadata(cls, content: str) -> dict[str, Any] | None:
        """解析旧版元数据格式。"""
        table_pattern = re.compile(r"\|\s*字段\s*\|\s*值\s*\|\s*\n\|[-| ]+\|\s*\n((?:\|.*\|\s*\n?)+)")
        match = table_pattern.search(content)
        if not match:
            return None

        metadata: dict[str, Any] = {}
        field_map = {
            "技能名称": "display_name",
            "英文名称": "name",
            "适用场景": "applicable_tasks_text",
            "前置条件": "prerequisites",
            "API端点": "api_endpoints",
            "依赖模块": "dependencies_text",
        }

        for row in match.group(1).strip().split("\n"):
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if len(cells) >= 2:
                field = field_map.get(cells[0], cells[0])
                metadata[field] = cells[1]

        if "applicable_tasks_text" in metadata:
            text = metadata.pop("applicable_tasks_text")
            metadata["applicable_tasks"] = cls._extract_task_types(text)
        if "dependencies_text" in metadata:
            metadata["dependencies"] = [d.strip() for d in metadata.pop("dependencies_text").split("、")]

        return metadata

    @classmethod
    def _extract_task_types(cls, text: str) -> list[str]:
        """提取任务类型。"""
        type_keywords = {
            "预测": "prediction",
            "训练": "training",
            "分析": "analysis",
            "优化": "optimization",
            "分类": "classification",
            "检测": "detection",
            "振动": "vibration_analysis",
            "磨损": "wear_analysis",
            "寿命": "rul_prediction",
        }
        tasks = []
        for cn, en in type_keywords.items():
            if cn in text:
                tasks.append(en)
        return tasks if tasks else ["*"]

    @classmethod
    def _find_body_start(cls, content: str) -> int:
        """查找正文起始位置。"""
        table_match = re.search(r"\n---\s*\n", content)
        if table_match:
            return table_match.end()
        heading_match = re.search(r"^#+\s", content, re.MULTILINE)
        if heading_match:
            return heading_match.start()
        return 0
