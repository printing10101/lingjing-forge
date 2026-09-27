"""验证周期性链状态持久化下的崩溃恢复一致性。

场景：
1. 写入 50 条日志（_CHAIN_STATE_SAVE_INTERVAL=32，应只在第 32 条时保存一次）
2. 不调用 close()，模拟崩溃（_unsaved_count=18 未保存）
3. 重新实例化，验证 _load_chain_state 通过 _rebuild_chain_state_from_log 重建到 50
4. verify_integrity() 校验 50 条日志链完整
5. 继续写入第 51 条，链应保持连续
6. close 后状态文件应同步到 51
"""

import json
from pathlib import Path


def test_chain_state_periodic_save_and_crash_recovery(tmp_path):
    """周期性保存链状态下，崩溃后能通过日志文件重建链状态。"""
    from app.agent.middleware import AgentAuditLog

    log_path = str(tmp_path / "audit.log")

    # 阶段1：写入 50 条，不调用 close()（模拟崩溃）
    log = AgentAuditLog(log_path=log_path)
    for i in range(50):
        log.log(f"agent_{i}", "/test", "read", 200, 1.0)
    # 故意不调用 close()，模拟进程崩溃

    chain_state_file = tmp_path / "agent_audit_chain_state.json"
    state = json.loads(chain_state_file.read_text(encoding="utf-8"))
    assert state["chain_seq"] == 32, f"状态文件应在第 32 条时保存，但实际为 {state['chain_seq']}"

    # 阶段2：重启，应通过 _rebuild_chain_state_from_log 重建到 50
    log2 = AgentAuditLog(log_path=log_path)
    assert log2._chain_seq == 50, f"重建失败: expected 50, got {log2._chain_seq}"

    # 阶段3：完整性校验
    is_valid, breaks = log2.verify_integrity()
    assert is_valid, f"完整性校验失败: {breaks[:3]}"

    # 阶段4：重启后继续写入，链应保持连续
    log2.log("agent_final", "/final", "read", 200, 1.0)
    is_valid, breaks = log2.verify_integrity()
    assert is_valid and len(breaks) == 0, f"链断裂: {breaks[:3]}"

    # 阶段5：close 后状态文件应同步到 51
    log2.close()
    state_after_close = json.loads(chain_state_file.read_text(encoding="utf-8"))
    assert state_after_close["chain_seq"] == 51, f"close 应将状态同步到 51，但实际为 {state_after_close['chain_seq']}"


def test_chain_state_save_interval_constant():
    """验证 _CHAIN_STATE_SAVE_INTERVAL 常量存在且为合理值。"""
    from app.agent.middleware import AgentAuditLog

    interval = AgentAuditLog._CHAIN_STATE_SAVE_INTERVAL
    assert isinstance(interval, int) and interval > 1, f"_CHAIN_STATE_SAVE_INTERVAL 应为 >1 的整数，实际为 {interval}"
    # 32 是性能与崩溃恢复成本的平衡点：
    # - 太小（如 1）退化为每次保存，性能差
    # - 太大（如 1000）崩溃时重建成本高
    assert interval <= 256, f"_CHAIN_STATE_SAVE_INTERVAL 过大（{interval}），崩溃时重建成本高"
