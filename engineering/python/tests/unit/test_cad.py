"""cad 模块单元测试（高级特征 spec dataclass）。"""

from __future__ import annotations

import pytest

from app.cad.advanced_features import (
    ChamferSpec,
    FilletSpec,
    SlotSpec,
    StepSpec,
)

pytestmark = pytest.mark.unit


class TestAdvancedFeatureSpecs:
    def test_chamfer_defaults_and_dict(self):
        c = ChamferSpec()
        assert c.length == 1.0
        assert c.angle == 45.0
        assert c.to_dict() == {'type': 'chamfer', 'length': 1.0, 'angle': 45.0, 'edges_selector': '|'}

    def test_chamfer_custom(self):
        c = ChamferSpec(length=2.0, angle=30.0, edges_selector='>Z')
        assert c.to_dict()['length'] == 2.0
        assert c.to_dict()['angle'] == 30.0

    def test_fillet_defaults_and_dict(self):
        f = FilletSpec(radius=2.0)
        assert f.to_dict() == {'type': 'fillet', 'radius': 2.0, 'edges_selector': '|'}

    def test_step_defaults_and_dict(self):
        s = StepSpec(offset_x=1.0, length=10.0)
        d = s.to_dict()
        assert d['type'] == 'step'
        assert d['offset_x'] == 1.0
        assert d['length'] == 10.0
        assert d['width'] is None
        assert d['height'] is None

    def test_slot_defaults_and_dict(self):
        s = SlotSpec()
        d = s.to_dict()
        assert d['type'] == 'slot'
        assert d['length'] == 20.0
        assert d['width'] == 5.0
        assert d['depth'] == 2.5
        assert d['axis'] == 'x'
        assert d['surface_z'] is None

    def test_slot_custom(self):
        s = SlotSpec(center_x=1.0, center_y=2.0, axis='y', surface_z=10.0)
        d = s.to_dict()
        assert d['center_x'] == 1.0
        assert d['axis'] == 'y'
        assert d['surface_z'] == 10.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
