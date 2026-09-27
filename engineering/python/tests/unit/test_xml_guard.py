"""app.utils.xml_guard 单元测试：不可信 XML 的 DTD 拒绝防护。"""

from __future__ import annotations

import pytest

from app.utils.xml_guard import UnsafeXmlError, parse_untrusted_xml

pytestmark = [pytest.mark.unit]


class TestParseUntrustedXml:
    def test_normal_mtconnect_payload_parses(self):
        root = parse_untrusted_xml(
            '<MTConnectStreams xmlns="urn:mtconnect.org:MTConnectStreams:1.5">'
            "<Streams><DeviceStream name='M1'/></Streams></MTConnectStreams>"
        )
        assert root.tag.endswith("MTConnectStreams")

    def test_billion_laughs_dtd_rejected(self):
        # 经典实体炸弹：几 KB 报文可指数膨胀到内存耗尽，必须在解析前拒绝
        bomb = (
            "<?xml version='1.0'?><!DOCTYPE lolz [<!ENTITY lol 'lol'>"
            "<!ENTITY lol2 '&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;'>"
            "<!ENTITY lol3 '&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;'>"
            "]><lolz>&lol3;</lolz>"
        )
        with pytest.raises(UnsafeXmlError):
            parse_untrusted_xml(bomb)

    def test_external_entity_doctype_rejected(self):
        smuggle = (
            "<?xml version='1.0'?><!DOCTYPE foo ["
            "<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><foo>&xxe;</foo>"
        )
        with pytest.raises(UnsafeXmlError):
            parse_untrusted_xml(smuggle)

    def test_doctype_detection_case_and_whitespace_insensitive(self):
        sneaky = "<! dOcTyPe foo [<!eNtItY bar 'baz'>]><foo/>"
        with pytest.raises(UnsafeXmlError):
            parse_untrusted_xml(sneaky)

    def test_none_input_raises_unsafe_xml(self):
        with pytest.raises(UnsafeXmlError):
            parse_untrusted_xml(None)

    def test_malformed_xml_still_raises_parse_error(self):
        with pytest.raises(Exception):  # noqa: B017 - ET.ParseError 非 ValueError 子类
            parse_untrusted_xml("<not-closed>")

    def test_unsafe_xml_error_is_value_error(self):
        # 调用方（如 adapter 的重试逻辑）按 ValueError 家族兜底即可感知
        assert issubclass(UnsafeXmlError, ValueError)
