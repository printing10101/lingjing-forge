import math
import pytest
from probe import analytic_contact,clearance,sampled_contact,run


@pytest.mark.parametrize('tool_radius',[2.,5.,8.])
def test_analytic_against_independent_dense_sampling(tool_radius):
    exact=analytic_contact(5.,tool_radius,4.)
    # Independent formula, not calling clearance().
    limit=min(tool_radius,4.)
    sampled=min(tool_radius-math.sqrt(max(0.,tool_radius**2-(limit*i/20000)**2))-(limit*i/20000)**2/10 for i in range(20001))
    assert abs(exact['min_clearance_mm']-sampled)<1e-6
    assert exact['gouges']==(tool_radius>5)


def test_interior_witness_and_scale():
    ref=analytic_contact(5.,6.,10.)
    assert 0<ref['witness_x_mm']<6 and ref['min_clearance_mm']<0
    for scale in (.1,10.):
        scaled=analytic_contact(5*scale,6*scale,10*scale)
        assert scaled['min_clearance_mm']==pytest.approx(scale*ref['min_clearance_mm'])
    assert sampled_contact(5,6,10)==pytest.approx(ref['min_clearance_mm'],abs=1e-6)


@pytest.mark.parametrize('values',[(0,2,4),(5,-1,4),(5,2,math.nan),(True,2,4),(5,2,math.inf)])
def test_invalid_geometry(values):
    with pytest.raises(ValueError):analytic_contact(*values)


def test_invalid_domain_and_samples():
    with pytest.raises(ValueError):clearance(3,5,2)
    with pytest.raises(ValueError):sampled_contact(5,2,4,1)


def test_frozen_output(tmp_path):
    output=tmp_path/'probe.json';report=run(output)
    assert len(report['rows'])==9
    assert sum(r['gouges'] for r in report['rows'])==3
    assert all(not r['normal_only_undercut'] for r in report['rows'])
    with pytest.raises(FileExistsError):run(output)
