"""Draft tool matches must retain source labels without inventing verification."""
from pathlib import Path
import json

import pytest

from app.data.process_data_manager import CuttingParameterEntry, ToolEntry
from app.process_planning._tool_models import MatchedTool
from app.process_planning.tool_param_matcher import ToolParamMatcher

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("source", [None, "repo_seed_v1", "handbook_common_range", "manufacturer_candidate"])
def test_source_survives_loading_and_draft_serialization(source):
    path = Path(__file__).resolve().parents[2] / "app/data/cutting_parameters.json"
    record = json.loads(path.read_text(encoding="utf-8"))[0]
    if source is None:
        record.pop("source", None)
    else:
        record["source"] = source
    params = CuttingParameterEntry.from_dict(record)
    assert params.source == (source or "unverified")
    tool = ToolEntry(id="fixture_tool", series="twist_drill", name="fixture drill", diameter_mm=8.0,
                     material="HSS", application="drill")
    serialized = MatchedTool(tool, params).to_dict()["cutting_parameters"]
    assert serialized["source"] == params.source
    assert serialized["source_verified"] is False
    warnings = ToolParamMatcher._generate_warnings(None, tool, 8.0, params)
    assert any(params.source in warning and "尚未核验" in warning for warning in warnings)
