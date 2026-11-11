"""Re-evaluate every design after resolving the known plate interface error."""

import argparse
import hashlib
import itertools
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from .mechanics import Cutting,averaged_boundary,periodic_boundary
from .plate_spline import SplinePlate,contact_modes,solve_plate

ROOT=Path(__file__).resolve().parent
SOURCES=("spline_config.json","spline_pilot.py","plate_spline.py","plate.py","mechanics.py")


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def load_config(output,create=False):
    if create:
        output.mkdir(parents=True,exist_ok=False)
        (output/"source").mkdir()
        for name in SOURCES: shutil.copy2(ROOT/name,output/"source"/name)
        write_json(output/"manifest.json",{"python":sys.version,"scope":"adaptive_exploratory_recheck",
            "source_sha256":{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in SOURCES}})
    manifest=json.loads((output/"manifest.json").read_text(encoding="utf-8"))
    for name,expected in manifest["source_sha256"].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise ValueError(f"[版本漂移] {name}已变更。建议操作：新建目录重跑。")
    return json.loads((output/"source/spline_config.json").read_text(encoding="utf-8"))


def candidates(config):
    t=config["thickness_mm"]
    values=np.arange(t["min"],t["max"]+t["step"]/2,t["step"])
    return [np.array(x).reshape(2,2)/1000 for x in itertools.product(values,repeat=4)
            if abs(sum(x)-4*t["mean"])<1e-8]


def scores(solution,plate,config,contacts,rpms):
    rows=[]
    for point in contacts:
        modes=contact_modes(solution,point,plate,config["damping_ratio"])
        for rpm in rpms:
            rows.append({"contact":point,"rpm":rpm,
                         "boundary_m":averaged_boundary(modes,rpm,Cutting(**config["cutting"]))})
    return rows


def select(records):
    feasible=[r for r in records if r["feasible"]]
    return {"uniform":min(feasible,key=lambda r:np.linalg.norm(np.array(r["thickness_mm"])-2)),
            "static_service":min(feasible,key=lambda r:r["service_minimax"]),
            "first_frequency":max(feasible,key=lambda r:r["first_frequency_hz"]),
            "worst_stability":max(feasible,key=lambda r:r["minimum_boundary_m"]),
            "mean_stability":max(feasible,key=lambda r:r["mean_boundary_m"])}


def screen(output):
    config=load_config(output,True)
    started=time.monotonic()
    levels=[]
    for segments in config["mesh_levels"]:
        plate=SplinePlate(**{**config["plate"],"segments_x":segments,"segments_y":segments})
        baseline=solve_plate(np.full((2,2),config["thickness_mm"]["mean"]/1000),plate,config["modes"])
        records=[]
        for index,thickness in enumerate(candidates(config)):
            solution=solve_plate(thickness,plate,config["modes"])
            ratios={key:solution["compliances"][key]/baseline["compliances"][key] for key in config["service_constraints"]}
            feasible=all(ratios[key]<=limit*(1+1e-8) for key,limit in config["service_constraints"].items())
            record={"design_id":index,"thickness_mm":(thickness*1000).tolist(),"mass_kg":solution["mass_kg"],
                    "service_ratios":ratios,"service_minimax":max(ratios.values()),"feasible":bool(feasible),
                    "first_frequency_hz":float(solution["omega"][0]/(2*np.pi))}
            if feasible:
                raw=scores(solution,plate,config,config["screen_contacts"],config["screen_rpms"])
                record.update(minimum_boundary_m=min(r["boundary_m"] for r in raw),
                              mean_boundary_m=float(np.mean([r["boundary_m"] for r in raw])),raw=raw)
            records.append(record)
            if time.monotonic()-started>config["budget_seconds_per_stage"]:
                write_json(output/"incomplete.json",{"segments":segments,"records":records,"elapsed":time.monotonic()-started})
                raise RuntimeError("[预算停止] 空间核查超出预算。建议操作：先检查成本，不报告部分搜索最优。")
        levels.append({"segments":segments,"records":records,"selected":select(records),
                       "feasible_count":sum(r["feasible"] for r in records)})
        write_json(output/"screening_progress.json",{"levels":levels,"elapsed_seconds":time.monotonic()-started})
        print(f"spline segments={segments} feasible={levels[-1]['feasible_count']} elapsed={time.monotonic()-started:.1f}s",flush=True)
    write_json(output/"screening.json",{"levels":levels,"selected":levels[-1]["selected"],
               "elapsed_seconds":time.monotonic()-started,"new_contribution_established":False})
    print(json.dumps({label:{k:v for k,v in row.items() if k!='raw'} for label,row in levels[-1]["selected"].items()},ensure_ascii=False),flush=True)


def verify(output):
    config=load_config(output)
    summary=json.loads((output/"screening.json").read_text(encoding="utf-8"))
    n=max(config["mesh_levels"])
    plate=SplinePlate(**{**config["plate"],"segments_x":n,"segments_y":n})
    started=time.monotonic()
    records,completed=[],{}
    for label,design in summary["selected"].items():
        if design["design_id"] in completed:
            records.extend({**r,"design":label,"reused_identical_profile":True} for r in completed[design["design_id"]])
            continue
        solution=solve_plate(np.array(design["thickness_mm"])/1000,plate,config["modes"])
        current=[]
        for point in config["verification_contacts"]:
            modes=contact_modes(solution,point,plate,config["damping_ratio"])
            for rpm in config["verification_rpms"]:
                for steps in config["floquet_steps"]:
                    depth=periodic_boundary(modes,rpm,steps,Cutting(**config["cutting"]))
                    current.append({"design":label,"contact":point,"rpm":rpm,"steps":steps,"boundary_m":depth})
                print(f"spline verify {label} {point} rpm={rpm} boundary_m={current[-1]['boundary_m']}",flush=True)
                if time.monotonic()-started>config["budget_seconds_per_stage"]:
                    write_json(output/"incomplete_verification.json",{"records":records+current})
                    raise RuntimeError("[预算停止] 周期核查超出预算。建议操作：检查成本后重跑。")
        completed[design["design_id"]]=current
        records.extend(current)
    write_json(output/"verification.json",{"periodic":records,"elapsed_seconds":time.monotonic()-started,
               "plate_physical_validation":False,"new_contribution_established":False})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage",choices=["screen","verify"])
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    {"screen":screen,"verify":verify}[args.stage](args.output.resolve())


if __name__=="__main__": main()
