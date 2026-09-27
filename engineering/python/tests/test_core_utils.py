"""
Test Core Utilities

Tests for:
- extract_json_from_markdown: Parse JSON from LLM responses
- flatten_documents: Flatten nested document structures
- format_bytes: Human-readable byte formatting
- MetricsCollector: Thread-safe metrics collection
"""

import pytest
import threading
from app.utils.utils import (
    extract_json_from_markdown,
    flatten_documents,
    format_bytes,
    MetricsCollector,
    get_metrics_collector,
)


class TestExtractJsonFromMarkdown:
    """Test JSON extraction from markdown"""

    def test_extract_json_code_fence(self):
        content = """
        Here is the result:
        ```json
        {"status": "success", "data": [1, 2, 3]}
        ```
        End of response.
        """
        result = extract_json_from_markdown(content)
        assert result["status"] == "success"
        assert result["data"] == [1, 2, 3]

    def test_extract_gcode_fence(self):
        content = """
        Generated code:
        ```gcode
        {"tool_path": "M3 S1000", "feed_rate": 150}
        ```
        """
        result = extract_json_from_markdown(content)
        assert result["tool_path"] == "M3 S1000"

    def test_extract_plain_code_fence(self):
        content = """
        ```
        {"key": "value", "count": 42}
        ```
        """
        result = extract_json_from_markdown(content)
        assert result["key"] == "value"

    def test_extract_without_fence(self):
        content = '{"direct": "json", "value": 100}'
        result = extract_json_from_markdown(content)
        assert result["direct"] == "json"

    def test_extract_with_whitespace(self):
        content = '   ```json\n{"trimmed": true}\n   ```   '
        result = extract_json_from_markdown(content)
        assert result["trimmed"] is True

    def test_extract_invalid_json_returns_empty(self):
        content = "```json\n{invalid json}\n```"
        result = extract_json_from_markdown(content)
        assert result == {}

    def test_extract_empty_string(self):
        result = extract_json_from_markdown("")
        assert result == {}

    def test_extract_whitespace_only(self):
        result = extract_json_from_markdown("   \n\t  ")
        assert result == {}

    def test_extract_complex_nested_json(self):
        content = """
        ```json
        {
            "model": "LNN",
            "config": {
                "layers": 4,
                "hidden_size": 256
            },
            "metrics": [0.95, 0.92, 0.98]
        }
        ```
        """
        result = extract_json_from_markdown(content)
        assert result["model"] == "LNN"
        assert result["config"]["layers"] == 4
        assert len(result["metrics"]) == 3

    def test_extract_json_with_special_chars(self):
        content = '{"chinese": "中文", "special": "value\\nwith\\nnewlines"}'
        result = extract_json_from_markdown(content)
        assert result["chinese"] == "中文"


class TestFlattenDocuments:
    """Test document flattening"""

    def test_flatten_nested_list(self):
        docs = [["doc1", "doc2", "doc3"]]
        result = flatten_documents(docs)
        assert result == ["doc1", "doc2", "doc3"]

    def test_flatten_flat_list(self):
        docs = ["doc1", "doc2", "doc3"]
        result = flatten_documents(docs)
        assert result == ["doc1", "doc2", "doc3"]

    def test_flatten_empty_list(self):
        result = flatten_documents([])
        assert result == []

    def test_flatten_none(self):
        result = flatten_documents(None)
        assert result == []

    def test_flatten_non_list(self):
        result = flatten_documents("not a list")
        assert result == []

    def test_flatten_converts_to_strings(self):
        docs = [[1, 2, 3]]
        result = flatten_documents(docs)
        assert result == ["1", "2", "3"]

    def test_flatten_mixed_content(self):
        docs = [["item1", "item2"]]
        result = flatten_documents(docs)
        assert len(result) == 2
        assert all(isinstance(item, str) for item in result)


class TestFormatBytes:
    """Test byte formatting"""

    def test_format_bytes_small(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(512) == "512 B"
        assert format_bytes(1023) == "1023 B"

    def test_format_kilobytes(self):
        assert "KB" in format_bytes(1024)
        assert "KB" in format_bytes(1024 * 500)

    def test_format_megabytes(self):
        result = format_bytes(1024 * 1024)
        assert "MB" in result
        assert "1.00" in result

    def test_format_gigabytes(self):
        result = format_bytes(1024 * 1024 * 1024)
        assert "GB" in result
        assert "1.00" in result

    def test_format_decimal_precision(self):
        result = format_bytes(1024 * 512 + 512)
        assert "KB" in result

    def test_format_large_value(self):
        result = format_bytes(1024 * 1024 * 1024 * 2)
        assert "GB" in result


class TestMetricsCollector:
    """Test metrics collector"""

    def test_initialization(self):
        collector = MetricsCollector()
        assert collector._request_count == 0
        assert collector._start_time > 0

    def test_record_single_request(self):
        collector = MetricsCollector()
        collector.record("/api/test", 0.5)
        assert collector._request_count == 1
        assert "/api/test" in collector._request_latency

    def test_record_multiple_requests(self):
        collector = MetricsCollector()
        collector.record("/api/test", 0.1)
        collector.record("/api/test", 0.2)
        collector.record("/api/test", 0.3)
        assert collector._request_count == 3
        assert len(collector._request_latency["/api/test"]) == 3

    def test_record_different_paths(self):
        collector = MetricsCollector()
        collector.record("/api/users", 0.5)
        collector.record("/api/orders", 0.3)
        assert len(collector._request_latency) == 2

    def test_latency_limit_enforced(self):
        collector = MetricsCollector()
        for i in range(1500):
            collector.record("/api/test", 0.1)
        assert len(collector._request_latency["/api/test"]) <= 1000

    def test_export_format(self):
        collector = MetricsCollector()
        collector.record("/api/test", 0.5)
        result = collector.export()
        assert "HELP" in result
        assert "TYPE" in result
        assert "uptime" in result
        assert "http_requests_total" in result

    def test_export_contains_metrics(self):
        collector = MetricsCollector()
        collector.record("/api/users", 0.1)
        result = collector.export()
        assert "/api/users" in result
        assert "http_request_duration" in result

    def test_concurrent_record(self):
        collector = MetricsCollector()
        errors = []

        def record_requests(path_prefix):
            try:
                for i in range(100):
                    collector.record(f"/{path_prefix}/{i}", 0.1)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=record_requests, args=(f"api{i}",))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert collector._request_count == 500

    def test_export_after_concurrent_writes(self):
        collector = MetricsCollector()

        def record_batch():
            for i in range(50):
                collector.record("/api/test", 0.1)

        threads = [threading.Thread(target=record_batch) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        result = collector.export()
        assert "http_requests_total" in result
        assert collector._request_count == 200


class TestGetMetricsCollector:
    """Test global metrics collector"""

    def test_returns_metrics_collector(self):
        collector = get_metrics_collector()
        assert isinstance(collector, MetricsCollector)

    def test_returns_singleton(self):
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        assert collector1 is collector2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
