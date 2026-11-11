import numpy as np
import pytest
from prototypes.thinwall_inverse_design.plate import Plate, assemble_plate, basis, contact_modes, solve_plate
from prototypes.thinwall_inverse_design.mechanics import Strip, extract_modes


def test_simply_supported_navier_frequencies_and_unit_pressure_compliance():
    plate=Plate(boundary="simply_supported",order_x=4,order_y=4,quadrature=20)
    t=0.002
    result=solve_plate(np.full((2,2),t),plate,count=6)
    rigidity=plate.young_pa*t**3/(12*(1-plate.poisson**2))
    eigenvalues=[]
    compliance=0.0
    for i in range(1,5):
        for j in range(1,5):
            beta=(i*np.pi/plate.length_m)**2+(j*np.pi/plate.width_m)**2
            eigenvalues.append(np.sqrt(rigidity/(plate.density_kg_m3*t))*beta)
            if i%2 and j%2:
                # For unit total pressure force: f_i=4/(i*j*pi²), K_i=D*beta²*area/4.
                compliance+=(4/(i*j*np.pi**2))**2/(rigidity*beta**2*plate.length_m*plate.width_m/4)
    assert np.allclose(result["omega"],sorted(eigenvalues)[:6],rtol=1e-9)
    assert result["compliances"]["normal_pressure"] == pytest.approx(compliance,rel=1e-9)


def test_plate_reduces_to_beam_for_zero_poisson_and_constant_width_mode():
    plate=Plate(poisson=0,order_x=7,order_y=1,quadrature=12)
    result=solve_plate(np.full((2,2),0.002),plate,count=3)
    beam,compliance,mass=extract_modes([0.002]*3,count=3,strip=Strip(width_m=plate.width_m))
    assert result["omega"][0] == pytest.approx(beam.omega_rad_s[0],rel=1e-6)
    assert result["compliances"]["free_edge_uniform"] == pytest.approx(compliance,rel=1e-8)
    assert result["mass_kg"] == pytest.approx(mass)


def test_clamp_shape_and_thickness_mass_invariance():
    plate=Plate()
    shape=basis(plate,0,np.linspace(0,1,10))[0]
    assert np.all(shape==0)
    # Zero normal slope at root by a one-sided difference.
    eps=1e-7
    assert np.max(abs(basis(plate,eps,np.linspace(0,1,10))[0]/eps)) < 1e-5
    a=solve_plate([[0.003,0.002],[0.002,0.001]],plate)
    b=solve_plate([[0.002,0.003],[0.001,0.002]],plate)
    assert a["mass_kg"] == pytest.approx(b["mass_kg"])
    assert np.allclose(a["omega"],b["omega"],rtol=1e-8)
    for matrix in [a["stiffness"],a["mass"]]:
        assert np.allclose(matrix,matrix.T)
        assert np.linalg.eigvalsh(matrix).min()>0
    # A complete modal basis closes the zero-frequency flexibility identity.
    full=solve_plate(np.full((2,2),0.002),plate,count=30)
    mode=contact_modes(full,[1,0.5],plate)
    force=basis(plate,1,0.5)[0][0]
    expected=force @ np.linalg.solve(full["stiffness"],force)
    assert mode.frf(0).real == pytest.approx(expected,rel=1e-7)


@pytest.mark.parametrize("kwargs",[{"young_pa":0},{"poisson":0.5},{"order_x":0},{"quadrature":1},{"boundary":"invalid"}])
def test_bad_plate_configuration(kwargs):
    with pytest.raises(ValueError): Plate(**kwargs)


def test_invalid_plate_geometry_mode_and_contact():
    with pytest.raises(ValueError): assemble_plate([[0.002,0],[0.002,0.002]])
    with pytest.raises(ValueError): solve_plate(np.full((2,2),0.002),count=100)
    solution=solve_plate(np.full((2,2),0.002))
    with pytest.raises(ValueError): contact_modes(solution,[0,0.5])
