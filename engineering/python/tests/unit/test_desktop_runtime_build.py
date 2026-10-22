"""A desktop build must never publish readiness after a failed native probe."""
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/build_desktop_runtime.py"
spec = importlib.util.spec_from_file_location("desktop_runtime_build", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
pytestmark = pytest.mark.unit


def complete_report():
    return {"python": "3.12.14", "cad_volume": 6.0, "imports": {
        name: "tested" for name in ("fastapi", "uvicorn", "pydantic", "ezdxf",
                                   "cadquery", "onnxruntime", "torch",
                                   "sentence_transformers", "chromadb")}}


def test_copy_tree_excludes_directories_and_bytecode(tmp_path):
    source = tmp_path / "source"
    for name in ("app.py", "sub/keep.py", "sub/stale.pyc", "tests/test_x.py", "__pycache__/cached.pyc"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")
    destination = tmp_path / "output"
    assert build.copy_tree(source, destination, ("tests", "__pycache__", ".pyc")) == 2
    assert {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()} == {
        "app.py", "sub/keep.py"}


def test_clear_preserves_root_and_external_files(tmp_path):
    root = tmp_path / "runtime"
    target = root / "backend"
    (target / "nested").mkdir(parents=True)
    (target / "nested/file").write_text("old")
    sentinel = tmp_path / "source.txt"
    sentinel.write_text("keep")
    build.clear_dir(target, allowed_root=root)
    assert target.is_dir() and list(target.iterdir()) == []
    assert sentinel.read_text() == "keep"


@pytest.mark.parametrize("kind", ["root", "outside", "traversal"])
def test_clear_rejects_wrong_target(tmp_path, kind):
    root = tmp_path / "runtime"
    root.mkdir()
    target = {"root": root, "outside": tmp_path, "traversal": root / ".." / "source"}[kind]
    with pytest.raises(ValueError, match="Refusing"):
        build.clear_dir(target, allowed_root=root)
    assert root.exists()


def test_build_rejects_dependency_source_inside_output(tmp_path):
    root = tmp_path / "desktop"
    packages = root / "runtime/Lib/site-packages"
    packages.mkdir(parents=True)
    with pytest.raises(ValueError, match="overlap"):
        build.validate_build_paths(tmp_path / "python", root, tmp_path / "backend", packages)


def test_build_rejects_missing_explicit_dependency_source(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        build.validate_build_paths(tmp_path / "python", tmp_path / "desktop", tmp_path / "backend", tmp_path / "absent")


def test_build_accepts_separate_sources(tmp_path):
    build.validate_build_paths(tmp_path / "python", tmp_path / "desktop", tmp_path / "backend")


def test_native_probe_requires_clean_exit(monkeypatch):
    process = Mock(returncode=3221225477, stdout=json.dumps(complete_report()), stderr="native crash")
    monkeypatch.setattr(build.subprocess, "run", Mock(return_value=process))
    with pytest.raises(RuntimeError, match="3221225477"):
        build.validate_runtime(Path("python.exe"))


def test_native_probe_success_strips_injected_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "untrusted-path")
    run = Mock(return_value=Mock(returncode=0, stdout="log\n" + json.dumps(complete_report()), stderr=""))
    monkeypatch.setattr(build.subprocess, "run", run)
    assert build.validate_runtime(Path("python.exe")) == complete_report()
    assert "PYTHONPATH" not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["timeout"] == 180
    assert run.call_args.kwargs["encoding"] == "utf-8"


@pytest.mark.parametrize("report", ["", "not-json", "[]", "{}"])
def test_invalid_native_report_is_rejected(monkeypatch, report):
    monkeypatch.setattr(build.subprocess, "run", Mock(return_value=Mock(returncode=0, stdout=report, stderr="")))
    with pytest.raises(RuntimeError, match="report"):
        build.validate_runtime(Path("python.exe"))


@pytest.mark.parametrize("volume", [float("nan"), float("inf"), "6", None, True, 5.9])
def test_invalid_geometry_is_rejected(monkeypatch, volume):
    report = complete_report()
    report["cad_volume"] = volume
    monkeypatch.setattr(build.subprocess, "run", Mock(return_value=Mock(returncode=0, stdout=json.dumps(report), stderr="")))
    with pytest.raises(RuntimeError, match="incomplete"):
        build.validate_runtime(Path("python.exe"))


def test_probe_timeout_is_not_readiness(monkeypatch):
    monkeypatch.setattr(build.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("python", 180)))
    with pytest.raises(subprocess.TimeoutExpired):
        build.validate_runtime(Path("python.exe"))


def test_incompatible_python_is_rejected(monkeypatch):
    report = complete_report()
    report["python"] = "3.13.12"
    monkeypatch.setattr(build.subprocess, "run", Mock(return_value=Mock(returncode=0, stdout=json.dumps(report), stderr="")))
    with pytest.raises(RuntimeError, match="incomplete"):
        build.validate_runtime(Path("python.exe"))


@pytest.mark.parametrize("native_ready", [False, True])
def test_build_publishes_manifest_only_after_native_success(monkeypatch, tmp_path, native_ready):
    source = tmp_path / "python"
    source.mkdir()
    entry = "python.exe" if build.sys.platform == "win32" else "bin/python3"
    (source / entry).parent.mkdir(parents=True, exist_ok=True)
    (source / entry).write_text("placeholder")
    packages = tmp_path / "packages"
    packages.mkdir()
    backend = tmp_path / "backend"
    backend.mkdir()
    root = tmp_path / "desktop"
    root.mkdir()
    (root / "runtime.json").write_text('{"ready": true}')
    monkeypatch.setattr(build.sys, "argv", [str(SCRIPT), "--python-src", str(source),
                                            "--runtime-dir", str(root), "--site-packages", str(packages),
                                            "--backend-src", str(backend)])
    monkeypatch.setattr(build.subprocess, "run", Mock(return_value=Mock(returncode=0, stdout="3.12.14")))
    probe = Mock(return_value=complete_report()) if native_ready else Mock(side_effect=RuntimeError("native crash"))
    monkeypatch.setattr(build, "validate_runtime", probe)
    assert build.main() == (0 if native_ready else 1)
    assert (root / "runtime.json").exists() == native_ready
    if native_ready:
        manifest = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        assert manifest["python"] == "3.12.14"
        assert manifest["validation"] == complete_report()
        assert len(manifest["constraints_sha256"]) == 64


def test_copy_tree_keeps_model_weights(tmp_path):
    """安装包必须带上工艺参数的模型权重。

    回归背景（2026-10-22）：build_desktop_runtime 以
    ``ignore=("__pycache__", "tests", "data", ".pyc")`` 复制 app/，
    checkpoints 下的 .pt 必须被保留——否则装完仍是解析回退路径，
    "双路径颤振预测"在交付物里等于不存在。
    """
    source = tmp_path / "app"
    for name in (
        "simulation/chatter/checkpoints/chatter_model.pt",
        "simulation/cutting_force/checkpoints/best_model.pt",
        "simulation/chatter/tests/test_predictor.py",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x01")

    destination = tmp_path / "out"
    copied = build.copy_tree(source, destination, ("__pycache__", "tests", "data", ".pyc"))
    assert copied == 2
    assert (destination / "simulation/chatter/checkpoints/chatter_model.pt").is_file()
    assert (destination / "simulation/cutting_force/checkpoints/best_model.pt").is_file()
    assert not (destination / "simulation/chatter/tests").exists()


def test_repository_ships_parameter_weights():
    """两处工艺参数权重必须随仓库存在（桌面安装包的唯一来源）。"""
    backend = Path(__file__).resolve().parents[2]
    for rel in (
        "app/simulation/chatter/checkpoints/chatter_model.pt",
        "app/simulation/cutting_force/checkpoints/best_model.pt",
    ):
        weight = backend / rel
        assert weight.is_file(), f"缺少随安装包分发的权重: {rel}"
        assert weight.stat().st_size > 10_000, f"权重文件可疑（过小）: {rel}"
