import numpy as np
import pytest
from prototypes.thinwall_inverse_design.coupled_milling import CoupledModes,Milling,coupled_radius
from prototypes.thinwall_inverse_design.mechanics import Cutting,Modes,floquet_radius


def test_directional_signs_against_direct_force_projection():
    milling=Milling(helix_deg=0,immersion=0.5)
    phi=0.3
    matrix=milling.directional(phi)
    displacement=np.array([0.2,-0.1])
    chip=displacement @ [np.sin(phi),np.cos(phi)]
    expected=chip*np.array([-np.cos(phi)-0.37*np.sin(phi),np.sin(phi)-0.37*np.cos(phi)])
    assert np.allclose(matrix @ displacement,expected)
    assert np.all(milling.directional(4.0)==0)


def test_scalar_projection_matches_previous_separate_solver():
    w=np.array([1000.0,2100.0])
    participation=np.array([2.0,0.5])
    coupled=CoupledModes(w,np.full(2,0.02),np.vstack([participation,np.zeros(2)]))
    scalar=Modes(w,participation,0.02)
    cutting=Cutting(tangential_pa=644e6,radial_ratio=0.37,teeth=1)
    actual=coupled_radius(coupled,18000,0.0003,80,Milling(helix_deg=0),delay_rule="left")
    assert actual==pytest.approx(floquet_radius(scalar,18000,0.0003,80,cutting),rel=1e-9)
    assert coupled_radius(coupled,18000,0)==pytest.approx(np.exp(-20/300))


def test_two_axes_and_helix_are_active():
    modes=CoupledModes(np.array([4500.0,4530.0]),np.array([0.01,0.012]),np.diag([7.0,7.2]))
    straight=coupled_radius(modes,25000,0.0003,80,Milling(helix_deg=0,immersion=0.1))
    helical=coupled_radius(modes,25000,0.0003,80,Milling(helix_deg=45,immersion=0.1))
    assert np.isfinite(helical) and helical>0
    assert abs(straight-helical)>1e-6
    inactive=CoupledModes(np.array([1000.0]),np.array([0.02]),np.zeros((2,1)))
    assert coupled_radius(inactive,6000,0.001)<1


@pytest.mark.parametrize("kwargs",[{"tangential_pa":0},{"diameter_m":0},{"teeth":0},{"immersion":0},{"helix_deg":90}])
def test_invalid_milling(kwargs):
    with pytest.raises(ValueError): Milling(**kwargs)


def test_invalid_coupled_modes_and_solver():
    with pytest.raises(ValueError): CoupledModes(np.array([1]),np.array([0.1]),np.ones((1,1)))
    with pytest.raises(ValueError): CoupledModes(np.array([-1]),np.array([0.1]),np.ones((2,1)))
    good=CoupledModes(np.array([1000.0]),np.array([0.01]),np.ones((2,1)))
    with pytest.raises(ValueError): coupled_radius(good,6000,-1)
