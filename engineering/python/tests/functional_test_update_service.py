"""Functional tests for Template Update Service."""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from app.templates.template_update_service import TemplateUpdateService  # noqa: E402


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "updates.db")
        svc = TemplateUpdateService(db_path=db_path)
        svc.initialize()
        yield svc
        svc.close()


def test_classify_priority_optional(service):
    assert service.classify_priority(0.01) == "optional"
    assert service.classify_priority(0.02) == "optional"


def test_classify_priority_recommended(service):
    assert service.classify_priority(0.03) == "recommended"
    assert service.classify_priority(0.05) == "recommended"
    assert service.classify_priority(0.09) == "recommended"


def test_classify_priority_critical(service):
    assert service.classify_priority(0.10) == "critical"
    assert service.classify_priority(0.25) == "critical"


def test_create_notification(service):
    notif = service.create_notification(
        project_id="proj_001",
        suggestion={
            "suggestion_id": "ev_001",
            "title": "Optimize scheduling",
            "description": "GPU utilization low",
            "change_preview": {"freq": "5m"},
            "expected_impact": {"improvement": 0.15},
        },
        priority="critical",
    )
    assert notif.project_id == "proj_001"
    assert notif.priority == "critical"
    assert notif.status == "pending"


def test_scan_for_updates(service):
    suggestions = [
        {
            "suggestion_id": "ev_001",
            "title": "Test",
            "description": "Test desc",
            "change_preview": {},
            "expected_impact": {"improvement": 0.05},
        },
    ]
    notifs = service.scan_for_updates("proj_001", suggestions)
    assert len(notifs) == 1
    assert notifs[0].priority == "recommended"


def test_scan_no_duplicates(service):
    suggestions = [
        {
            "suggestion_id": "ev_001",
            "title": "Test",
            "description": "Test desc",
            "change_preview": {},
            "expected_impact": {"improvement": 0.05},
        },
    ]
    notifs1 = service.scan_for_updates("proj_001", suggestions)
    assert len(notifs1) == 1
    notifs2 = service.scan_for_updates("proj_001", suggestions)
    assert len(notifs2) == 0


def test_apply_update(service):
    notif = service.create_notification(
        project_id="proj_001",
        suggestion={
            "suggestion_id": "ev_001",
            "title": "T",
            "description": "D",
            "change_preview": {},
            "expected_impact": {},
        },
        priority="optional",
    )
    result = service.apply_update(notif.notification_id)
    assert result is not None
    assert result.status == "applied"


def test_apply_nonexistent(service):
    result = service.apply_update("nonexistent")
    assert result is None


def test_dismiss_notification(service):
    notif = service.create_notification(
        project_id="proj_001",
        suggestion={
            "suggestion_id": "ev_001",
            "title": "T",
            "description": "D",
            "change_preview": {},
            "expected_impact": {},
        },
        priority="optional",
    )
    result = service.dismiss_notification(notif.notification_id)
    assert result is True


def test_dismiss_nonexistent(service):
    result = service.dismiss_notification("nonexistent")
    assert result is False


def test_preview_update(service):
    notif = service.create_notification(
        project_id="proj_001",
        suggestion={
            "suggestion_id": "ev_001",
            "title": "Preview Test",
            "description": "Description",
            "change_preview": {"key": "value"},
            "expected_impact": {"improvement": 0.1},
        },
        priority="critical",
    )
    preview = service.preview_update(notif.notification_id)
    assert preview is not None
    assert preview["title"] == "Preview Test"
    assert preview["change_preview"] == {"key": "value"}
    assert preview["priority"] == "critical"


def test_preview_nonexistent(service):
    result = service.preview_update("nonexistent")
    assert result is None


def test_get_notifications(service):
    service.create_notification(
        project_id="proj_001",
        suggestion={
            "suggestion_id": "ev_001",
            "title": "T",
            "description": "D",
            "change_preview": {},
            "expected_impact": {},
        },
        priority="optional",
    )
    service.create_notification(
        project_id="proj_002",
        suggestion={
            "suggestion_id": "ev_002",
            "title": "T",
            "description": "D",
            "change_preview": {},
            "expected_impact": {},
        },
        priority="recommended",
    )
    notifs = service.get_notifications("proj_001")
    assert len(notifs) == 1
    assert notifs[0].project_id == "proj_001"


def test_get_notifications_by_status(service):
    notif = service.create_notification(
        project_id="proj_001",
        suggestion={
            "suggestion_id": "ev_001",
            "title": "T",
            "description": "D",
            "change_preview": {},
            "expected_impact": {},
        },
        priority="optional",
    )
    service.apply_update(notif.notification_id)
    pending = service.get_notifications("proj_001", status_filter="pending")
    assert len(pending) == 0
    applied = service.get_notifications("proj_001", status_filter="applied")
    assert len(applied) == 1
