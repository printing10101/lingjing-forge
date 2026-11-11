"""Reuse the existing three-face STEP adapter; output remains an open compound."""
from pathlib import Path
import sys
import importlib.util
import numpy as np
from numpy.polynomial import Polynomial
from math import comb

PILOT=Path(__file__).resolve().parents[1]/'surface_edit_pilot'


def load_legacy(name,filename):
    if name in sys.modules:return sys.modules[name]
    spec=importlib.util.spec_from_file_location(name,PILOT/filename)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    try:spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name,None)
        raise
    return module


legacy_geometry=load_legacy('_contact_legacy_geometry','geometry.py')
# The old kernel imports a bare `geometry`. Bind only during loading and restore
# immediately: do not put its directory before this prototype on sys.path.
previous_geometry=sys.modules.get('geometry')
try:
    sys.modules['geometry']=legacy_geometry
    legacy_kernel=load_legacy('_contact_legacy_kernel','kernel.py')
finally:
    if previous_geometry is None:sys.modules.pop('geometry',None)
    else:sys.modules['geometry']=previous_geometry
Patch,Contract,audit_edit=legacy_geometry.Patch,legacy_geometry.Contract,legacy_geometry.audit_edit
roundtrip=legacy_kernel.roundtrip
from repair import initial_curve
from verification import verify_ball


def graph_patch(curve,width=10.):
    a,b=curve.t[0],curve.t[-1]
    greville=np.array([np.mean(curve.t[i+1:i+curve.k+1]) for i in range(len(curve.c))])
    poles=np.zeros((len(curve.c),4,3))
    poles[:,:,0]=greville[:,None]
    poles[:,:,1]=np.linspace(0,width,4)[None,:]
    poles[:,:,2]=curve.c[:,None]
    return Patch(poles,(curve.t-a)/(b-a),np.array([0.]*4+[1.]*4),curve.k,3,b-a,width)


def side_curve(a,b):
    from scipy.interpolate import BSpline
    x=Polynomial([a,b-a]);poly=.1*x*x-.003*x**4
    controls=[sum(poly.coef[j]*comb(i,j)/comb(5,j) for j in range(min(i,4)+1)) for i in range(6)]
    return BSpline(np.array([a]*6+[b]*6),np.array(controls),5)


def export_and_reverify(candidate,request,path):
    path=Path(path)
    if path.exists():
        raise FileExistsError('[FROZEN_STEP] Refusing overwrite. 建议操作：选择新文件名。')
    if candidate.t[0]!=-4 or candidate.t[-1]!=4 or request.lock_order!=2:
        raise ValueError('[STEP_SCOPE] Adapter requires unscaled C2 locked example. 建议操作：选择支持的案例。')
    original=[graph_patch(side_curve(-12,-4)),graph_patch(initial_curve()),graph_patch(side_curve(4,12))]
    edited=[original[0],graph_patch(candidate),original[2]]
    restored,step=roundtrip(edited,path)
    audit=audit_edit(original,restored,Contract(0.,2,True,False,pin_center=True),(1,))
    middle=restored[1]
    # Check every v control curve. Nonnegative tensor-product v weights make their
    # common obstacle upper bound sufficient for the extruded graph surface.
    checks=[vars(verify_ball(middle.u_knots*8-4,middle.poles[:,j,2],middle.u_degree,
                            request.radius,request.tolerance)) for j in range(4)]
    if not audit['passed'] or any(c['status']!='pass' for c in checks):
        raise RuntimeError('[READBACK_REJECTED] Independent checks rejected exported geometry. 建议操作：保留失败证据并检查导出误差。')
    return dict(step=step,design_audit=audit,contact_after_readback=checks,
                scope='fixed_ball_section_and_extruded_graph_only_not_full_machining')
