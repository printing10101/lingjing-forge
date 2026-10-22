"""Both inference plugins must load the same artifact formats consistently."""

import numpy as np
import pytest

from app.contracts.task import Artifact
from app.plugins.artifact_data import load_artifact_data

pytestmark = pytest.mark.unit


def artifact(uri="metrics://input", **metadata):
    return Artifact(name="input", type="metrics", uri=uri, metadata=metadata)


def test_missing_or_unsupported_artifact():
    assert load_artifact_data(None) is None
    assert load_artifact_data(artifact()) is None


def test_metadata_takes_precedence():
    loaded = load_artifact_data(artifact("file:///missing.npy", data=[1, 2]))
    np.testing.assert_array_equal(loaded, [1, 2])
    assert loaded.dtype == np.float32


def test_invalid_metadata_returns_none():
    assert load_artifact_data(artifact(data=["invalid"])) is None


@pytest.mark.parametrize("legacy", [False, True])
def test_local_file_with_spaces_and_unicode(tmp_path, legacy):
    path = tmp_path / "输入 数据.npy"
    expected = np.array([[1.0, 2.0]])
    np.save(path, expected)
    uri = f"file://{path}" if legacy else path.as_uri()
    np.testing.assert_array_equal(load_artifact_data(artifact(uri)), expected)


def test_localhost_uri(tmp_path):
    path = tmp_path / "data.npy"
    np.save(path, [2, 3])
    uri = path.as_uri().replace("file:///", "file://localhost/", 1)
    np.testing.assert_array_equal(load_artifact_data(artifact(uri)), [2, 3])


def test_legacy_relative_uri(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    np.save(tmp_path / "data" / "input.npy", [4, 5])
    np.testing.assert_array_equal(load_artifact_data(artifact("file://data/input.npy")), [4, 5])


@pytest.mark.parametrize("kind", ["missing", "corrupt", "object", "archive"])
def test_unusable_file_returns_none(tmp_path, kind):
    path = tmp_path / "data.npy"
    if kind == "corrupt":
        path.write_bytes(b"not a numpy array")
    elif kind == "object":
        np.save(path, np.array([{}], dtype=object))
    elif kind == "archive":
        path = tmp_path / "data.npz"
        np.savez(path, values=[1])
    assert load_artifact_data(artifact(path.as_uri())) is None


def test_both_plugins_use_shared_loader(monkeypatch):
    from app.plugins.rl_agent import plugin as rl
    from app.plugins.world_model import plugin as wm

    expected = np.array([3.0])
    for module, cls in [(rl, rl.RLAgentPlugin), (wm, wm.WorldModelPlugin)]:
        monkeypatch.setattr(module, "load_artifact_data", lambda value: expected)
        assert cls._load_artifact_data(None, artifact()) is expected
