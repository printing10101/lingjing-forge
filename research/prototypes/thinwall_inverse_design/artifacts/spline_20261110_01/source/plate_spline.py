"""C1 bicubic tensor B-spline Kirchhoff plate with thickness interfaces at knots.

An independent spatial approximation to the global-polynomial Ritz pilot. This
allows curvature jumps at the four-patch interfaces. It is still a thin-plate
linear bending model, not measured truth or a full eccentric shell model.
"""

from dataclasses import dataclass

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.interpolate import BSpline
from scipy.linalg import eigh

from .mechanics import Modes
from .plate import Plate


@dataclass(frozen=True)
class SplinePlate:
    length_m: float=0.08
    width_m: float=0.06
    young_pa: float=70e9
    density_kg_m3: float=2700.0
    poisson: float=0.33
    segments_x: int=4
    segments_y: int=4
    quadrature: int=5

    def __post_init__(self):
        Plate(self.length_m,self.width_m,self.young_pa,self.density_kg_m3,self.poisson,
              order_x=1,order_y=1,quadrature=self.quadrature)
        if (any(not isinstance(n,int) or n<2 or n%2 for n in [self.segments_x,self.segments_y])
                or self.quadrature<4):
            raise ValueError("[样条错误] 分段数须为正偶数且积分阶数至少4。建议操作：使用4、6、8分段。")


def axis_basis(coordinate,segments,clamped=False):
    knots=np.r_[np.zeros(4),np.repeat(np.arange(1,segments)/segments,2),np.ones(4)]
    function=BSpline(knots,np.eye(len(knots)-4),3,extrapolate=False)
    start=2 if clamped else 0
    return tuple(function(np.atleast_1d(coordinate),nu=degree)[:,start:] for degree in range(3))


def basis(plate,x,y):
    x,y=np.broadcast_arrays(x,y)
    bx,dx,dxx=axis_basis(x.ravel(),plate.segments_x,True)
    by,dy,dyy=axis_basis(y.ravel(),plate.segments_y)
    product=lambda a,b:np.einsum("pi,pj->pij",a,b).reshape(len(x.ravel()),-1)
    return (product(bx,by),product(dxx,by)/plate.length_m**2,
            product(bx,dyy)/plate.width_m**2,product(dx,dy)/(plate.length_m*plate.width_m))


def assemble_plate(thickness_m,plate=SplinePlate()):
    thickness=np.asarray(thickness_m,dtype=float)
    if thickness.shape!=(2,2) or not np.all(np.isfinite(thickness)) or np.any(thickness<=0):
        raise ValueError("[几何错误] 需要2×2正壁厚矩阵。建议操作：检查米制壁厚。")
    ndof=2*plate.segments_x*(2*plate.segments_y+2)
    stiffness,mass=np.zeros((ndof,ndof)),np.zeros((ndof,ndof))
    pressure=np.zeros(ndof)
    g,w=leggauss(plate.quadrature)
    for ix in range(plate.segments_x):
        for iy in range(plate.segments_y):
            x,y=np.meshgrid((ix+(g+1)/2)/plate.segments_x,
                            (iy+(g+1)/2)/plate.segments_y,indexing="ij")
            weights=np.outer(w,w).ravel()*plate.length_m*plate.width_m/(4*plate.segments_x*plate.segments_y)
            shape,bxx,byy,bxy=basis(plate,x,y)
            t=thickness[2*ix//plate.segments_x,2*iy//plate.segments_y]
            rigidity=plate.young_pa*t**3/(12*(1-plate.poisson**2))
            gram=lambda a,b:a.T @ (weights[:,None]*b)
            stiffness+=rigidity*(gram(bxx,bxx)+gram(byy,byy)+plate.poisson*(gram(bxx,byy)+gram(byy,bxx))
                                +2*(1-plate.poisson)*gram(bxy,bxy))
            mass+=plate.density_kg_m3*t*gram(shape,shape)
            pressure+=shape.T @ weights/(plate.length_m*plate.width_m)
    total_mass=plate.density_kg_m3*plate.length_m*plate.width_m*thickness.mean()
    return stiffness,mass,pressure,float(total_mass)


def edge_load(plate,left_half=False):
    result=np.zeros(2*plate.segments_x*(2*plate.segments_y+2))
    g,w=leggauss(plate.quadrature)
    intervals=plate.segments_y//2 if left_half else plate.segments_y
    density=2 if left_half else 1
    for iy in range(intervals):
        y=(iy+(g+1)/2)/plate.segments_y
        result+=basis(plate,np.ones_like(y),y)[0].T @ (w*density/(2*plate.segments_y))
    return result


def solve_plate(thickness_m,plate=SplinePlate(),count=6):
    stiffness,mass,pressure,total_mass=assemble_plate(thickness_m,plate)
    if not isinstance(count,int) or not 1<=count<=len(stiffness):
        raise ValueError("[模态错误] 保留模态数越界。建议操作：检查自由度数。")
    values,vectors=eigh(stiffness,mass,subset_by_index=[0,count-1])
    loads={"normal_pressure":pressure,"free_edge_uniform":edge_load(plate),
           "free_edge_left_half":edge_load(plate,True)}
    compliances={name:float(load @ np.linalg.solve(stiffness,load)) for name,load in loads.items()}
    return {"omega":np.sqrt(values),"vectors":vectors,"compliances":compliances,
            "mass_kg":total_mass,"stiffness":stiffness,"mass":mass}


def contact_modes(solution,point,plate=SplinePlate(),damping_ratio=0.01):
    point=np.asarray(point,dtype=float)
    if point.shape!=(2,) or not np.all(np.isfinite(point)) or not (0<point[0]<=1 and 0<=point[1]<=1):
        raise ValueError("[接触错误] 接触点坐标越界。建议操作：检查板内相对位置。")
    projection=basis(plate,*point)[0][0] @ solution["vectors"]
    return Modes(solution["omega"],projection,damping_ratio)
