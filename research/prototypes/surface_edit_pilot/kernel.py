"""OCP boundary: actual shape-preserving refinement and STEP round trips."""
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.Geom import Geom_BSplineSurface
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.TColgp import TColgp_Array2OfPnt
from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Pnt, gp_Vec

from geometry import Patch


def _knot_arrays(expanded):
    unique, counts = np.unique(expanded, return_counts=True)
    knots, mults = TColStd_Array1OfReal(1, len(unique)), TColStd_Array1OfInteger(1, len(unique))
    for i, (value, count) in enumerate(zip(unique, counts), 1):
        knots.SetValue(i, float(value))
        mults.SetValue(i, int(count))
    return knots, mults


def to_surface(patch):
    nu, nv = patch.poles.shape[:2]
    poles = TColgp_Array2OfPnt(1, nu, 1, nv)
    for i in range(nu):
        for j in range(nv):
            poles.SetValue(i + 1, j + 1, gp_Pnt(*map(float, patch.poles[i, j])))
    uk, um = _knot_arrays(patch.u_knots)
    vk, vm = _knot_arrays(patch.v_knots)
    return Geom_BSplineSurface(poles, uk, vk, um, vm, patch.u_degree, patch.v_degree)


def from_surface(surface, length, width):
    if not isinstance(surface, Geom_BSplineSurface):
        raise ValueError('[UNSUPPORTED_STEP_SURFACE] Expected B-spline surface on round trip.')
    if surface.IsURational() or surface.IsVRational():
        raise ValueError('[UNSUPPORTED_WEIGHTS] This pilot supports unit-weight surfaces only.')
    poles = np.array([[[surface.Pole(i, j).X(), surface.Pole(i, j).Y(), surface.Pole(i, j).Z()]
                       for j in range(1, surface.NbVPoles() + 1)]
                      for i in range(1, surface.NbUPoles() + 1)])
    uk = np.repeat([surface.UKnot(i) for i in range(1, surface.NbUKnots() + 1)],
                   [surface.UMultiplicity(i) for i in range(1, surface.NbUKnots() + 1)])
    vk = np.repeat([surface.VKnot(i) for i in range(1, surface.NbVKnots() + 1)],
                   [surface.VMultiplicity(i) for i in range(1, surface.NbVKnots() + 1)])
    return Patch(poles, uk, vk, surface.UDegree(), surface.VDegree(), length, width)


def refine(patch, action):
    surface = to_surface(patch)
    if action == 'insert_knots':
        for knot in (.25, .5, .75):
            surface.InsertUKnot(knot, 1, 1e-12, False)
    elif action == 'elevate_degree':
        surface.IncreaseDegree(6, 3)
    else:
        raise ValueError('[UNKNOWN_REFINEMENT] Choose insert_knots or elevate_degree.')
    return from_surface(surface, patch.length, patch.width)


def kernel_jet(surface, u, v, length, width):
    point = gp_Pnt()
    du, dv, duu, dvv, duv = [gp_Vec() for _ in range(5)]
    surface.D2(float(u), float(v), point, du, dv, duu, dvv, duv)
    return np.array([[point.X(), point.Y(), point.Z()],
                     [du.X()/length, du.Y()/length, du.Z()/length],
                     [dv.X()/width, dv.Y()/width, dv.Z()/width],
                     [duu.X()/length**2, duu.Y()/length**2, duu.Z()/length**2],
                     [dvv.X()/width**2, dvv.Y()/width**2, dvv.Z()/width**2],
                     [duv.X()/(length*width), duv.Y()/(length*width), duv.Z()/(length*width)]])


def roundtrip(patches, path):
    """Export an open three-face compound, not a solid or a sewn shell.

    Face identity is matched by centroid x, not reader enumeration order.
    Verify reader status, topology validity and OCCT D2 on a sampled grid.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    surfaces = [to_surface(p) for p in patches]
    for surface in surfaces:
        maker = BRepBuilderAPI_MakeFace(surface, 1e-8)
        if not maker.IsDone():
            raise RuntimeError('[FACE_BUILD_FAILED] OCCT failed to build a surface face.')
        builder.Add(compound, maker.Face())
    if not BRepCheck_Analyzer(compound).IsValid():
        raise RuntimeError('[INVALID_BREP] Input compound is invalid.')
    writer = STEPControl_Writer()
    if writer.Transfer(compound, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError('[STEP_TRANSFER_FAILED] Transfer did not complete.')
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError('[STEP_WRITE_FAILED] Write did not complete.')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or reader.TransferRoots() < 1:
        raise RuntimeError('[STEP_READ_FAILED] Read or transfer did not complete.')
    shape = reader.OneShape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise RuntimeError('[INVALID_ROUNDTRIP] Read-back B-rep is invalid.')
    explorer, imported = TopExp_Explorer(shape, TopAbs_FACE), []
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        imported.append(BRep_Tool.Surface_s(face))
        explorer.Next()
    if len(imported) != 3:
        raise RuntimeError('[STEP_FACE_COUNT] Expected three read-back faces.')
    imported.sort(key=lambda s: s.Value(.5, .5).X())
    errors = np.zeros(6)
    for original, restored, patch in zip(surfaces, imported, patches):
        for u in np.linspace(0, 1, 9):
            for v in np.linspace(0, 1, 7):
                errors = np.maximum(errors, np.linalg.norm(
                    kernel_jet(original, u, v, patch.length, patch.width) -
                    kernel_jet(restored, u, v, patch.length, patch.width), axis=1))
    restored = [from_surface(s, p.length, p.width) for s, p in zip(imported, patches)]
    return restored, {'passed': bool(np.max(errors) < 1e-7), 'face_count': len(imported),
                      'sampled_jet_errors': errors.tolist(), 'step_bytes': path.stat().st_size,
                      'representation': 'open_face_compound_not_solid', 'path': str(path)}
