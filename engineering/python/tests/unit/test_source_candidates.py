"""Manufacturer extraction must preserve conditions and fail on ambiguous units."""
import copy
import hashlib

import pytest

from app.rag.source_candidates import build_vendor_candidates

pytestmark = pytest.mark.unit
PDF = b"%PDF-1.7\nsynthetic test document"


def manifest():
    return {"sha256": hashlib.sha256(PDF).hexdigest(), "url": "https://vendor.example/catalog.pdf", "records": [{
        "material_group": "amg_1_3", "tool": "R10A", "locator": "row 1.3 / R10A; feed G / 5/16",
        "hardness_condition": "<24 HRC", "speed_code": "89G", "pdf_pages": [4, 5],
        "feed": 0.0063, "feed_unit": "inch/rev", "diameter_inch": 0.3125, "feed_tolerance_percent": 25,
    }]}


def test_confirmed_feed_conversion_preserves_unresolved_speed_and_scope():
    result = build_vendor_candidates(PDF, manifest())[0]
    assert result["parameters"] == {"feed_per_revolution_mm": 0.16002, "feed_min_mm_per_rev": 0.120015,
                                     "feed_max_mm_per_rev": 0.200025}
    assert result["conditions"] == {"hardness": "<24 HRC", "diameter_mm": 7.9375}
    assert result["original_record"]["speed_code"] == "89G"
    assert "spindle_rpm" not in result["parameters"]
    assert result["review_status"] == "unreviewed" and not result["eligible_for_recommendation"]
    assert result["material"] != "steel_45"
    assert result["provenance"]["pdf_pages"] == [4, 5]


def test_metric_feed_and_content_identity():
    data = manifest()
    first = build_vendor_candidates(PDF, data)[0]
    assert build_vendor_candidates(PDF, data)[0]["candidate_id"] == first["candidate_id"]
    data["records"][0].update(feed=0.16, feed_unit="mm/rev")
    revised = build_vendor_candidates(PDF, data)[0]
    assert revised["parameters"]["feed_per_revolution_mm"] == 0.16
    assert revised["candidate_id"] != first["candidate_id"]


@pytest.mark.parametrize("pdf,key,value", [
    (b"HTML", None, None), (PDF, "sha256", "0" * 64),
    (PDF, "url", "http://vendor.example/a"), (PDF, "url", "https://user:secret@vendor.example/a"),
    (PDF, "records", []), (PDF, "records", "wrong"),
    (PDF, "url", None), (PDF, "records", [None]),
])
def test_invalid_source_rejected(pdf, key, value):
    data = manifest()
    if key:
        data[key] = value
    with pytest.raises(ValueError):
        build_vendor_candidates(pdf, data)


@pytest.mark.parametrize("key,value", [
    ("feed", True), ("feed", float("nan")), ("feed", float("inf")), ("feed", 0),
    ("feed", "0.0063"), ("diameter_inch", -1), ("feed_unit", "mm/tooth"),
    ("feed_unit", "unknown"), ("feed_tolerance_percent", 100), ("tool", ""),
    ("hardness_condition", None), ("pdf_pages", [0]), ("pdf_pages", [True]), ("pdf_pages", []),
    ("feed", 1e308), ("diameter_inch", 1e308),
])
def test_incomplete_or_ambiguous_records_rejected(key, value):
    data = manifest()
    data["records"][0][key] = value
    with pytest.raises(ValueError):
        build_vendor_candidates(PDF, data)


def test_duplicate_record_rejected():
    data = manifest()
    data["records"].append(copy.deepcopy(data["records"][0]))
    with pytest.raises(ValueError, match="重复"):
        build_vendor_candidates(PDF, data)


def test_manifest_type_and_metric_overflow_rejected():
    with pytest.raises(ValueError, match="清单"):
        build_vendor_candidates(PDF, [])
    data = manifest()
    data["records"][0].update(feed=1.7e308, feed_unit="mm/rev")
    with pytest.raises(ValueError, match="maximum_feed"):
        build_vendor_candidates(PDF, data)
