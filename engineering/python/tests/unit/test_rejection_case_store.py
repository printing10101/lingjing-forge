"""B2 否决案例库与导入脚本测试。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from app.cam_validation.rejection_case_store import (  # noqa: E402
    RejectionCase,
    RejectionCaseStore,
    make_case_id,
    redact_text,
)
from import_rejection_cases import build_cases, material_family, severity_for  # noqa: E402

pytestmark = pytest.mark.unit


def _case(case_id: str = "rc_1", **kwargs) -> RejectionCase:
    defaults = dict(
        case_id=case_id,
        source_task_id="gc_1",
        stage="stability_gate",
        decision="blocked",
        severity="high",
        error_codes=["UNSTABLE_FEATURES"],
        evidence_summary="特征 plane_top 颤振不稳定，禁止生成 G 代码",
    )
    defaults.update(kwargs)
    return RejectionCase(**defaults)


def test_redact_text_removes_paths_emails_and_ids():
    raw = r"失败于 D:\客户\ABC公司\part.step，联系人 a.b@corp.com，批次 20261015001，IP 192.168.1.7"
    cleaned, notes = redact_text(raw)
    assert "<path>" in cleaned and "D:\\" not in cleaned
    assert "<email>" in cleaned and "@corp.com" not in cleaned
    assert "<long_id>" in cleaned and "20261015001" not in cleaned
    assert "<ip>" in cleaned and "192.168" not in cleaned
    assert set(notes) == {"path", "email", "ip", "long_id"}


def test_validate_rejects_unredacted_or_invalid_cases():
    leaked = _case(evidence_summary=r"输出目录 D:\客户\零件\a.nc 越界")
    with pytest.raises(ValueError, match="未脱敏"):
        leaked.validate()

    with pytest.raises(ValueError, match="非法 severity"):
        _case(severity="urgent").validate()
    with pytest.raises(ValueError, match="白名单"):
        _case(params={"customer_part_no": 1.0}).validate()
    with pytest.raises(ValueError, match="已脱敏"):
        _case(desensitized=False).validate()
    with pytest.raises(ValueError, match="error_codes"):
        _case(error_codes=[]).validate()


def test_store_append_is_idempotent_and_reports_stats(tmp_path: Path):
    store = RejectionCaseStore(tmp_path / "cases.jsonl")
    assert store.append([_case("rc_1"), _case("rc_2", severity="critical")]) == 2
    # 重复导入同一批不新增
    assert store.append([_case("rc_1"), _case("rc_2")]) == 0
    assert len(store.load()) == 2
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["by_severity"]["critical"] == 1
    assert stats["by_error_code"]["UNSTABLE_FEATURES"] == 2


def test_make_case_id_is_stable_and_distinct():
    assert make_case_id("a", "b") == make_case_id("a", "b")
    assert make_case_id("a", "b") != make_case_id("a", "c")


def _write_db(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE failure_cases (case_id TEXT, task_id TEXT, outcome TEXT, source TEXT,"
        " controller_type TEXT, material_name TEXT, error_codes TEXT, error_messages TEXT,"
        " gcode_text TEXT, total_features INT, unstable_features INT, created_at REAL)"
    )
    rows = [
        ("fc_1", "gc_1", "failure", "unstable_features", "fanuc_0i", "45#钢", '["UNSTABLE_FEATURES"]',
         json.dumps(["特征 plane_top(plane) 颤振预测结果为不稳定（axial_depth=6.0mm, limit_depth=2.0mm, method=analytical）"], ensure_ascii=False),
         "", 1, 1, 1790000000.0),
        ("fc_2", "gc_2", "failure", "unstable_features", "siemens_840d", "铝合金", '["UNSTABLE_FEATURES"]',
         json.dumps(["特征 pocket 颤振不稳定（axial_depth=1.2mm, limit_depth=2.4mm）"], ensure_ascii=False),
         "", 1, 1, 1790000100.0),
        ("fc_3", "gc_3", "success", "", "", "", "[]", "[]", "", 1, 0, 1790000200.0),
    ]
    connection.executemany("INSERT INTO failure_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def test_build_cases_maps_real_rows(tmp_path: Path):
    db_path = tmp_path / "failure_cases.db"
    _write_db(db_path)
    cases = build_cases(db_path, per_code=5)
    assert len(cases) == 2  # success 行不入库
    by_depth = {case.params.get("axial_depth_mm"): case for case in cases}
    critical = by_depth[6.0]
    assert critical.severity == "critical"  # 6.0/2.0 = 3 倍超限
    assert critical.stage == "stability_gate"
    assert critical.material_family == "steel_carbon"
    assert critical.controller_type == "fanuc_0i"
    medium = by_depth[1.2]
    assert medium.severity == "medium"  # 低于极限
    assert medium.material_family == "aluminium"


def test_per_code_limit_and_helpers():
    assert severity_for(3.0, 2.0) == "critical"
    assert severity_for(2.4, 2.0) == "high"
    assert severity_for(None, 2.0) == "high"
    assert material_family("TC4钛合金") == "titanium"
    assert material_family("POM") == "unknown"
