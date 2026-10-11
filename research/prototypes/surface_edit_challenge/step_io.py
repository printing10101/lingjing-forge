"""Atlas STEP adapter; validates open face compounds, not sewn solids."""
from dataclasses import replace
from pathlib import Path

import numpy as np

from model import from_surface, to_surface
from kernel import (BRep_Builder, BRep_Tool, BRepBuilderAPI_MakeFace, BRepCheck_Analyzer,
                    IFSelect_RetDone, STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer,
                    TopAbs_FACE, TopExp_Explorer, TopoDS, TopoDS_Compound, gp_Vec, kernel_jet)
from OCP.gp import gp_Trsf


def global_surface(tile):
    surface = to_surface(tile.patch)
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(0, tile.y_origin, 0))
    surface.Transform(transform)
    return surface


def roundtrip(atlas, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    surfaces = [global_surface(t) for t in atlas.tiles]
    for surface in surfaces:
        maker = BRepBuilderAPI_MakeFace(surface, 1e-8)
        if not maker.IsDone():
            raise RuntimeError('[FACE_FAILED] OCCT could not build a face.')
        builder.Add(compound, maker.Face())
    writer = STEPControl_Writer()
    if not BRepCheck_Analyzer(compound).IsValid() or writer.Transfer(compound, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError('[STEP_TRANSFER_FAILED] Invalid input or failed transfer.')
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError('[STEP_WRITE_FAILED] Failed write.')
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or reader.TransferRoots() < 1:
        raise RuntimeError('[STEP_READ_FAILED] Failed read or transfer.')
    shape = reader.OneShape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise RuntimeError('[INVALID_READBACK] Read-back compound failed B-rep check.')
    explorer, imported = TopExp_Explorer(shape, TopAbs_FACE), []
    while explorer.More():
        imported.append(BRep_Tool.Surface_s(TopoDS.Face_s(explorer.Current())))
        explorer.Next()
    if len(imported) != len(surfaces):
        raise RuntimeError('[FACE_COUNT_CHANGED] Face identity cannot be matched.')
    matched = []
    errors = np.zeros(6)
    for surface, tile in zip(surfaces, atlas.tiles):
        center = tile.point(.5, .5)
        distances = [np.linalg.norm(np.array([s.Value(.5, .5).X(), s.Value(.5, .5).Y(), s.Value(.5, .5).Z()])-center)
                     for s in imported]
        selected = int(np.argmin(distances))
        if distances[selected] > 1e-7:
            raise RuntimeError('[FACE_MATCH_FAILED] STEP changed coordinates or parameters.')
        restored = imported.pop(selected)
        for u in np.linspace(0, 1, 7):
            for v in np.linspace(0, 1, 7):
                errors = np.maximum(errors, np.linalg.norm(kernel_jet(surface, u, v, tile.patch.length, tile.patch.width)-
                                      kernel_jet(restored, u, v, tile.patch.length, tile.patch.width), axis=1))
        local = restored.Copy()
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(0, -tile.y_origin, 0))
        local.Transform(transform)
        matched.append(replace(tile, patch=from_surface(local, tile.patch.length, tile.patch.width)))
    return replace(atlas, tiles=matched), dict(passed=bool(max(errors) < 1e-7),
        sampled_jet_errors=errors.tolist(), face_count=len(matched), step_bytes=path.stat().st_size,
        representation='open_face_compound_not_solid')
