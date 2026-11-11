import numpy as np
import pytest
from prototypes.thinwall_inverse_design.plate_spline import SplinePlate,axis_basis,basis,contact_modes,solve_plate
from prototypes.thinwall_inverse_design.plate import Plate,solve_plate as global_solve


def test_stepped_beam_static_exact_limit_catches_interface_error():
    p=SplinePlate(poisson=0,segments_x=2,segments_y=2)
    thickness=np.array([[0.003,0.003],[0.001,0.001]])
    result=solve_plate(thickness,p)
    l=p.length_m
    expected=((l**3-(l/2)**3)/(p.young_pa*p.width_m*0.003**3/12)
              +(l/2)**3/(p.young_pa*p.width_m*0.001**3/12))/3
    assert result["compliances"]["free_edge_uniform"]==pytest.approx(expected,rel=1e-9)
    assert result["mass_kg"]==pytest.approx(p.density_kg_m3*p.length_m*p.width_m*0.002)


def test_spline_and_independent_global_basis_agree_for_smooth_uniform_plate():
    a=solve_plate(np.full((2,2),0.002),SplinePlate(segments_x=6,segments_y=6))
    b=global_solve(np.full((2,2),0.002),Plate(order_x=10,order_y=9,quadrature=16))
    assert np.allclose(a["omega"][:4],b["omega"][:4],rtol=0.003)
    assert a["compliances"]["normal_pressure"]==pytest.approx(b["compliances"]["normal_pressure"],rel=0.002)


def test_root_clamp_interface_partition_and_frf_closure():
    p=SplinePlate(segments_x=2,segments_y=2)
    assert np.all(basis(p,0,np.linspace(0,1,5))[0]==0)
    assert np.all(axis_basis([0],2,True)[1]==0)
    # The y basis retains partition of unity including endpoints and interfaces.
    assert np.allclose(axis_basis([0,0.25,0.5,0.75,1],2)[0].sum(axis=1),1)
    result=solve_plate(np.full((2,2),0.002),p,count=24)
    mode=contact_modes(result,[1,0.5],p)
    force=basis(p,1,0.5)[0][0]
    assert mode.frf(0).real==pytest.approx(force @ np.linalg.solve(result["stiffness"],force),rel=1e-9)


def test_invalid_spline_inputs():
    with pytest.raises(ValueError): SplinePlate(segments_x=3)
    with pytest.raises(ValueError): SplinePlate(quadrature=3)
    with pytest.raises(ValueError): solve_plate([[0,1],[1,1]])
    with pytest.raises(ValueError): solve_plate(np.full((2,2),0.002),count=1000)
    with pytest.raises(ValueError): contact_modes({},[1,2])


def test_coarse_global_false_feasibility_is_rejected_after_interface_resolution():
    thickness=np.array([[0.003,0.003],[0.001,0.001]])
    uniform=np.full((2,2),0.002)
    global_plate=Plate(order_x=6,order_y=5,quadrature=12)
    coarse=global_solve(thickness,global_plate)["compliances"]["free_edge_left_half"]
    coarse_reference=global_solve(uniform,global_plate)["compliances"]["free_edge_left_half"]
    spline_plate=SplinePlate(segments_x=4,segments_y=4)
    resolved=solve_plate(thickness,spline_plate)["compliances"]["free_edge_left_half"]
    resolved_reference=solve_plate(uniform,spline_plate)["compliances"]["free_edge_left_half"]
    assert coarse/coarse_reference<1
    assert resolved/resolved_reference>1.1
