"""不可信 XML 的安全解析入口。

标准库 ElementTree 对内部 DTD 实体不做限制：解析网络来源的 XML 时，
恶意构造的"实体炸弹"（billion laughs / CWE-776）可通过实体递归定义
把几 KB 报文膨胀到耗尽内存。MTConnect 机床报文永远不会合法携带
DOCTYPE——桌面端嵌入运行时又没有 defusedxml/lxml 可用——因此直接
拒绝含 DTD 声明的输入，是最贴合本仓依赖现状的防护方式。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element

# DTD 必须出现在根元素之前，但为防注释填充绕过，对全文做不区分大小写扫描
_DTD_DECL = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class UnsafeXmlError(ValueError):
    """输入含 DTD 声明，已拒绝解析。"""


def parse_untrusted_xml(xml_text: str) -> Element:
    """解析不可信来源（网络/机床代理/外部文件）的 XML。

    含 DOCTYPE 或 ENTITY 声明时 fail fast——实体膨胀与外部实体注入
    都必须依赖 DTD 才能构造，拒绝 DTD 即同时封死这两类攻击面。

    Raises:
        UnsafeXmlError: 输入含 DTD 声明。
        ET.ParseError: 输入不是合法 XML。
    """
    if xml_text is None:
        raise UnsafeXmlError("[输入校验] XML 内容为空。建议操作：检查数据源是否返回了有效报文。")
    if _DTD_DECL.search(xml_text):
        raise UnsafeXmlError(
            "[输入校验] XML 含 DOCTYPE/ENTITY 声明，疑似实体注入，已拒绝解析。"
            "建议操作：检查数据源；MTConnect 等设备报文不应携带 DTD。"
        )
    return ET.fromstring(xml_text)
