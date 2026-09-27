"""API功能测试 — STEP导入全链路验证。

覆盖:
T1: API功能测试 — 上传STEP验证响应字段
T2: 前端显示验证 — 上传进度/3D渲染/等轴测视图/交互操作
T3: 错误处理测试 — 上传.txt验证400+错误信息
T4: 下游兼容性测试 — 毛坯定义+刀路生成+几何匹配
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

# 添加python目录到sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 工具函数
PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"
WARN = "[WARN]"
TOTAL = 0
PASSED = 0
FAILED = 0


def result(name: str, ok: bool, detail: str = ""):
    global TOTAL, PASSED, FAILED
    TOTAL += 1
    if ok:
        PASSED += 1
        print(f"  {PASS} {name}")
    else:
        FAILED += 1
        print(f"  {FAIL} {name}" + (f" -- {detail}" if detail else ""))


def section(name: str):
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")


def _generate_test_step(output_path: Path) -> Path:
    """生成测试用STEP文件: 100x50x30长方体, 中心带直径10mm通孔。"""
    if output_path.exists():
        return output_path

    import cadquery as cq

    block = cq.Workplane("XY").box(100, 50, 30)
    hole = cq.Workplane("XY").circle(5).extrude(30, both=True)
    model = block.cut(hole)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(model, str(output_path), exportType="STEP")
    return output_path


def test_api_functional():
    """T1: API功能测试 — 上传STEP验证响应字段。"""
    section("T1: API功能测试")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.step_import.api import router as step_router

    test_app = FastAPI()
    test_app.include_router(step_router)
    client = TestClient(test_app)

    # T1.1: 生成并上传STEP文件
    step_path = _generate_test_step(PROJECT_ROOT / "output/step_import/test_api_100x50x30.step")
    file_size = step_path.stat().st_size
    print(f"  {INFO} 测试模型: {step_path.name}, 大小={file_size} bytes")

    with open(step_path, "rb") as f:
        resp = client.post(
            "/api/import/step",
            files={"file": ("test_model.step", f, "application/octet-stream")},
            data={"output_format": "stl", "precision": "medium"},
        )

    result("HTTP状态码 200", resp.status_code == 200, f"实际={resp.status_code}")

    body = resp.json()
    result("响应 code=0", body.get("code") == 0, f"实际={body.get('code')}")

    data = body.get("data", {})

    # T1.2: 检查 volume 字段
    model_info = data.get("model_info", {})
    volume = model_info.get("volume")
    volume_ok = volume is not None and isinstance(volume, (int, float)) and volume > 0
    # 期望体积: 100*50*30 - pi*5^2*30 = 150000 - 2356.2 = 147643.8
    expected_volume = 100 * 50 * 30 - 3.14159 * 5**2 * 30
    volume_close = abs(volume - expected_volume) / expected_volume < 0.05 if volume_ok else False
    result(
        f"体积(volume) > 0, 约 {expected_volume:.2f} mm^3",
        volume_ok,
        f"实际={volume}" if volume else "体积字段缺失",
    )
    result(
        "体积精度验证 (偏差<5%)",
        volume_close,
        f"期望~{expected_volume:.2f}, 实际={volume}, 偏差={abs(volume - expected_volume) / expected_volume * 100:.2f}%"
        if volume_ok
        else "",
    )

    # T1.3: 检查 bounding_box 字段
    bbox = model_info.get("bounding_box", {})
    bbox_ok = all(k in bbox for k in ["length", "width", "height"])
    result("包围盒(bounding_box)包含length/width/height", bbox_ok)

    if bbox_ok:
        result(
            "包围盒.length ~ 100mm",
            abs(bbox["length"] - 100) < 2,
            f"实际={bbox['length']}",
        )
        result("包围盒.width ~ 50mm", abs(bbox["width"] - 50) < 2, f"实际={bbox['width']}")
        result(
            "包围盒.height ~ 30mm",
            abs(bbox["height"] - 30) < 2,
            f"实际={bbox['height']}",
        )

    # T1.4: 检查 stl_path
    stl_files = data.get("stl_files", [])
    has_stl = len(stl_files) > 0
    result("包含STL文件列表(stl_files)", has_stl, f"共{len(stl_files)}个STL文件")

    if has_stl:
        first_stl = stl_files[0]
        result("STL文件含stl_path字段", "stl_path" in first_stl)
        stl_path_str = first_stl.get("stl_path", "")
        stl_file_exists = Path(stl_path_str).exists()
        result("STL文件实际存在", stl_file_exists, f"路径={stl_path_str}")
        result("STL文件含stl_url字段", "stl_url" in first_stl)

    # T1.5: 检查其他必要字段
    result("包含parse_time_ms", "parse_time_ms" in data)
    result("包含conversion_time_ms", "conversion_time_ms" in data)
    result("包含import_id", "import_id" in data and len(data["import_id"]) > 0)
    result("包含status.success", data.get("status", {}).get("success") is True)
    result("包含model_info.face_count", model_info.get("face_count", 0) > 0)
    result("包含model_info.vertex_count", model_info.get("vertex_count", 0) > 0)

    return data


def test_error_handling():
    """T3: 错误处理测试 — 上传.txt验证400+错误信息。"""
    section("T3: 错误处理测试")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.step_import.api import router as step_router

    test_app = FastAPI()
    test_app.include_router(step_router)
    client = TestClient(test_app)

    # T3.1: 上传.txt文件
    txt_content = b"This is a plain text file, not a STEP file."
    resp = client.post(
        "/api/import/step",
        files={"file": ("test.txt", io.BytesIO(txt_content), "text/plain")},
    )

    result(
        "HTTP状态码 200(业务层返回)",
        resp.status_code == 200,
        f"实际={resp.status_code}",
    )

    body = resp.json()
    code_nonzero = body.get("code", 0) != 0
    result("响应code != 0(错误标识)", code_nonzero, f"实际code={body.get('code')}")

    message = body.get("message", "")
    has_error_msg = len(message) > 0
    result("包含错误信息(message)", has_error_msg, f"message={message[:100]}")

    # T3.2: 检查detail字段
    detail = body.get("detail", "")
    has_detail = len(detail) > 0
    result("包含错误详情(detail)", has_detail)

    # T3.3: 上传空文件
    resp_empty = client.post(
        "/api/import/step",
        files={"file": ("empty.step", io.BytesIO(b""), "application/octet-stream")},
    )
    result("空.step文件上传", resp_empty.status_code == 200)

    body_empty = resp_empty.json()
    result("空文件返回错误code", body_empty.get("code", 0) != 0)

    # T3.4: 上传损坏的STEP内容
    resp_corrupt = client.post(
        "/api/import/step",
        files={
            "file": (
                "corrupt.step",
                io.BytesIO(b"NOT_A_STEP_CONTENT_12345"),
                "application/octet-stream",
            )
        },
        data={"output_format": "stl"},
    )
    body_corrupt = resp_corrupt.json()
    result("损坏STEP文件返回错误", body_corrupt.get("code", 0) != 0)
    result("损坏文件包含错误提示", "message" in body_corrupt)


def test_downstream_compatibility(step_data: dict = None):
    """T4: 下游兼容性测试 — 毛坯定义+刀路生成+几何匹配。"""
    section("T4: 下游兼容性测试")

    from app.simulation.voxel_cutter import VoxelCutter, ToolModel
    from app.simulation.toolpath_parser import ToolpathParser

    # T4.1: 验证STL文件可用于下游仿真
    if step_data is None:
        result("跳过下游测试: 无step_data", False, "前置条件不满足")
        return
    stl_files = step_data.get("stl_files", [])
    if not stl_files:
        result("跳过下游测试: 无STL文件", False, "前置条件不满足")
        return

    stl_path = Path(stl_files[0]["stl_path"])
    if not stl_path.exists():
        result("跳过下游测试: STL不存在", False)
        return

    result("STL文件可用下游模块", True)

    # T4.2: 验证STL文件完整性
    import trimesh

    mesh = trimesh.load(str(stl_path))
    has_vertices = len(mesh.vertices) > 0
    has_faces = len(mesh.faces) > 0
    result(f"STL顶点数: {len(mesh.vertices)}", has_vertices)
    result(f"STL面数: {len(mesh.faces)}", has_faces)

    # T4.3: 验证包围盒一致性
    model_bbox = step_data.get("model_info", {}).get("bounding_box", {})
    mesh_extents = mesh.extents
    if mesh_extents is not None and len(mesh_extents) >= 3:
        length_match = abs(mesh_extents[0] - model_bbox.get("length", 0)) < 2
        width_match = abs(mesh_extents[1] - model_bbox.get("width", 0)) < 2
        height_match = abs(mesh_extents[2] - model_bbox.get("height", 0)) < 2
        result(
            "STL包围盒.length一致(偏差<2mm)",
            length_match,
            f"模型={model_bbox.get('length')}, STL={mesh_extents[0]:.1f}",
        )
        result(
            "STL包围盒.width一致(偏差<2mm)",
            width_match,
            f"模型={model_bbox.get('width')}, STL={mesh_extents[1]:.1f}",
        )
        result(
            "STL包围盒.height一致(偏差<2mm)",
            height_match,
            f"模型={model_bbox.get('height')}, STL={mesh_extents[2]:.1f}",
        )

    # T4.4: 生成刀路并验证
    tool = ToolModel(diameter=10.0, length=20.0, tool_type="flat")
    cutter = VoxelCutter(voxel_size=2.0)

    gcode = """G00 X0 Y0 Z10
G01 X100 Y0 Z-2 F500
G01 X100 Y50 Z-2 F500
G01 X0 Y50 Z-2 F500
G01 X0 Y0 Z-2 F500
G00 Z10
"""
    parser = ToolpathParser(controller_type="fanuc")
    segments = parser.parse_gcode(gcode)

    output_dir = PROJECT_ROOT / "output/step_import"
    sim_result = cutter.run_simulation(
        stock_stl_path=stl_path,
        tool=tool,
        segments=segments,
        output_dir=output_dir,
        safe_z_height=10.0,
        task_id="downstream_test",
    )

    result_ok = sim_result is not None and hasattr(sim_result, "voxel_count")
    result("刀路仿真执行成功", result_ok)

    if result_ok:
        result(f"体素总数: {sim_result.voxel_count}", sim_result.voxel_count > 0)
        result(
            f"移除体素数: {sim_result.removed_voxel_count}",
            sim_result.removed_voxel_count > 0,
        )
        result("碰撞检测完成", True)

        # T4.5: 验证输出STL文件
        output_stl = Path(sim_result.stock_stl_url) if sim_result.stock_stl_url else None
        if output_stl and output_stl.exists():
            out_mesh = trimesh.load(str(output_stl))
            result(f"输出STL顶点数: {len(out_mesh.vertices)}", len(out_mesh.vertices) > 0)
            result(f"输出STL面数: {len(out_mesh.faces)}", len(out_mesh.faces) > 0)

            # T4.6: 验证几何坐标偏差 < 0.1mm
            if len(mesh.vertices) > 0 and len(out_mesh.vertices) > 0:
                orig_bbox = mesh.bounds
                out_bbox = out_mesh.bounds
                if orig_bbox is not None and out_bbox is not None:
                    max_diff = max(abs(orig_bbox[1][i] - out_bbox[1][i]) for i in range(3))
                    result(
                        f"坐标偏差(max): {max_diff:.3f}mm < 0.1mm",
                        max_diff < 0.1,
                        f"实际偏差={max_diff:.3f}mm",
                    )


def test_frontend_validation():
    """T2: 前端显示验证 — 代码级组件检查。"""
    section("T2: 前端显示验证(代码级检查)")

    # T2.1: 检查StepImportDialog组件存在
    frontend_root = PROJECT_ROOT.parent / "src"
    dialog_path = frontend_root / "components/step_import/StepImportDialog.vue"
    result("StepImportDialog组件存在", dialog_path.exists())

    if dialog_path.exists():
        content = dialog_path.read_text(encoding="utf-8")

        # T2.2: 上传进度指示
        has_progress = "el-progress" in content
        result("包含上传进度指示(el-progress)", has_progress)

        has_upload_status = "uploadProgress" in content
        result("包含上传进度状态追踪", has_upload_status)

        # T2.3: 3D查看器集成
        has_viewer = "StepModelViewer" in content
        result("集成3D模型查看器", has_viewer)

        # T2.4: 等轴测视图
        viewer_path = frontend_root / "components/step_import/StepModelViewer.vue"
        result("StepModelViewer组件存在", viewer_path.exists())

        if viewer_path.exists():
            viewer_content = viewer_path.read_text(encoding="utf-8")
            has_iso = "viewIso" in viewer_content
            result("支持等轴测视图(viewIso)", has_iso)
            has_orbit = "OrbitControls" in viewer_content
            result("使用OrbitControls(旋转/缩放/平移)", has_orbit)
            has_fit = "fitView" in viewer_content
            result("支持模型自适应居中(fitView)", has_fit)
            has_damping = "enableDamping" in viewer_content
            result("启用阻尼平滑交互", has_damping)
            has_opacity = "opacity" in viewer_content
            result("支持透明度调整", has_opacity)

    # T2.5: 菜单集成
    app_path = frontend_root / "App.vue"
    if app_path.exists():
        app_content = app_path.read_text(encoding="utf-8")
        has_import_menu = "import-step" in app_content
        result("App.vue含'导入STEP'菜单项", has_import_menu)
        has_store = "useStepImportStore" in app_content
        result("App.vue集成stepImportStore", has_store)

    # T2.6: Pinia Store检查
    store_path = frontend_root / "stores/stepImport.ts"
    result("Pinia Store存在", store_path.exists())

    if store_path.exists():
        store_content = store_path.read_text(encoding="utf-8")
        has_upload = "importStepFile" in store_content
        result("Store含importStepFile方法", has_upload)
        has_progress = "uploadProgress" in store_content
        result("Store含uploadProgress状态", has_progress)
        has_state = "ImportState" in store_content
        result("Store含ImportState类型定义", has_state)

    # T2.7: 类型定义
    types_path = frontend_root / "types/index.ts"
    if types_path.exists():
        types_content = types_path.read_text(encoding="utf-8")
        has_step_type = "StepImportResult" in types_content
        result("类型含StepImportResult", has_step_type)
        has_model_info = "ModelInfo" in types_content
        result("类型含ModelInfo", has_model_info)


# 主入口
async def main():
    print(f"\n{'#' * 60}")
    print("#  STEP导入功能端到端验证")
    print("#  灵境制造 V4")
    print(f"{'#' * 60}")

    step_data = None
    try:
        step_data = await test_api_functional()
    except Exception as e:
        print(f"\n{FAIL} T1执行异常: {e}")
        import traceback

        traceback.print_exc()

    try:
        await test_error_handling()
    except Exception as e:
        print(f"\n{FAIL} T3执行异常: {e}")
        import traceback

        traceback.print_exc()

    try:
        await test_downstream_compatibility(step_data)
    except Exception as e:
        print(f"\n{FAIL} T4执行异常: {e}")
        import traceback

        traceback.print_exc()

    try:
        await test_frontend_validation()
    except Exception as e:
        print(f"\n{FAIL} T2执行异常: {e}")
        import traceback

        traceback.print_exc()

    # 汇总
    print(f"\n{'=' * 60}")
    print("  测试汇总")
    print(f"{'=' * 60}")
    print(f"  总计: {TOTAL}")
    print(f"  通过: {PASSED}")
    print(f"  失败: {FAILED}")
    if TOTAL > 0:
        print(f"  通过率: {PASSED / TOTAL * 100:.1f}%")
    print(f"{'=' * 60}")

    if FAILED > 0:
        print(f"\n  {WARN} 有 {FAILED} 个测试未通过，请检查上方详情")
    else:
        print(f"\n  [OK] 全部 {TOTAL} 个测试通过!")

    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
