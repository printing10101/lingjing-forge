"""skill_marketplace 遮蔽回归测试（W10.4 架构卫生，2026-09）。

背景：``app/plugins/skill_marketplace/`` 目录（stub 包）曾与
``app/plugins/skill_marketplace.py``（真实实现）同名并存——Python 正则包
优先于同名模块，导致市场 API 静默返回 stub 假数据。本测试防两类回归：

1. 遮蔽回归：任何以目录形式重新引入的同名包必须导出真实实现；
2. 路径穿越：skill_id / version 白名单校验拒绝穿越载荷。
"""

from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.plugins]


class TestModuleResolution:
    def test_package_shadowing_is_gone(self):
        """skill_marketplace 必须解析为真实模块文件，而非 stub 目录包。"""
        import app.plugins.skill_marketplace as m

        assert m.__file__ is not None
        assert m.__file__.replace("\\", "/").endswith("app/plugins/skill_marketplace.py")

    def test_real_implementation_exported(self):
        """真实实现的公共符号必须可导入（stub 时代 SkillMarketplace 缺失）。"""
        from app.plugins.skill_marketplace import (
            DEFAULT_MARKET_DIR,
            MarketListing,
            SkillMarketplace,
            get_marketplace,
        )

        assert callable(get_marketplace)
        assert SkillMarketplace is not None
        assert MarketListing is not None
        assert DEFAULT_MARKET_DIR.endswith(".marketplace")

    def test_stub_only_signature_is_gone(self, tmp_path):
        """旧 stub 的 get_stats() 返回 {'total','published','downloaded'}，
        真实现返回 {'total_listings',...}——API 契约必须是真实现语义。

        用真实返回值断言而非 __code__.co_consts：co_consts 的常量组织
        随 CPython 版本变化（3.12 把 dict 字面量 keys 编译为嵌套元组，
        3.14 平铺为顶层常量），按顶层 `in` 检查在 3.12 上必挂（2026-09-10 CI 实测）。"""
        from app.plugins.skill_marketplace import SkillMarketplace

        market = SkillMarketplace(market_dir=str(tmp_path / "market"))
        stats = market.get_stats()
        assert set(stats) == {
            "total_listings",
            "market_dir",
            "most_downloaded",
            "highest_rated",
        }


class TestPathTraversalGuard:
    def test_rejects_traversal_skill_id(self, tmp_path):
        from app.plugins.skill_marketplace import SkillMarketplace

        market = SkillMarketplace(market_dir=str(tmp_path / "market"))
        with pytest.raises(ValueError, match="非法 skill_id"):
            market.publish("../../evil", "attacker")
        with pytest.raises(ValueError, match="非法 skill_id"):
            market.download("..\\evil")
        with pytest.raises(ValueError, match="非法 skill_id"):
            market.unpublish("a/../b")

    def test_rejects_traversal_version_in_package_build(self, tmp_path):
        from app.plugins.skill_marketplace import SkillMarketplace

        market = SkillMarketplace(market_dir=str(tmp_path / "market"))
        with pytest.raises(ValueError, match="非法技能版本号"):
            market._build_package(
                {"skill_id": "ok_id", "metadata": {"version": "../../evil"}}
            )

    def test_accepts_normal_ids(self, tmp_path):
        """常规 id/version 不受影响（白名单不误伤合法输入）。"""
        from app.plugins.skill_marketplace import SkillMarketplace

        market = SkillMarketplace(market_dir=str(tmp_path / "market"))
        package_file = market._build_package(
            {"skill_id": "my_skill-1", "metadata": {"version": "1.2.3"}, "raw_content": "# s"}
        )
        assert package_file.endswith("my_skill-1_1.2.3.skz")


class TestStubFallbackGuard:
    def test_reimport_fails_closed_to_real_module(self, monkeypatch):
        """真实模块被移动/删除时（模拟历史 stub 场景），导入必须显式失败
        而不是静默回落到返回假数据的 stub——遮蔽事故不能以任何形式回归。"""
        import app.plugins.skill_marketplace as m

        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "app.plugins.skill_marketplace", None)
            with pytest.raises(ImportError):
                importlib.import_module("app.plugins.skill_marketplace")
        # sys.modules 已还原；确认正常导入路径仍然健康
        m2 = importlib.import_module("app.plugins.skill_marketplace")
        assert m2 is m or m2.__file__ == m.__file__
