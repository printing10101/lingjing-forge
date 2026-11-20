"""全几何类型 DXF（20/20）+ STEP 真实零件端到端全链路覆盖测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cam_validation.voxel_validator import VoxelValidator
from app.config import CamValidationConfig
from app.dxf.process_service import DxfProcessService
from app.step_import.feature_bridge import extract_step_features, run_step_to_nc_pipeline

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURES = sorted((_REPO_ROOT / "data" / "test_fixtures").glob("case*.dxf"))
_COMBOS = [
    ("al_6061", "fanuc_0i"),
    ("ss_304", "gsk_980"),
    ("ti_tc4", "hnc_848"),
    ("steel_45", "siemens_840d"),
]


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("dxf_path", "material", "controller"),
    [
        pytest.param(dxf, *_COMBOS[idx % len(_COMBOS)], id=dxf.stem)
        for idx, dxf in enumerate(_FIXTURES)
    ],
)
def test_all_20_dxf_fixtures_end_to_end_with_voxel_gate(
    tmp_path: Path, dxf_path: Path, material: str, controller: str
):
    """全部 20 种几何类型的 DXF 搭配不同材料与控制器均能出有效 NC 并通过体素+欠切校验。"""
    out_dir = tmp_path / dxf_path.stem
    res = DxfProcessService().process(
        dxf_path=dxf_path,
        output_dir=out_dir,
        postprocessor=controller,
        material=material,
        face_allowance_mm=0.5,
    )
    assert res.success, f"{dxf_path.name} 处理失败: {res.errors}"
    assert res.gcode is not None and res.gcode.success, (
        f"{dxf_path.name} NC 生成失败: {res.gcode.error if res.gcode else 'None'}"
    )
    nc_files = list(out_dir.glob("*.nc"))
    assert nc_files, f"{dxf_path.name} 未输出 .nc 文件"
    nc_text = nc_files[0].read_text(encoding="utf-8")

    feat_json = out_dir / "features.json"
    fdata = json.loads(feat_json.read_text(encoding="utf-8")) if feat_json.exists() else {}
    h = float(fdata.get("overall_height", 10.0) or 10.0)
    stock_length = float(fdata.get("overall_length", 100.0) or 100.0)
    stock_width = float(fdata.get("overall_width", 100.0) or 100.0)
    holes_raw = fdata.get("holes", [])
    expected = [
        {
            "id": hl.get("hole_id", f"H{i + 1:03d}"),
            "type": "hole",
            "x": hl["center_x"],
            "y": hl["center_y"],
            "diameter": hl["diameter"],
            "depth": h + 0.5,
        }
        for i, hl in enumerate(holes_raw)
    ]

    report = VoxelValidator(CamValidationConfig()).validate(
        gcode_text=nc_text,
        controller_type=controller.split("_")[0],
        safe_z=80.0,
        stock_top_z=h + 0.5,
        stock_length=stock_length,
        stock_width=stock_width,
        stock_height=h + 0.5,
        breakthrough_tolerance_mm=3.0,
        expected_features=expected or None,
    )
    assert report.passed, f"{dxf_path.name} 体素校验未通过: {report.warnings}"
    assert report.collision_count == 0
    assert report.undercut_count == 0


@pytest.mark.e2e
def test_step_to_nc_pipeline_on_golden_and_real_parts(tmp_path: Path):
    """STEP 三维实体 → 特征提取 → 切参推荐 → 工艺/NC → 体素+欠切校验全链路打通。"""
    cases = [
        (_REPO_ROOT / "tests" / "golden" / "case_01" / "model.step", 25, "steel_45", "fanuc_0i"),
        (_REPO_ROOT / "data" / "real_parts" / "run_nema23" / "model.step", 9, "al_6061", "fanuc_0i"),
        (_REPO_ROOT / "data" / "real_parts" / "as1_base_plate_180x150x20.stp", 0, "steel_45", "hnc_848_22"),
        (_REPO_ROOT / "data" / "real_parts" / "as1_vise_jaw_50x100x60.step", 0, "ss_304", "siemens_840d"),
    ]
    for step_path, expected_holes, mat, ctl in cases:
        summary = run_step_to_nc_pipeline(
            step_path,
            output_dir=tmp_path / step_path.stem,
            material=mat,
            controller_type=ctl,
        )
        assert summary["hole_count"] == expected_holes, f"{step_path.name} 孔数不匹配"
        assert summary["voxel_passed"] is True, f"{step_path.name} 体素校验失败"
        assert summary["collision_count"] == 0
        assert summary["undercut_count"] == 0


@pytest.mark.e2e
def test_step_assembly_raises_clear_split_hint():
    """多实体装配体 STEP（as1-oc-214.stp）必须显式报错提示拆分，不得静默漏实体。"""
    asm = _REPO_ROOT / "data" / "real_parts" / "as1-oc-214.stp"
    with pytest.raises(ValueError, match="装配体"):
        extract_step_features(asm)
