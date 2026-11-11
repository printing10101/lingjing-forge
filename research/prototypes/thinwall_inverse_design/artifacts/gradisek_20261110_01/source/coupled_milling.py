"""Two-direction regenerative milling model for external published benchmarks.

Force projection follows Gradisek et al. (2005), Eqs. (2)-(7), DOI
10.1016/j.ijmachtools.2004.11.015. Identical teeth, constant speed; modal damping
is per mode. This implementation does not calibrate parameters to test labels.
"""

from dataclasses import dataclass

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.linalg import expm

from .mechanics import positive


@dataclass(frozen=True)
class CoupledModes:
    omega: np.ndarray
    damping: np.ndarray
    participation: np.ndarray

    def __post_init__(self):
        w,z,v=map(np.asarray,(self.omega,self.damping,self.participation))
        if w.ndim!=1 or not w.size or z.shape!=w.shape or v.shape!=(2,len(w)):
            raise ValueError("[模态错误] 需要频率/阻尼向量及2×r参与矩阵。建议操作：核对输入维度。")
        if not (np.all(np.isfinite(w)) and np.all(w>0) and np.all(np.isfinite(z))
                and np.all((z>0)&(z<1)) and np.all(np.isfinite(v))):
            raise ValueError("[模态错误] 数据含非物理值。建议操作：检查模态参数及单位。")


@dataclass(frozen=True)
class Milling:
    tangential_pa: float=644e6
    radial_ratio: float=0.37
    teeth: int=1
    diameter_m: float=0.008
    immersion: float=0.5
    helix_deg: float=45.0

    def __post_init__(self):
        positive(self.tangential_pa,"tangential_pa")
        positive(self.diameter_m,"diameter_m")
        if (not isinstance(self.teeth,int) or self.teeth<1 or not 0<self.immersion<=1
            or not np.isfinite(self.radial_ratio) or self.radial_ratio<0
            or not np.isfinite(self.helix_deg) or not 0<=self.helix_deg<80):
            raise ValueError("[切削错误] 方向/刀具配置非法。建议操作：核对浸入比、齿数和螺旋角。")

    def directional(self,phase,depth_m=0.0):
        phase=np.asarray(phase)
        z,w=leggauss(4)
        lag=(z+1)*depth_m*np.tan(np.deg2rad(self.helix_deg))/self.diameter_m
        phi=np.mod(phase[...,None,None]+2*np.pi*np.arange(self.teeth)[:,None]/self.teeth-lag,2*np.pi)
        sine,cosine=np.sin(phi),np.cos(phi)
        engaged=phi<np.arccos(1-2*self.immersion)
        weights=engaged*w/2
        hxx=np.sum(weights*(-sine*cosine-self.radial_ratio*sine**2),axis=(-2,-1))
        hxy=np.sum(weights*(-cosine**2-self.radial_ratio*sine*cosine),axis=(-2,-1))
        hyx=np.sum(weights*(sine**2-self.radial_ratio*sine*cosine),axis=(-2,-1))
        hyy=np.sum(weights*(sine*cosine-self.radial_ratio*cosine**2),axis=(-2,-1))
        return np.stack([np.stack([hxx,hxy],axis=-1),np.stack([hyx,hyy],axis=-1)],axis=-2)


def coupled_radius(modes,rpm,depth_m,steps=80,milling=Milling()):
    positive(rpm,"rpm")
    if not np.isfinite(depth_m) or depth_m<0 or not isinstance(steps,int) or steps<8:
        raise ValueError("[求解错误] 切深/步数非法。建议操作：使用非负切深、至少8步。")
    w,z,v=map(np.asarray,(modes.omega,modes.damping,modes.participation))
    tau=60/(rpm*milling.teeth)
    if depth_m==0: return float(np.exp(-np.min(w*z)*tau))
    active=np.flatnonzero(np.linalg.norm(v,axis=1)>0)
    if not len(active): return float(np.exp(-np.min(w*z)*tau))
    projection=v[active]
    count,channels=len(w),len(active)
    dynamic,history=2*count,steps*channels
    dt=tau/steps
    transition=np.eye(dynamic+history)
    phases=(np.arange(steps)+0.5)*2*np.pi/(milling.teeth*steps)
    matrices=milling.directional(phases,depth_m)
    for coefficient in matrices:
        gain=depth_m*milling.tangential_pa*(v.T @ coefficient[:,active])
        current=np.block([[np.zeros((count,count)),np.eye(count)],
                          [-np.diag(w**2)+gain @ projection,-np.diag(2*z*w)]])
        delayed=np.vstack([np.zeros((count,channels)),-gain])
        augmented=np.zeros((dynamic+channels,dynamic+channels))
        augmented[:dynamic,:dynamic]=current
        augmented[:dynamic,dynamic:]=delayed
        exponential=expm(augmented*dt)
        updated=np.empty_like(transition)
        updated[:dynamic]=exponential[:dynamic,:dynamic] @ transition[:dynamic]
        updated[:dynamic]+=exponential[:dynamic,dynamic:] @ transition[-channels:]
        updated[dynamic:dynamic+channels]=projection @ transition[:count]
        updated[dynamic+channels:]=transition[dynamic:-channels]
        transition=updated
    return float(np.abs(np.linalg.eigvals(transition)).max())
