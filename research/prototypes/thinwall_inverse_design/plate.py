"""Kirchhoff-Love Rayleigh-Ritz plates; no FE/shell or physical-validation claim.

Tensor-product admissible Legendre polynomials enforce one clamped edge. Natural
free-edge conditions follow from the variational formulation. Thickness is four
constant patches, integrated separately, centered on a common midsurface (no
membrane-bending coupling). A Navier sine basis provides an analytic test case.
"""

from dataclasses import dataclass

import numpy as np
from numpy.polynomial import Legendre, Polynomial
from numpy.polynomial.legendre import leggauss
from scipy.linalg import eigh

from .mechanics import Modes, positive


@dataclass(frozen=True)
class Plate:
    length_m: float = 0.08
    width_m: float = 0.06
    young_pa: float = 70e9
    density_kg_m3: float = 2700.0
    poisson: float = 0.33
    order_x: int = 6
    order_y: int = 5
    quadrature: int = 12
    boundary: str = "cantilever"

    def __post_init__(self):
        for name in ("length_m", "width_m", "young_pa", "density_kg_m3"):
            positive(getattr(self, name), name)
        if not np.isfinite(self.poisson) or not -1 < self.poisson < 0.5:
            raise ValueError("[材料错误] 泊松比必须在(-1,0.5)。建议操作：检查材料配置。")
        if any(not isinstance(n, int) or n < 1 for n in (self.order_x, self.order_y)):
            raise ValueError("[基函数错误] 阶数必须为正整数。建议操作：检查Ritz阶数。")
        if not isinstance(self.quadrature, int) or self.quadrature < max(self.order_x, self.order_y)+2:
            raise ValueError("[积分错误] 积分阶数不足。建议操作：至少使用最大基函数阶数加2。")
        if self.boundary not in ("cantilever", "simply_supported"):
            raise ValueError("[边界错误] 不支持该边界。建议操作：选择cantilever或simply_supported。")


def one_axis_basis(coordinate, order, boundary, clamped=False):
    coordinate = np.atleast_1d(coordinate)
    values = []
    for index in range(order):
        if boundary == "simply_supported":
            frequency = (index+1)*np.pi
            values.append((np.sin(frequency*coordinate), frequency*np.cos(frequency*coordinate),
                           -frequency**2*np.sin(frequency*coordinate)))
        else:
            polynomial = Legendre.basis(index).convert(kind=Polynomial)(Polynomial([-1, 2]))
            if clamped:
                polynomial = polynomial * Polynomial([0,0,1])
            values.append(tuple(polynomial.deriv(k)(coordinate) for k in range(3)))
    return tuple(np.array([v[k] for v in values]) for k in range(3))


def basis(plate, x_fraction, y_fraction):
    x, y = np.broadcast_arrays(x_fraction, y_fraction)
    fx, dx, dxx = one_axis_basis(x.ravel(), plate.order_x, plate.boundary, clamped=True)
    fy, dy, dyy = one_axis_basis(y.ravel(), plate.order_y, plate.boundary)
    product = lambda a,b: np.einsum("ip,jp->ijp",a,b).reshape(plate.order_x*plate.order_y,-1).T
    return (product(fx,fy), product(dxx,fy)/plate.length_m**2,
            product(fx,dyy)/plate.width_m**2, product(dx,dy)/(plate.length_m*plate.width_m))


def assemble_plate(thickness_m, plate=Plate()):
    thickness = np.asarray(thickness_m, dtype=float)
    if thickness.shape != (2,2) or not np.all(np.isfinite(thickness)) or np.any(thickness <= 0):
        raise ValueError("[几何错误] 需2×2正壁厚矩阵。建议操作：按根部/顶部及左右排列，单位米。")
    ndof = plate.order_x*plate.order_y
    stiffness, mass = np.zeros((ndof,ndof)), np.zeros((ndof,ndof))
    pressure_load = np.zeros(ndof)
    gauss, weight = leggauss(plate.quadrature)
    for ix in range(2):
        for iy in range(2):
            xx, yy = np.meshgrid((gauss+1)/4+ix/2, (gauss+1)/4+iy/2, indexing="ij")
            weights = np.outer(weight,weight).ravel()*plate.length_m*plate.width_m/16
            shape, bxx, byy, bxy = basis(plate,xx,yy)
            rigidity = plate.young_pa*thickness[ix,iy]**3/(12*(1-plate.poisson**2))
            gram = lambda a,b: a.T @ (weights[:,None]*b)
            stiffness += rigidity*(gram(bxx,bxx)+gram(byy,byy)
                         + plate.poisson*(gram(bxx,byy)+gram(byy,bxx))
                         + 2*(1-plate.poisson)*gram(bxy,bxy))
            mass += plate.density_kg_m3*thickness[ix,iy]*gram(shape,shape)
            pressure_load += shape.T @ weights/(plate.length_m*plate.width_m)
    total_mass = plate.density_kg_m3*plate.length_m*plate.width_m*thickness.mean()
    return stiffness, mass, pressure_load, float(total_mass)


def service_loads(plate=Plate()):
    """Two unit-resultant normal loads: free-edge line, and half-edge line.

    These are illustrative service-rigidity constraints, not real product specs.
    Distributed pressure load is generated during assembly.
    """
    g, w = leggauss(plate.quadrature)
    full_edge = basis(plate,np.ones_like(g),(g+1)/2)[0].T @ (w/2)
    half_edge = basis(plate,np.ones_like(g),(g+1)/4)[0].T @ (w/2)
    return {"free_edge_uniform": full_edge, "free_edge_left_half": half_edge}


def solve_plate(thickness_m, plate=Plate(), count=6):
    stiffness, mass, pressure_load, total_mass = assemble_plate(thickness_m,plate)
    if not isinstance(count,int) or not 1 <= count <= len(stiffness):
        raise ValueError("[模态错误] 模态数超出Ritz空间。建议操作：检查保留模态数。")
    # Equalize generalized mass diagonals before eigenanalysis / static solves.
    scale = 1/np.sqrt(np.diag(mass))
    ks, ms = scale[:,None]*stiffness*scale, scale[:,None]*mass*scale
    values, vectors = eigh(ks,ms,subset_by_index=[0,count-1])
    modes = scale[:,None]*vectors
    loads = {"normal_pressure": pressure_load, **service_loads(plate)}
    compliances = {name: float((load*scale) @ np.linalg.solve(ks,load*scale)) for name,load in loads.items()}
    return {"omega": np.sqrt(values), "vectors": modes, "compliances": compliances,
            "mass_kg": total_mass, "stiffness": stiffness, "mass": mass}


def contact_modes(solution, point, plate=Plate(), damping_ratio=0.01):
    point = np.asarray(point,dtype=float)
    if point.shape != (2,) or not np.all(np.isfinite(point)) or not (0 < point[0] <= 1 and 0 <= point[1] <= 1):
        raise ValueError("[接触错误] 接触点须为合法板内相对坐标。建议操作：检查x/y范围。")
    load = basis(plate,*point)[0][0]
    return Modes(solution["omega"],load @ solution["vectors"],damping_ratio)
