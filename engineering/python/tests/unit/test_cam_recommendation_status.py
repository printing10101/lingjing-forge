"""An empty recommendation must explain whether knowledge is absent or unmatched."""
from unittest.mock import Mock

import pytest

from app.api.v1.agent_gateway.cam import cam_process_recommend


@pytest.mark.unit
@pytest.mark.parametrize(
    "recommendations,total,expected_status,hint_fragment",
    [([], 0, "knowledge_empty", "知识库为空"),
     ([], 4, "no_match", "未找到匹配"),
     ([{"process": "drilling", "source": "reviewed-document"}], 4, "matched", None)],
)
def test_recommendation_business_status(monkeypatch, recommendations, total, expected_status, hint_fragment):
    index = Mock()
    index.assess_recommendations.return_value = {"recommendations": recommendations, "applicability_checks": []}
    index.get_stats.return_value = {"total_quadruples": total}
    monkeypatch.setattr("app.rag.process_quadruple.get_process_quadruple_index", lambda: index)
    payload = cam_process_recommend(feature=" Hole ", material=" Steel_45 ", top_k=3)["data"]
    assert payload["recommendation_status"] == expected_status
    assert payload["recommendations"] == recommendations
    if hint_fragment:
        assert hint_fragment in payload["hint"]
    else:
        assert payload["hint"] is None
    index.assess_recommendations.assert_called_once_with("hole", "steel_45", top_k=3, conditions=None)
