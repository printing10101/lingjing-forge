"""Functional tests for Template Branching System."""

import os
import shutil
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.templates.template_branching import TemplateBranchManager
from app.templates.template_branching import init_template_branching, get_branch_manager


@pytest.fixture
def branch_manager():
    """Create a temporary branch manager for testing."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "branches.db")
    json_dir = os.path.join(tmpdir, "branches")
    os.makedirs(json_dir, exist_ok=True)

    manager = TemplateBranchManager(db_path=db_path, json_dir=json_dir)
    manager.initialize()

    yield manager

    manager.close()
    shutil.rmtree(tmpdir)


@pytest.fixture
def api_client(branch_manager):
    """Create test client for branch API."""
    from app.api.v1.template_branching_routes import router

    app = FastAPI()
    app.include_router(router)

    # Override dependency to use test manager
    import app.api.v1.template_branching_routes as routes

    routes._test_manager = branch_manager

    return TestClient(app)


def test_create_branch(branch_manager):
    """Test creating a main branch."""
    template_data = {
        "name": "CNC Milling Template",
        "skills": ["vibration_analysis", "wear_prediction"],
        "model_config": {"lr": 0.001, "epochs": 100},
    }

    branch = branch_manager.create_branch(
        name="main",
        base_branch=None,
        data=template_data,
        metadata={"type": "main"},
    )

    assert branch.branch_id is not None
    assert branch.name == "main"
    assert branch.base_branch is None
    assert branch.metadata["type"] == "main"
    assert branch.template_data == template_data
    assert len(branch.commit_log) == 1
    assert branch.commit_log[0].to_dict()["action"] == "create"


def test_get_branch(branch_manager):
    """Test retrieving a branch by ID."""
    template_data = {"name": "Test Template"}
    branch = branch_manager.create_branch(
        name="main", base_branch=None, data=template_data, metadata={"type": "main"}
    )

    retrieved = branch_manager.get_branch(branch.branch_id)

    assert retrieved is not None
    assert retrieved.branch_id == branch.branch_id
    assert retrieved.name == "main"


def test_list_branches(branch_manager):
    """Test listing branches with optional type filter."""
    # Create main branch
    branch_manager.create_branch(
        name="main", base_branch=None, data={"name": "Main"}, metadata={"type": "main"}
    )
    # Create industry branch
    branch_manager.create_branch(
        name="car-industry",
        base_branch="main",
        data={"name": "Car"},
        metadata={"type": "industry"},
    )
    # Create material branch
    branch_manager.create_branch(
        name="aluminum",
        base_branch="main",
        data={"name": "Aluminum"},
        metadata={"type": "material"},
    )

    all_branches = branch_manager.list_branches()
    assert len(all_branches) == 3

    industry_branches = branch_manager.list_branches(type_filter="industry")
    assert len(industry_branches) == 1
    assert industry_branches[0].name == "car-industry"

    material_branches = branch_manager.list_branches(type_filter="material")
    assert len(material_branches) == 1
    assert material_branches[0].name == "aluminum"


def test_commit_log(branch_manager):
    """Test that create and merge operations are logged."""
    branch = branch_manager.create_branch(
        name="main", base_branch=None, data={"name": "Main"}, metadata={"type": "main"}
    )

    log = branch_manager.get_commit_log(branch.branch_id)

    assert len(log) == 1
    assert log[0]["action"] == "create"
    assert log[0]["branch_name"] == "main"
    assert "timestamp" in log[0]


def test_api_create_branch(api_client):
    """Test POST /api/v1/templates/branches/"""
    response = api_client.post(
        "/api/v1/templates/branches/",
        json={
            "name": "main",
            "base_branch": None,
            "data": {"name": "Main Template"},
            "metadata": {"type": "main"},
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["branch"]["name"] == "main"
    assert data["branch"]["metadata"]["type"] == "main"


def test_api_list_branches(api_client):
    """Test GET /api/v1/templates/branches/"""
    api_client.post(
        "/api/v1/templates/branches/",
        json={
            "name": "test-branch",
            "base_branch": None,
            "data": {"name": "Test"},
            "metadata": {"type": "main"},
        },
    )

    response = api_client.get("/api/v1/templates/branches/")
    assert response.status_code == 200
    data = response.json()
    assert "branches" in data
    assert len(data["branches"]) >= 1


def test_api_get_branch(api_client):
    """Test GET /api/v1/templates/branches/{branch_id}"""
    create_resp = api_client.post(
        "/api/v1/templates/branches/",
        json={
            "name": "single",
            "base_branch": None,
            "data": {"name": "Single"},
            "metadata": {"type": "main"},
        },
    )
    branch_id = create_resp.json()["branch"]["branch_id"]

    response = api_client.get(f"/api/v1/templates/branches/{branch_id}")
    assert response.status_code == 200
    assert response.json()["branch"]["name"] == "single"


def test_api_merge_branch(api_client):
    """Test POST /api/v1/templates/branches/merge"""
    src = api_client.post(
        "/api/v1/templates/branches/",
        json={
            "name": "source",
            "base_branch": None,
            "data": {"skill": "v1"},
            "metadata": {"type": "experiment"},
        },
    )
    tgt = api_client.post(
        "/api/v1/templates/branches/",
        json={
            "name": "target",
            "base_branch": None,
            "data": {"skill": "v0"},
            "metadata": {"type": "main"},
        },
    )

    response = api_client.post(
        "/api/v1/templates/branches/merge",
        json={
            "source_id": src.json()["branch"]["branch_id"],
            "target_id": tgt.json()["branch"]["branch_id"],
            "strategy": "overwrite",
        },
    )
    assert response.status_code == 200
    assert response.json()["merged_branch"]["template_data"]["skill"] == "v1"


def test_startup_initialization():
    """Test that init_template_branching creates a manager with working methods."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    json_dir = os.path.join(tmpdir, "branches")

    # P2-1 修复：用 try/finally 保护资源，防止 assert 失败导致
    # sqlite 连接、manager、tmpdir 泄漏。
    manager = init_template_branching(db_path=db_path, json_dir=json_dir)
    conn = None
    try:
        same = get_branch_manager()
        assert same is manager

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='template_branches'"
        )
        assert cursor.fetchone() is not None
    finally:
        if conn is not None:
            conn.close()
        manager.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
