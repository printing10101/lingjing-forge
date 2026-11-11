"""行业标准条款级对齐（赛题任务要求②的后半段）。

要解决的问题
------------
"对齐国标"这句话有两种写法，差别很大：

- 能站住的写法：引用一个**当前有效**的标准编号 + 说明对齐到哪个主题 + 承认条款级
  引用尚未取得（标准全文付费），不得声称"符合第 X 条"。
- 站不住的写法：拿一个已废止的标准当依据；或把项目自定义的校验编号（如 K001–K009）
  包装成国标条款；或干脆把"有相关国标"说成"符合国标"。

本模块把第二种可能性做成代码层面的硬拦截：

1. **日期感知的有效性**。标准是否有效取决于查询日期，不是写死状态。GB 15760-2025
   发布于 2025-08-01、实施于 2026-08-01；在 2026-07-01 查询它属于"尚未实施"，
   在 2026-11-10 查询才"现行"。
2. **废止引用直接阻断**。引用 `GB 15760-2004` 会被判定为 repealed，并给出替代标准。
3. **条款级缺失显式化**。未取得全文时 `clause_level` 恒为 `unverified`，
   `may_claim_compliance` 恒为 False——任何下游输出都不许写"符合 XX 条"。
4. **无对应国标的规则记为缺口**，不硬凑映射（颤振判据、K 码校验均无直接国标）。

零第三方依赖，标准数据与映射分别放在同目录的 `standards.json` / `rule_map.json`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent

#: 状态判定结果
VALID = "current"
NOT_YET = "not_yet_effective"
REPEALED = "repealed"


def _as_date(value: str | date | None) -> date:
    if value is None:
        raise ValueError("as_of date is required")
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


@dataclass(frozen=True)
class Standard:
    """一条标准元数据（仅公开信息，不含全文）。"""

    code: str
    title: str
    status: str
    published: str
    effective: str
    replaced_by: str | None
    replaces: tuple[str, ...]
    scope_summary: str
    url: str
    full_text: str
    last_review: str | None = None
    review_conclusion: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Standard":
        return cls(
            code=data["code"],
            title=data["title"],
            status=data["status"],
            published=data["published"],
            effective=data["effective"],
            replaced_by=data.get("replaced_by"),
            replaces=tuple(data.get("replaces") or ()),
            scope_summary=data.get("scope_summary", ""),
            url=data.get("url", ""),
            full_text=data.get("full_text", "not_acquired"),
            last_review=data.get("last_review"),
            review_conclusion=data.get("review_conclusion"),
        )

    def status_as_of(self, as_of: date) -> str:
        """按查询日期判定有效状态。

        优先级：声明废止 > 尚未实施 > 现行。声明废止以标准自身的 replaced_by 为准，
        另外兼顾 replaced_by 指向的标准的生效日期。
        """
        if self.status == "repealed":
            return REPEALED
        if as_of < _as_date(self.effective):
            return NOT_YET
        return VALID


class StandardRegistry:
    """标准元数据 + 规则映射 + 对齐审计。"""

    def __init__(self, standards: dict[str, Standard], rules: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        self._standards = standards
        self._rules = {r["rule_id"]: r for r in rules}
        self.meta = meta

    # ------------------------------------------------------------------ 构造
    @classmethod
    def load(cls, data_dir: Path | str = DATA_DIR) -> "StandardRegistry":
        base = Path(data_dir)
        raw = json.loads((base / "standards.json").read_text(encoding="utf-8"))
        rules = json.loads((base / "rule_map.json").read_text(encoding="utf-8"))["rules"]
        standards = {item["code"]: Standard.from_dict(item) for item in raw["standards"]}
        meta = {
            "verified_at": raw.get("verified_at"),
            "verification_source": raw.get("verification_source"),
            "full_text_policy": raw.get("full_text_policy"),
        }
        return cls(standards, rules, meta)

    # ------------------------------------------------------------------ 查询
    def get(self, code: str) -> Standard:
        if code not in self._standards:
            raise KeyError(f"未登记的标准：{code}")
        return self._standards[code]

    def codes(self) -> list[str]:
        return sorted(self._standards)

    def rules(self) -> list[dict[str, Any]]:
        return list(self._rules.values())

    # ------------------------------------------------------------------ 对齐
    def resolve_rule(self, rule_id: str, as_of: str | date = "2026-11-10") -> dict[str, Any]:
        """把一条内部规则解析成「可用 / 被拦截 / 缺口」三种结论之一。

        返回字段刻意包含 `may_claim_compliance`：下游（智能体、文档、答辩 PPT）只能
        依据该布尔值决定能不能写"符合国标"。
        """
        if rule_id not in self._rules:
            raise KeyError(f"未登记的规则：{rule_id}")
        rule = self._rules[rule_id]
        day = _as_date(as_of)
        base = {
            "rule_id": rule_id,
            "rule_name": rule["rule_name"],
            "spec_ref": rule.get("spec_ref"),
            "alignment": rule.get("alignment"),
            "topic": rule.get("topic"),
            "as_of": day.isoformat(),
        }

        if not rule.get("standard"):
            return {
                **base,
                "usable": False,
                "state": "gap",
                "standard": None,
                "clause_ref": None,
                "clause_level": "n/a",
                "may_claim_compliance": False,
                "message": rule.get("clause_note", "无对应国家标准，记为缺口"),
            }

        standard = self.get(rule["standard"])
        state = standard.status_as_of(day)
        usable = state == VALID
        message = ""
        if state == REPEALED:
            message = (
                f"{standard.code} 已废止"
                + (f"，由 {standard.replaced_by} 代替" if standard.replaced_by else "")
                + "；不得作为现行依据引用"
            )
        elif state == NOT_YET:
            message = f"{standard.code} 尚未实施（实施日期 {standard.effective}），当前日期不可作为现行依据"
        else:
            message = rule.get("clause_note", "")

        return {
            **base,
            "usable": usable,
            "state": state,
            "standard": {
                "code": standard.code,
                "title": standard.title,
                "status_declared": standard.status,
                "status_as_of": state,
                "effective": standard.effective,
                "replaced_by": standard.replaced_by,
                "url": standard.url,
                "scope_summary": standard.scope_summary,
            },
            "clause_ref": rule.get("clause_ref"),
            # 全文未取得 → 条款级对齐一律 unverified
            "clause_level": "unverified" if standard.full_text == "not_acquired" else "verified",
            "may_claim_compliance": False,
            "message": message,
        }

    def audit(self, as_of: str | date = "2026-11-10") -> dict[str, Any]:
        """全量审计：每条规则的对齐结论 + 汇总。"""
        results = [self.resolve_rule(rid, as_of) for rid in self._rules]
        return {
            "as_of": _as_date(as_of).isoformat(),
            "verified_at": self.meta.get("verified_at"),
            "verification_source": self.meta.get("verification_source"),
            "counts": {
                "total": len(results),
                "usable": sum(1 for r in results if r["usable"]),
                "blocked": sum(1 for r in results if r["state"] in {REPEALED, NOT_YET}),
                "gaps": sum(1 for r in results if r["state"] == "gap"),
                "clause_level_unverified": sum(1 for r in results if r["clause_level"] == "unverified"),
            },
            "results": results,
        }

    #: 无依据的合规表述（全文未取得时一律命中）
    RISKY_PHRASES = (
        "符合国标",
        "符合国家标准",
        "符合 GB",
        "满足 GB",
        "依据 GB 第",
        "符合第",
        "依据第",
    )

    def assert_no_compliance_claim(self, text: str) -> list[str]:
        """检测文本里是否存在无依据的「符合国标 / 某条」表述。

        返回命中的短语列表（空 = 通过）。用于文档与回答出稿前的最后一道检查：
        全文未取得时，允许写"对齐某主题"，不允许写"符合某标准某条"。
        条数是保守的——宁可误报让人改口，不可放过越权承诺。
        """
        return [p for p in self.RISKY_PHRASES if p in text]
