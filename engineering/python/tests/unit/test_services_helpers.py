"""services 纯辅助函数单元测试（JSON/日期/numpy 转换/可靠性评分）。"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from app.services._agent_helpers import (
    _ACTION_FIELD_ORDER,
    _STATE_FIELD_INDEX,
_action_array_to_dict,
    _action_dict_to_array,
    _extract_state_field,
)
from app.services._card_helpers import _json_dumps, _json_loads, _parse_iso_datetime
from app.services.experience_store import ExperienceStore

pytestmark = pytest.mark.unit


class TestJsonHelpers:
    def test_json_dumps_none(self):
        assert _json_dumps(None) == '[]'

    def test_json_dumps_normal(self):
        assert _json_dumps({'a': 1}) == '{"a": 1}'

    def test_json_loads_empty(self):
        assert _json_loads('', 'fallback') == 'fallback'
        assert _json_loads(None, []) == []

    def test_json_loads_valid(self):
        assert _json_loads('{"a": 1}', {}) == {'a': 1}

    def test_json_loads_invalid(self):
        assert _json_loads('not-json', {}) == {}


class TestParseIsoDatetime:
    def test_empty(self):
        assert _parse_iso_datetime('') is None
        assert _parse_iso_datetime(None) is None

    def test_valid(self):
        d = _parse_iso_datetime('2026-01-15T10:30:00')
        assert d == datetime(2026, 1, 15, 10, 30, 0)

    def test_invalid(self):
        assert _parse_iso_datetime('not-a-date') is None


class TestActionHelpers:
    def test_action_dict_to_array(self):
        d = {field: 1.0 for field in _ACTION_FIELD_ORDER}
        arr = _action_dict_to_array(d, field_name='action')
        assert arr.shape == (len(_ACTION_FIELD_ORDER),)
        assert arr.dtype == np.float32

    def test_action_dict_to_array_empty_raises(self):
        with pytest.raises(ValueError, match='不能为空'):
            _action_dict_to_array({}, field_name='action')

    def test_action_dict_to_array_missing_field(self):
        d = {field: 1.0 for field in _ACTION_FIELD_ORDER[1:]}
        with pytest.raises(ValueError, match='缺少字段'):
            _action_dict_to_array(d, field_name='action')

    def test_action_array_to_dict(self):
        arr = np.arange(len(_ACTION_FIELD_ORDER), dtype=np.float32)
        d = _action_array_to_dict(arr)
        assert len(d) == len(_ACTION_FIELD_ORDER)
        assert d[_ACTION_FIELD_ORDER[0]] == 0.0


class TestExtractStateField:
    def test_valid_field(self):
        first = list(_STATE_FIELD_INDEX.keys())[0]
        arr = np.zeros(10, dtype=np.float32)
        arr[0] = 5.0
        assert _extract_state_field(arr, first) == 5.0

    def test_unknown_field_returns_default(self):
        arr = np.zeros(10, dtype=np.float32)
        assert _extract_state_field(arr, 'unknown_field') == 0.0
        assert _extract_state_field(arr, 'unknown_field', default=-1.0) == -1.0


class TestReliabilityScore:
    def test_zero_validation_returns_half(self):
        assert ExperienceStore._calculate_reliability_score(0, 1.0) == 0.5

    def test_full_confidence(self):
        # consistency 1.0 * 0.7 + confidence_weight 1.0 * 0.3 = 1.0
        assert ExperienceStore._calculate_reliability_score(10, 1.0) == 1.0

    def test_partial(self):
        # 0.5 * 0.7 + 0.5 * 0.3 = 0.5
        assert ExperienceStore._calculate_reliability_score(5, 0.5) == pytest.approx(0.5)

    def test_clamped(self):
        assert ExperienceStore._calculate_reliability_score(100, 1.0) == 1.0
        assert ExperienceStore._calculate_reliability_score(100, 0.0) == pytest.approx(0.3)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
