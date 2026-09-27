"""工艺规则导入导出/备份 mixin（从 rule_db 拆出）。"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from typing import Any
from collections.abc import Callable

from app.config.limits import MAX_EXPORT_LIMIT
from app.database._constants import CURRENT_FORMAT_VERSION, DB_DIR
from app.database._models import ProcessRule, RuleGroup
from app.database._version import check_version_compatibility, get_project_version

logger = logging.getLogger(__name__)


class _TransferMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _find_group_by_name: Callable[..., Any]
    _get_conn: Callable[..., Any]
    _now: Callable[..., Any]
    create_group: Callable[..., Any]
    create_rule: Callable[..., Any]
    list_groups: Callable[..., Any]
    list_rules: Callable[..., Any]
    db_path: Any

    def export_rules(self, output_path: str) -> dict[str, Any]:
        """导出所有规则和分组到JSON文件"""
        rules = self.list_rules(limit=MAX_EXPORT_LIMIT)
        groups = self.list_groups()

        project_version = get_project_version()

        data = {
            "version": project_version,
            "format_version": CURRENT_FORMAT_VERSION,
            "exported_at": self._now(),
            "groups": [g.to_dict() for g in groups],
            "rules": [r.to_dict() for r in rules],
            "total_rules": len(rules),
            "total_groups": len(groups),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"导出规则到: {output_path} ({len(rules)} 条规则, {len(groups)} 个分组, 版本: {project_version})")
        return data

    def import_rules(self, input_path: str) -> dict[str, Any]:
        """从JSON文件导入规则和分组

        Returns:
            导入结果字典，包含 imported_groups, imported_rules, total_rules, total_groups,
            version_check (版本检查结果: compatible/warning/incompatible), version_message (版本提示信息)
        """
        # M14 修复：json.load 包裹 try/except，外部文件可能损坏
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"规则文件加载失败（文件损坏或格式错误）: {input_path}: {e}") from e

        # 版本兼容性检查
        current_version = get_project_version()
        import_version = data.get("version", "1.0")
        is_compatible, version_message = check_version_compatibility(import_version, current_version)

        if not is_compatible:
            logger.warning("规则导入版本不兼容: %s", version_message)
            return {
                "imported_groups": 0,
                "imported_rules": 0,
                "total_rules": len(data.get("rules", [])),
                "total_groups": len(data.get("groups", [])),
                "version_check": "incompatible",
                "version_message": version_message,
                "error": version_message,
            }

        if import_version != current_version:
            logger.info("规则导入版本提示: %s", version_message)

        imported_groups = 0
        imported_rules = 0
        group_id_map: dict[int, int] = {}

        self._get_conn()

        for g_data in data.get("groups", []):
            old_id = g_data.get("id")
            group = RuleGroup.from_dict(g_data)
            group.id = None
            group.created_at = None
            group.updated_at = None

            existing = self._find_group_by_name(group.name)
            if existing:
                group_id_map[old_id] = existing.id
            else:
                new_group = self.create_group(group)
                group_id_map[old_id] = new_group.id
                imported_groups += 1

        for r_data in data.get("rules", []):
            rule = ProcessRule.from_dict(r_data)
            rule.id = None
            rule.created_at = None
            rule.updated_at = None

            if rule.group_id and rule.group_id in group_id_map:
                rule.group_id = group_id_map[rule.group_id]

            self.create_rule(rule)
            imported_rules += 1

        version_check = "compatible" if import_version == current_version else "warning"

        logger.info("导入规则完成: %s 个分组, %s 条规则", imported_groups, imported_rules)
        return {
            "imported_groups": imported_groups,
            "imported_rules": imported_rules,
            "total_rules": len(data.get("rules", [])),
            "total_groups": len(data.get("groups", [])),
            "version_check": version_check,
            "version_message": version_message,
        }

    def backup_database(self, backup_path: str | None = None) -> str:
        """备份数据库到指定路径"""
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = str(DB_DIR / f"process_rules_backup_{timestamp}.db")

        conn = self._get_conn()
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        shutil.copy2(self.db_path, backup_path)
        logger.info("数据库备份完成: %s", backup_path)
        return backup_path
