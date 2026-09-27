"""MTConnect parser 单元测试（纯 XML 解析 + Sample 数据模型）。"""

from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest

from app.integrations.mtconnect.parser import (
    Sample,
    _coerce_float,
    _local_tag,
    parse_sample_response,
)

pytestmark = pytest.mark.unit


def _xml() -> str:
    return '''<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5">
      <Streams>
        <DeviceStream name="M12345">
          <ComponentStream component="Spindle" name="spindle">
            <Samples>
              <SpindleSpeed>12000</SpindleSpeed>
              <SpindleLoad>42.5</SpindleLoad>
            </Samples>
            <Events>
              <Execution>ACTIVE</Execution>
            </Events>
          </ComponentStream>
          <ComponentStream component="Axes" name="axes">
            <Samples>
              <Feedrate>1500.0</Feedrate>
            </Samples>
          </ComponentStream>
        </DeviceStream>
      </Streams>
    </MTConnectStreams>'''


class TestLocalTag:
    def test_strips_namespace(self):
        assert _local_tag('{urn:mtconnect.org:MTConnectStreams:1.5}SpindleSpeed') == 'SpindleSpeed'

    def test_no_namespace(self):
        assert _local_tag('SpindleSpeed') == 'SpindleSpeed'


class TestCoerceFloat:
    def test_none(self):
        assert _coerce_float(None) is None

    def test_empty(self):
        assert _coerce_float('') is None
        assert _coerce_float('   ') is None

    def test_unavailable(self):
        assert _coerce_float('UNAVAILABLE') is None
        assert _coerce_float('NA') is None
        assert _coerce_float('N/A') is None

    def test_valid(self):
        assert _coerce_float('12000') == 12000.0
        assert _coerce_float('42.5') == 42.5

    def test_invalid(self):
        assert _coerce_float('abc') is None


class TestParseSampleResponse:
    def test_empty_body(self):
        s = parse_sample_response('')
        assert s.is_empty() is True

    def test_parse_full_sample(self):
        s = parse_sample_response(_xml())
        assert s.spindle_speed == 12000.0
        assert s.spindle_load == 42.5
        assert s.feedrate == 1500.0
        assert s.execution == 'ACTIVE'
        assert s.is_empty() is False

    def test_malformed_xml_raises(self):
        with pytest.raises(ET.ParseError):
            parse_sample_response('<not-closed')

    def test_missing_fields_are_none(self):
        xml = '<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5"><Streams/></MTConnectStreams>'
        s = parse_sample_response(xml)
        assert s.spindle_speed is None
        assert s.execution is None
        assert s.is_empty() is True


class TestSampleModel:
    def test_is_empty_all_none(self):
        assert Sample().is_empty() is True

    def test_is_empty_with_value(self):
        assert Sample(spindle_speed=1000.0).is_empty() is False

    def test_to_storage_row(self):
        ts = datetime(2026, 1, 15, 10, 30, 0, 123456, tzinfo=timezone.utc)
        s = Sample(spindle_speed=1000.0, spindle_load=50.0, feedrate=200.0, execution='ACTIVE', observed_at=ts)
        row = s.to_storage_row()
        assert row['spindle_speed'] == 1000.0
        assert row['spindle_load'] == 50.0
        assert row['feedrate'] == 200.0
        assert row['execution'] == 'ACTIVE'
        assert row['ts'].startswith('2026-01-15')

    def test_to_storage_row_none_values(self):
        s = Sample(observed_at=datetime.now(timezone.utc))
        row = s.to_storage_row()
        assert row['spindle_speed'] is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
