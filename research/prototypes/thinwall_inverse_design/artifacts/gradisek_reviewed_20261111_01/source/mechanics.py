"""SI-unit cantilever-strip FE model and scalar regenerative cutting models.

This is a verification pilot, not a shell model or an experimentally validated
machining predictor. The tool is rigid; damping and cutting coefficients are
declared scenario assumptions. No learned model or engineering-side import.
"""

from dataclasses import dataclass

import numpy as np
from scipy.linalg import eigh, expm
from scipy.optimize import brentq


def positive(value, name):
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"[参数错误] {name} 必须为有限正数。建议操作：检查单位和配置。")


@dataclass(frozen=True)
class Strip:
    length_m: float = 0.08
    width_m: float = 0.02
    young_pa: float = 70e9
    density_kg_m3: float = 2700.0

    def __post_init__(self):
        for name, value in vars(self).items():
            positive(value, name)


@dataclass(frozen=True)
class Modes:
    omega_rad_s: np.ndarray
    participation: np.ndarray
    damping_ratio: float

    def __post_init__(self):
        w, v = np.asarray(self.omega_rad_s), np.asarray(self.participation)
        if w.ndim != 1 or not w.size or w.shape != v.shape:
            raise ValueError("[模态错误] 频率和参与因子必须为等长非空向量。建议操作：重新提取模态。")
        if not np.all(np.isfinite(w)) or not np.all(w > 0) or not np.all(np.isfinite(v)):
            raise ValueError("[模态错误] 模态数据含非法数值。建议操作：检查约束和特征值。")
        if not 0 < self.damping_ratio < 1:
            raise ValueError("[阻尼错误] 阻尼比必须在 (0,1) 内。建议操作：检查配置。")

    def frf(self, omega):
        omega = np.asarray(omega)
        w = self.omega_rad_s
        denominator = w[:, None] ** 2 - omega.reshape(1, -1) ** 2
        denominator = denominator + 2j * self.damping_ratio * w[:, None] * omega.reshape(1, -1)
        value = (self.participation[:, None] ** 2 / denominator).sum(axis=0)
        return value[0] if omega.ndim == 0 else value.reshape(omega.shape)


def assemble_strip(thickness_m, elements=24, strip=Strip()):
    """Three equal-height constant-thickness zones, root fully clamped.

    Two-node Euler-Bernoulli Hermite elements, consistent mass, transverse
    displacement/rotation DOFs. Mass is computed geometrically before clamping.
    """
    thickness = np.asarray(thickness_m, dtype=float)
    if thickness.shape != (3,) or not np.all(np.isfinite(thickness)) or np.any(thickness <= 0):
        raise ValueError("[几何错误] 需三个正壁厚（米）。建议操作：检查根部到顶部的壁厚。")
    if not isinstance(elements, int) or elements < 3 or elements % 3:
        raise ValueError("[网格错误] 单元数须为不小于3的3倍数。建议操作：使用12、24或48。")
    length = strip.length_m / elements
    ndof = 2 * (elements + 1)
    stiffness, mass = np.zeros((ndof, ndof)), np.zeros((ndof, ndof))
    elastic = np.array([[12, 6*length, -12, 6*length],
                        [6*length, 4*length**2, -6*length, 2*length**2],
                        [-12, -6*length, 12, -6*length],
                        [6*length, 2*length**2, -6*length, 4*length**2]])
    inertia = np.array([[156, 22*length, 54, -13*length],
                        [22*length, 4*length**2, 13*length, -3*length**2],
                        [54, 13*length, 156, -22*length],
                        [-13*length, -3*length**2, -22*length, 4*length**2]])
    for element in range(elements):
        t = thickness[element // (elements // 3)]
        area, second_moment = strip.width_m * t, strip.width_m * t**3 / 12
        dofs = np.arange(2 * element, 2 * element + 4)
        stiffness[np.ix_(dofs, dofs)] += strip.young_pa * second_moment / length**3 * elastic
        mass[np.ix_(dofs, dofs)] += strip.density_kg_m3 * area * length / 420 * inertia
    total_mass = strip.density_kg_m3 * strip.width_m * strip.length_m * thickness.mean()
    return stiffness[2:, 2:], mass[2:, 2:], float(total_mass)


def contact_vector(position_fraction, elements=24, strip=Strip()):
    if not np.isfinite(position_fraction) or not 0 < position_fraction <= 1:
        raise ValueError("[位置错误] 接触位置必须在 (0,1]。建议操作：使用相对悬臂长度的位置。")
    element = min(int(position_fraction * elements), elements - 1)
    xi = position_fraction * elements - element
    length = strip.length_m / elements
    shape = [1 - 3*xi**2 + 2*xi**3, length*(xi - 2*xi**2 + xi**3),
             3*xi**2 - 2*xi**3, length*(-xi**2 + xi**3)]
    force = np.zeros(2*(elements + 1))
    force[2*element:2*element + 4] = shape
    return force[2:]


def extract_modes(thickness_m, position_fraction=1.0, elements=24, count=4,
                  damping_ratio=0.01, strip=Strip()):
    stiffness, mass, total_mass = assemble_strip(thickness_m, elements, strip)
    if not isinstance(count, int) or not 1 <= count <= len(stiffness):
        raise ValueError("[模态错误] 模态数超出自由度。建议操作：降低保留模态数。")
    force = contact_vector(position_fraction, elements, strip)
    eigenvalues, shapes = eigh(stiffness, mass, subset_by_index=[0, count - 1])
    compliance = float(force @ np.linalg.solve(stiffness, force))
    return Modes(np.sqrt(eigenvalues), force @ shapes, damping_ratio), compliance, total_mass


@dataclass(frozen=True)
class Cutting:
    tangential_pa: float = 700e6
    radial_ratio: float = 0.3
    teeth: int = 2
    immersion: float = 0.5

    def __post_init__(self):
        positive(self.tangential_pa, "tangential_pa")
        if not np.isfinite(self.radial_ratio) or self.radial_ratio < 0:
            raise ValueError("[切削错误] 径向力系数比不得为负。建议操作：检查参数。")
        if not isinstance(self.teeth, int) or self.teeth < 1 or not 0 < self.immersion <= 0.5:
            raise ValueError("[切削错误] 需正整数齿数，径向浸入比在(0,0.5]。建议操作：使用低至半浸入顺序模型。")

    def directional(self, phase):
        """Ideal zero-helix up-milling, feed-direction scalar displacement.

        Dynamic chip thickness = sin(phi)*(x(t)-x(t-tau));
        resisting force = -Kt*b*(cos(phi)+Kr*sin(phi))*chip thickness.
        phi in [0, acos(1-2ae/D)] is engaged. Other modes/directions omitted.
        """
        phi = np.mod(np.asarray(phase)[..., None] + 2*np.pi*np.arange(self.teeth)/self.teeth, 2*np.pi)
        engaged = phi < np.arccos(1 - 2*self.immersion)
        return np.sum(engaged*np.sin(phi)*(np.cos(phi) + self.radial_ratio*np.sin(phi)), axis=-1)

    def mean_directional(self):
        exit_angle = np.arccos(1 - 2*self.immersion)
        integral = 0.5*np.sin(exit_angle)**2
        integral += self.radial_ratio*(exit_angle/2 - np.sin(2*exit_angle)/4)
        return float(self.teeth/(2*np.pi)*integral)


def frequency_grid(modes, resolution=600):
    w = modes.omega_rad_s
    # Include dense modal neighborhoods; a uniform grid misses narrow resonances.
    return np.unique(np.concatenate([
        np.linspace(w[0]*0.02, w[-1]*1.8, resolution*2),
        *[np.linspace(0.65*wi, 1.4*wi, resolution) for wi in w],
    ]))


def averaged_boundary(modes, rpm, cutting=Cutting(), resolution=600):
    """First positive imaginary-axis crossing of the constant-mean DDE.

    Characteristic equation: 1 + b*Kt*hbar*G(s)*(1-exp(-s*tau)) = 0.
    This is a screening proxy, NOT the full periodically interrupted milling map.
    """
    positive(rpm, "rpm")
    delay = 60 / (rpm * cutting.teeth)
    omega = frequency_grid(modes, resolution)
    loop = modes.frf(omega) * (1 - np.exp(-1j*omega*delay))
    indices = np.flatnonzero(loop.imag[:-1]*loop.imag[1:] < 0)
    depths = []
    def loop_at(w):
        return modes.frf(w) * (1 - np.exp(-1j*w*delay))
    for index in indices:
        root = brentq(lambda w: loop_at(w).imag, omega[index], omega[index+1], xtol=1e-8)
        gain = loop_at(root).real
        if gain < -1e-14:
            depths.append(-1 / (cutting.tangential_pa * cutting.mean_directional() * gain))
    if not depths:
        raise RuntimeError("[边界错误] 频率扫描未找到正边界。建议操作：扩大频率范围和分辨率。")
    return float(min(depths))


def state_matrices(modes, depth_m, directional, cutting=Cutting()):
    count = len(modes.omega_rad_s)
    v = modes.participation
    gain = depth_m * cutting.tangential_pa * directional
    stiffness = np.diag(modes.omega_rad_s**2) + gain*np.outer(v, v)
    damping = np.diag(2*modes.damping_ratio*modes.omega_rad_s)
    current = np.block([[np.zeros((count, count)), np.eye(count)], [-stiffness, -damping]])
    delayed = np.r_[np.zeros(count), gain*v]
    return current, delayed


def floquet_radius(modes, rpm, depth_m, steps=80, cutting=Cutting(), averaged=False):
    """Zeroth-order delayed-signal semi-discretization over one tooth period.

    Midpoint piecewise-constant instantaneous coefficient, left-endpoint delayed
    displacement, exact matrix exponential within each interval. First order in
    delay approximation; convergence must be checked before trusting a boundary.
    """
    positive(rpm, "rpm")
    if not np.isfinite(depth_m) or depth_m < 0 or not isinstance(steps, int) or steps < 8:
        raise ValueError("[离散错误] 切深须非负且步数至少8。建议操作：检查配置。")
    count = len(modes.omega_rad_s)
    tau = 60/(rpm*cutting.teeth)
    if depth_m == 0:
        return float(np.exp(-modes.damping_ratio*modes.omega_rad_s.min()*tau))
    dt, dynamic = tau/steps, 2*count
    phase = (np.arange(steps) + 0.5) * 2*np.pi/(cutting.teeth*steps)
    coefficients = np.full(steps, cutting.mean_directional()) if averaged else cutting.directional(phase)
    transition = np.eye(dynamic + steps)
    for coefficient in coefficients:
        current, delayed = state_matrices(modes, depth_m, float(coefficient), cutting)
        augmented = np.zeros((dynamic + 1, dynamic + 1))
        augmented[:dynamic, :dynamic] = current
        augmented[:dynamic, -1] = delayed
        exponential = expm(augmented*dt)
        updated = np.empty_like(transition)
        updated[:dynamic] = exponential[:dynamic, :dynamic] @ transition[:dynamic]
        updated[:dynamic] += np.outer(exponential[:dynamic, -1], transition[-1])
        updated[dynamic] = modes.participation @ transition[:count]
        updated[dynamic+1:] = transition[dynamic:-1]
        transition = updated
    return float(np.abs(np.linalg.eigvals(transition)).max())


def periodic_boundary(modes, rpm, steps=80, cutting=Cutting(), cap_m=0.02):
    """Search from zero depth, bracket first observed loss of stability.

    Geometric bracketing can miss a narrow stable/unstable pocket. Record this
    limitation; this pilot does not certify a globally complete stability map.
    Returns None when no crossing was bracketed, never clips to a fake boundary.
    """
    positive(cap_m, "cap_m")
    lower = 0.0
    for upper in np.geomspace(min(1e-5, cap_m), cap_m, 22):
        if floquet_radius(modes, rpm, upper, steps, cutting) > 1:
            return float(brentq(lambda b: floquet_radius(modes, rpm, b, steps, cutting)-1,
                                lower, upper, xtol=1e-8))
        lower = upper
    return None


def history_growth(modes, rpm, depth_m, steps_per_tooth=400, periods=80, cutting=Cutting()):
    """Independent RK4 method-of-steps integration of the same linear DDE.

    This checks numerical consistency, not independent physical validity.
    Prescribed constant modal displacement history, zero velocity; linearly
    interpolate delayed stage values (strictly in already available history).
    """
    positive(rpm, "rpm")
    if depth_m < 0 or not np.isfinite(depth_m) or steps_per_tooth < 8 or periods < 4:
        raise ValueError("[时域错误] 配置非法。建议操作：使用正切深、至少8步及4周期。")
    count = len(modes.omega_rad_s)
    tau, steps = 60/(rpm*cutting.teeth), int(steps_per_tooth)
    dt, total = tau/steps, steps*int(periods)
    # Normalize the first-mode surface history to 1 micrometer.
    initial = np.zeros(2*count)
    initial[0] = 1e-6/max(abs(modes.participation[0]), 1e-12)
    trajectory = np.zeros((total+1, 2*count))
    surface = np.zeros(total+1)
    trajectory[0], surface[0] = initial, modes.participation @ initial[:count]
    def rhs(time_index, state):
        delayed_index = time_index - steps
        if delayed_index <= 0:
            delayed_surface = surface[0]
        else:
            left = int(np.floor(delayed_index))
            fraction = delayed_index-left
            delayed_surface = (1-fraction)*surface[left] + fraction*surface[left+1]
        phase = time_index*dt*rpm*2*np.pi/60
        current, delayed = state_matrices(modes, depth_m, float(cutting.directional(phase)), cutting)
        return current @ state + delayed*delayed_surface
    for i in range(total):
        state = trajectory[i]
        k1 = rhs(i, state)
        k2 = rhs(i+0.5, state+dt*k1/2)
        k3 = rhs(i+0.5, state+dt*k2/2)
        k4 = rhs(i+1, state+dt*k3)
        trajectory[i+1] = state + dt*(k1+2*k2+2*k3+k4)/6
        surface[i+1] = modes.participation @ trajectory[i+1, :count]
    amplitudes = np.max(np.abs(surface[1:].reshape(int(periods), steps)), axis=1)
    tail = np.maximum(amplitudes[len(amplitudes)//2:], 1e-300)
    log_growth_per_period = float(np.polyfit(np.arange(len(tail)), np.log(tail), 1)[0])
    return log_growth_per_period, amplitudes
