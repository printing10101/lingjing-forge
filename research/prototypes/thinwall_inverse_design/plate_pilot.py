"""Second bounded exploration: four-patch plate with service rigidity constraints."""

import argparse
import csv
import hashlib
import itertools
import json
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy

from .mechanics import Cutting, averaged_boundary, periodic_boundary
from .plate import Plate, contact_modes, solve_plate

ROOT=Path(__file__).resolve().parent
SOURCES=("plate.py","plate_pilot.py","plate_config.json","mechanics.py")


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def frozen_config(output,create=False):
    if create:
        output.mkdir(parents=True,exist_ok=False)
        (output/"source").mkdir()
        for name in SOURCES: shutil.copy2(ROOT/name,output/"source"/name)
        write_json(output/"manifest.json",{"created_utc":datetime.now(timezone.utc).isoformat(),
                   "source_sha256":{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in SOURCES},
                   "python":sys.version,"numpy":np.__version__,"scipy":scipy.__version__,
                   "platform":platform.platform(),"status":"exploratory_no_physical_validation"})
    manifest=json.loads((output/"manifest.json").read_text(encoding="utf-8"))
    for name,expected in manifest["source_sha256"].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise ValueError(f"[版本漂移] {name}不匹配。建议操作：使用新目录重新运行。")
    return json.loads((output/"source/plate_config.json").read_text(encoding="utf-8"))


def thickness_grid(config):
    t=config["thickness_mm"]
    values=np.arange(t["min"],t["max"]+t["step"]/2,t["step"])
    return [np.array(x).reshape(2,2)/1000 for x in itertools.product(values,repeat=4)
            if abs(sum(x)-4*t["mean"])<1e-8]


def score_plate(solution,plate,config,contacts,rpms):
    rows=[]
    for point in contacts:
        modes=contact_modes(solution,point,plate,config["damping_ratio"])
        for rpm in rpms:
            depth=averaged_boundary(modes,rpm,Cutting(**config["cutting"]))
            rows.append({"contact":point,"rpm":rpm,"averaged_boundary_m":depth})
    return rows


def screen(output):
    config=frozen_config(output,create=True)
    plate=Plate(**config["plate"])
    baseline=solve_plate(np.full((2,2),config["thickness_mm"]["mean"]/1000),plate,config["modes"])
    start=time.monotonic()
    candidates,raw=[],[]
    for index,thickness in enumerate(thickness_grid(config)):
        solution=solve_plate(thickness,plate,config["modes"])
        ratios={k:solution["compliances"][k]/baseline["compliances"][k] for k in config["service_constraints"]}
        feasible=all(ratios[k]<=cap*(1+1e-8) for k,cap in config["service_constraints"].items())
        record={"design_id":index,"thickness_mm":(thickness*1000).tolist(),
                "mass_kg":solution["mass_kg"],"service_compliance_ratios":ratios,
                "service_minimax":max(ratios.values()),"feasible":bool(feasible),
                "first_frequency_hz":float(solution["omega"][0]/(2*np.pi))}
        if feasible:
            rows=score_plate(solution,plate,config,config["screen_contacts"],config["screen_rpms"])
            values=[row["averaged_boundary_m"] for row in rows]
            record.update(minimum_boundary_m=min(values),mean_boundary_m=float(np.mean(values)))
            raw.extend({"design_id":index,**row} for row in rows)
        candidates.append(record)
        if (index+1)%10==0:
            print(f"plate {index+1} feasible={sum(r['feasible'] for r in candidates)} elapsed={time.monotonic()-start:.1f}s",flush=True)
        if time.monotonic()-start>240:
            write_json(output/"incomplete_screening.json",{"reason":"240 second budget","all_designs":candidates,"raw":raw})
            raise RuntimeError("[预算停止] 筛选超过240秒。建议操作：先检查计算成本，勿把部分搜索写成全部最优。")
    feasible=[r for r in candidates if r["feasible"]]
    uniform=min(feasible,key=lambda r:np.linalg.norm(np.array(r["thickness_mm"])-2))
    chosen={"uniform":uniform,"static_service":min(feasible,key=lambda r:r["service_minimax"]),
            "first_frequency":max(feasible,key=lambda r:r["first_frequency_hz"]),
            "worst_stability":max(feasible,key=lambda r:r["minimum_boundary_m"]),
            "mean_stability":max(feasible,key=lambda r:r["mean_boundary_m"])}
    write_json(output/"screening.json",{"all_designs":candidates,"selected":chosen,"raw":raw,
               "design_count":len(candidates),"feasible_count":len(feasible),"scenario_count":len(raw),
               "elapsed_seconds":time.monotonic()-start,"primary_claim_established":False})
    with (output/"designs.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list({key for row in candidates for key in row}))
        writer.writeheader()
        writer.writerows(candidates)
    print(json.dumps(chosen,ensure_ascii=False),flush=True)


def verify(output):
    config=frozen_config(output)
    chosen=json.loads((output/"screening.json").read_text(encoding="utf-8"))["selected"]
    start=time.monotonic()
    convergence,off_grid,periodic=[],[],[]
    completed={}
    for label,record in chosen.items():
        if record["design_id"] in completed:
            convergence.extend({**r,"design":label,"reused_identical_profile":True} for r in completed[record["design_id"]][0])
            off_grid.extend({**r,"design":label,"reused_identical_profile":True} for r in completed[record["design_id"]][1])
            periodic.extend({**r,"design":label,"reused_identical_profile":True} for r in completed[record["design_id"]][2])
            continue
        thickness=np.array(record["thickness_mm"])/1000
        this_convergence,this_off_grid,this_periodic=[],[],[]
        for order_x,order_y in config["checks"]["ritz_orders"]:
            kwargs={**config["plate"],"order_x":order_x,"order_y":order_y,"quadrature":max(16,order_x+2)}
            plate=Plate(**kwargs)
            solution=solve_plate(thickness,plate,config["modes"])
            reference=solve_plate(np.full((2,2),0.002),plate,config["modes"])
            ratios={key:solution["compliances"][key]/reference["compliances"][key] for key in config["service_constraints"]}
            scores=score_plate(solution,plate,config,config["screen_contacts"],config["screen_rpms"])
            this_convergence.append({"design":label,"order":[order_x,order_y],"quadrature":kwargs["quadrature"],
                                     "service_ratios":ratios,"feasible":all(ratios[k]<=cap*(1+1e-8) for k,cap in config["service_constraints"].items()),
                                     "first_frequency_hz":float(solution["omega"][0]/(2*np.pi)),
                                     "worst_averaged_boundary_m":min(r["averaged_boundary_m"] for r in scores)})
        for count in config["checks"]["modes"]:
            plate=Plate(**{**config["plate"],"order_x":10,"order_y":9,"quadrature":16})
            solution=solve_plate(thickness,plate,count)
            scores=score_plate(solution,plate,config,config["verification_contacts"],config["verification_rpms"])
            this_off_grid.extend({"design":label,"modes":count,**r} for r in scores)
        # Actual interrupted model on the finest spatial basis; finite modal truncation.
        solution=solve_plate(thickness,plate,config["modes"])
        for point in config["verification_contacts"]:
            modes=contact_modes(solution,point,plate,config["damping_ratio"])
            for rpm in config["verification_rpms"]:
                for steps in config["checks"]["floquet_steps"]:
                    boundary=periodic_boundary(modes,rpm,steps,Cutting(**config["cutting"]))
                    row={"design":label,"contact":point,"rpm":rpm,"steps":steps,"boundary_m":boundary}
                    this_periodic.append(row)
                print(f"verified {label} point={point} rpm={rpm} depth_m={this_periodic[-1]['boundary_m']}",flush=True)
        completed[record["design_id"]]=(this_convergence,this_off_grid,this_periodic)
        convergence.extend(this_convergence)
        off_grid.extend(this_off_grid)
        periodic.extend(this_periodic)
        write_json(output/"verification_progress.json",{"completed_design":label,"elapsed_seconds":time.monotonic()-start})
    write_json(output/"verification.json",{"convergence":convergence,"off_grid":off_grid,"periodic":periodic,
               "elapsed_seconds":time.monotonic()-start,"physical_validation":False,"new_contribution_established":False})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage",choices=["screen","verify"])
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    {"screen":screen,"verify":verify}[args.stage](args.output.resolve())


if __name__=="__main__": main()
