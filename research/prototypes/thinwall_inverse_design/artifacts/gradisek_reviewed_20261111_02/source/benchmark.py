"""Evaluate a fixed published-parameter milling model on existing digitized labels."""

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np

from .coupled_milling import CoupledModes,Milling,coupled_radius

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[2]


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def confusion(rows,key):
    counts={"stable_as_stable":0,"stable_as_chatter":0,"chatter_as_stable":0,"chatter_as_chatter":0}
    for row in rows:
        truth,prediction=row["stable_true"],row[key]
        counts[("stable" if truth else "chatter")+"_as_"+("stable" if prediction else "chatter")]+=1
    correct=counts["stable_as_stable"]+counts["chatter_as_chatter"]
    recalls=[]
    for prefix in ("stable","chatter"):
        total=counts[prefix+"_as_stable"]+counts[prefix+"_as_chatter"]
        if total: recalls.append(counts[prefix+"_as_"+prefix]/total)
    return {"n":len(rows),"accuracy":correct/len(rows) if rows else None,
            "balanced_accuracy":float(np.mean(recalls)) if recalls else None,"counts":counts}


def run(output):
    output.mkdir(parents=True,exist_ok=False)
    config=json.loads((ROOT/"benchmark_config.json").read_text(encoding="utf-8"))
    (output/"source").mkdir()
    for name in ("coupled_milling.py","benchmark.py","benchmark_config.json","mechanics.py"):
        shutil.copy2(ROOT/name,output/"source"/name)
    dataset=REPO/config["source_csv"]
    pdf=REPO/config["source_local_pdf"]
    rows=list(csv.DictReader(dataset.open(encoding="utf-8-sig")))
    expected=config.get("expected_reference_sha256")
    if expected and hashlib.sha256(dataset.read_bytes()).hexdigest()!=expected:
        raise ValueError("[来源漂移] 审阅数据哈希不匹配。建议操作：核对审阅导出，勿换用未核验标签。")
    write_json(output/"manifest.json",{"python":sys.version,"source_doi":config["source_doi"],
        "dataset_sha256":hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "pdf_sha256":hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "code_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (output/"source").iterdir()},
        "parameters_fitted":False,"digitization_independently_repeated":False,
        "reference_status":config.get("reference_status","unverified_digitized_labels"),
        "population_experimental_accuracy_supported":False})
    mass=np.array(config["mass_kg"])
    stiffness=np.array(config["stiffness_n_m"])
    damping=np.array(config["damping_n_s_m"])/(2*np.sqrt(mass*stiffness))
    modes=CoupledModes(np.sqrt(stiffness/mass),damping,np.diag(1/np.sqrt(mass)))
    milling=Milling(**config["milling"])
    start=time.monotonic()
    output_rows=[]
    for index,row in enumerate(rows):
        if row["result"] not in ("stable","chatter") or row["fig"]!="Fig7":
            raise ValueError(f"[数据错误] 第{index+1}行标签或图号未知。建议操作：核对原图。")
        tool=replace(milling,immersion=float(row["ae_mm"])/1000/milling.diameter_m)
        record={"row":index,"source_figure":row["fig"],"ae_label":row["ae_label"],
                "rpm":float(row["n_rpm"]),"depth_mm":float(row["ap_mm"]),
                "review":row.get("review",""),"stable_true":row["result"]=="stable"}
        for steps in config["steps"]:
            radius=coupled_radius(modes,record["rpm"],record["depth_mm"]/1000,steps,tool,config["delay_rule"])
            record[f"rho_{steps}"]=radius
            record[f"stable_{steps}"]=radius<1
        output_rows.append(record)
        if (index+1)%100==0: print(f"external {index+1}/{len(rows)} elapsed={time.monotonic()-start:.1f}s",flush=True)
        if time.monotonic()-start>config["budget_seconds"]:
            write_json(output/"incomplete.json",{"rows":output_rows,"reason":"budget exhausted"})
            raise RuntimeError("[预算停止] 外部核对达到预算。建议操作：优化计算后用新目录重跑。")
    finest=f"stable_{config['steps'][-1]}"
    groups={label:[r for r in output_rows if r["ae_label"]==label] for label in sorted({r["ae_label"] for r in output_rows})}
    result={"all_rows":confusion(output_rows,finest),
            "by_immersion":{label:confusion(group,finest) for label,group in groups.items()},
            "excluding_CHECK":confusion([r for r in output_rows if r["review"]!="CHECK"],finest),
            "by_steps":{steps:confusion(output_rows,f"stable_{steps}") for steps in config["steps"]},
            "numerical_label_changes":sum(r[f"stable_{config['steps'][0]}"]!=r[finest] for r in output_rows),
            "review_flags":dict(Counter(r["review"] for r in output_rows)),
            "rows":output_rows,"elapsed_seconds":time.monotonic()-start,
            "scope":"Published stationary tool model vs existing figure-digitized labels; not plate design validation",
            "new_experimental_data":False,"thinwall_design_validation":False}
    result["reference_status"]=config.get("reference_status","unverified_digitized_labels")
    result["metric_definition"]="Agreement with the provided reference labels only; not a population experimental accuracy estimate"
    result["population_experimental_accuracy_supported"]=False
    write_json(output/"results.json",result)
    with (output/"predictions.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    print(json.dumps({k:v for k,v in result.items() if k!="rows"},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    run(args.output.resolve())


if __name__=="__main__": main()
