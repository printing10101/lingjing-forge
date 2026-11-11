"""Independent limits and invariants; deterministic scenarios are not replicates."""

import numpy as np
import pytest
from scipy.linalg import eigh

from prototypes.thinwall_inverse_design.mechanics import (
    Cutting, Modes, Strip, assemble_strip, averaged_boundary, contact_vector,
    extract_modes, floquet_radius, history_growth, periodic_boundary,
)


def test_uniform_beam_against_closed_form():
    strip, t = Strip(), 0.002
    modes, compliance, mass = extract_modes([t]*3, elements=24, count=3, strip=strip)
    inertia, area = strip.width_m*t**3/12, strip.width_m*t
    expected_hz = np.array([1.875104068711961, 4.694091132974174, 7.854757438237612])**2
    expected_hz *= np.sqrt(strip.young_pa*inertia/(strip.density_kg_m3*area))/strip.length_m**2/(2*np.pi)
    assert np.allclose(modes.omega_rad_s/(2*np.pi), expected_hz, rtol=2e-5)
    assert compliance == pytest.approx(strip.length_m**3/(3*strip.young_pa*inertia), rel=1e-8)
    assert mass == pytest.approx(strip.density_kg_m3*area*strip.length_m)


def test_equal_mass_and_static_modal_closure():
    thickness = [0.003, 0.002, 0.001]
    stiffness, mass, total = assemble_strip(thickness, elements=12)
    assert np.allclose(stiffness, stiffness.T)
    assert np.allclose(mass, mass.T)
    assert np.linalg.eigvalsh(stiffness).min() > 0
    assert np.linalg.eigvalsh(mass).min() > 0
    assert total == pytest.approx(assemble_strip([0.002]*3, 12)[2])
    modes, compliance, _ = extract_modes(thickness, elements=12, count=24)
    assert modes.frf(0).real == pytest.approx(compliance, rel=1e-7)
    assert modes.frf(0).imag == 0


def test_clamped_root_and_hermite_force_distribution():
    force = contact_vector(1, 12)
    assert force[-2] == 1
    assert np.count_nonzero(force) == 1
    interior = contact_vector(0.51, 12)
    assert interior[::2].sum() == pytest.approx(1)


@pytest.mark.parametrize("thickness,elements", [([0,1,2],12), ([1,2],12), ([1,np.nan,2],12), ([1,2,3],11)])
def test_bad_geometry_rejected(thickness, elements):
    with pytest.raises(ValueError):
        assemble_strip(thickness, elements)


@pytest.mark.parametrize("position", [0, -1, 1.1, np.nan])
def test_bad_contact_rejected(position):
    with pytest.raises(ValueError):
        contact_vector(position)


def test_invalid_modes_and_material_rejected():
    for args in [([], [], 0.01), ([0], [1], 0.01), ([1], [1], 0), ([1], [np.nan], 0.01)]:
        with pytest.raises(ValueError):
            Modes(np.array(args[0]), np.array(args[1]), args[2])
    with pytest.raises(ValueError):
        Strip(young_pa=-1)
    with pytest.raises(ValueError):
        extract_modes([0.002]*3, count=0)


def test_milling_mean_coefficient_against_quadrature_and_periodicity():
    cutting = Cutting()
    phase = (np.arange(40000)+0.5)*2*np.pi/40000
    measured = cutting.directional(phase).mean()
    assert measured == pytest.approx(cutting.mean_directional(), rel=1e-8)
    assert np.allclose(cutting.directional(phase[:1000]), cutting.directional(phase[:1000]+np.pi))
    for kwargs in ({"teeth": 0}, {"immersion": 0.7}, {"radial_ratio": -0.1}):
        with pytest.raises(ValueError):
            Cutting(**kwargs)


def test_open_loop_decay_exact_limit():
    modes = Modes(np.array([800.0, 1600.0]), np.array([2.0, 1.0]), 0.02)
    radius = floquet_radius(modes, 6000, 0, 40)
    assert radius == pytest.approx(np.exp(-0.02*800*0.005), rel=1e-12)


def test_averaged_boundary_independent_sdof_hopf_formula():
    # Scalar characteristic real/imag equations, derived without FE or root scanner.
    wn, zeta, w = 1000.0, 0.02, 1200.0
    dynamic = wn**2-w**2 + 2j*zeta*wn*w
    phase = (np.pi-2*np.angle(dynamic)) % (2*np.pi)
    tau = phase/w
    rpm = 60/(2*tau)
    exact = -dynamic/(Cutting().tangential_pa*Cutting().mean_directional()*(1-np.exp(-1j*w*tau)))
    assert abs(exact.imag) < 1e-12
    modes = Modes(np.array([wn]), np.array([1.0]), zeta)
    scanned = averaged_boundary(modes, rpm)
    assert scanned == pytest.approx(exact.real, rel=1e-7)
    # A separately discretized constant-coefficient Floquet map must cross here.
    below = floquet_radius(modes, rpm, scanned*0.8, 160, averaged=True)
    above = floquet_radius(modes, rpm, scanned*1.2, 160, averaged=True)
    assert below < 1 < above


def test_frequency_resolution_and_modal_truncation():
    modes, _, _ = extract_modes([0.002]*3)
    assert averaged_boundary(modes, 6750, resolution=300) == pytest.approx(
        averaged_boundary(modes, 6750, resolution=1200), rel=1e-5)


def test_periodic_boundary_and_independent_time_integration():
    modes = Modes(np.array([1000.0]), np.array([2.0]), 0.02)
    boundary = periodic_boundary(modes, 6750, steps=80)
    assert boundary is not None and boundary > 0
    for ratio in (0.6, 1.5):
        radius = floquet_radius(modes, 6750, boundary*ratio, 80)
        growth, amplitudes = history_growth(modes, 6750, boundary*ratio, 100, 60)
        assert (radius > 1) == (growth > 0)
        assert np.all(np.isfinite(amplitudes))
    assert periodic_boundary(modes, 6750, 40, cap_m=1e-10) is None


def test_invalid_solver_inputs():
    modes = Modes(np.array([1000.0]), np.array([1.0]), 0.02)
    with pytest.raises(ValueError):
        averaged_boundary(modes, 0)
    with pytest.raises(ValueError):
        floquet_radius(modes, 6000, -1)
    with pytest.raises(ValueError):
        history_growth(modes, 6000, 1e-3, periods=1)
