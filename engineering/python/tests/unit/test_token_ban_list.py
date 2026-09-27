"""TokenBanList 兜底键回归测试（2026-09-18 CI 根因）。

历史缺陷：jti 缺失时用 ``token[:32]`` 做黑名单键——JWT 的前 32 字符是同算法
（HS256）共享的 Base64 头部，封禁任意一个 token 后，同进程内后续签发的所有
JWT 全部命中黑名单。CI 串行全量下表现为 ``test_jwt_valid_token_passes`` 被
"Token已被撤销"误拒；生产等价于"封禁一个用户 = 全站锁死"。修复后兜底键为
整 token 的 sha256。
"""

import pytest

from app.auth.security import TokenBanList, create_access_token, decode_token


@pytest.fixture
def ban_list(tmp_path):
    return TokenBanList(file_path=str(tmp_path / "bans.json"))


def test_banning_one_token_does_not_ban_other_tokens(ban_list):
    banned = create_access_token({"sub": "u1", "role": "user"})
    other = create_access_token({"sub": "u2", "role": "user"})

    ban_list.ban(banned)

    assert ban_list.is_banned(banned)
    assert not ban_list.is_banned(other)
    assert decode_token(other) is not None


def test_ban_persists_across_reload_without_collisions(tmp_path):
    # 注意：HS256 是确定性签名——同秒内同 payload 会签出逐字节相同的 token，
    # 因此这里必须用不同 sub 造"不同的 token"，而非同 sub 重签。
    path = str(tmp_path / "bans.json")
    banned = create_access_token({"sub": "u1"})

    TokenBanList(file_path=path).ban(banned)
    reloaded = TokenBanList(file_path=path)

    assert reloaded.is_banned(banned)
    assert not reloaded.is_banned(create_access_token({"sub": "u1-different"}))


def test_shared_header_prefix_tokens_are_distinguishable(ban_list):
    """同算法 token 共享 JWT 头部前缀——旧的 token[:32] 键会让它们全部互撞。"""
    tokens = [create_access_token({"sub": f"user_{i}"}) for i in range(5)]
    assert len({t[:32] for t in tokens}) == 1, "前提成立：前 32 字符相同（回归陷阱）"

    ban_list.ban(tokens[0])

    assert ban_list.is_banned(tokens[0])
    assert not any(ban_list.is_banned(t) for t in tokens[1:])
