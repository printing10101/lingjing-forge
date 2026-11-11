"""Export only explicitly reviewed figure labels; retain raw source unchanged.

Human/source-raster review is an input, not inferred from model agreement. The
export remains a single-rater, image-selected subset, not a population benchmark.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


LABELS={"stable","chatter","ambiguous","boundary"}


def reviewed_rows(source_path,review_path):
    source_path,review_path=Path(source_path),Path(review_path)
    review=json.loads(review_path.read_text(encoding="utf-8"))
    actual=hashlib.sha256(source_path.read_bytes()).hexdigest()
    if review.get("source_csv_sha256")!=actual:
        raise ValueError("[来源漂移] 原CSV哈希与审阅记录不一致。建议操作：核对原文件并重新审阅。")
    with source_path.open(encoding="utf-8-sig",newline="") as stream:
        source=list(csv.DictReader(stream))
    seen=set()
    exported=[]
    excluded=[]
    for annotation in review["annotations"]:
        index=annotation["row"]
        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(source) or index in seen:
            raise ValueError("[审阅错误] 行号越界或重复。建议操作：检查原始行号。")
        seen.add(index)
        label=annotation.get("visual_label")
        if label not in LABELS or not str(annotation.get("reason","")).strip():
            raise ValueError("[审阅错误] 标签或理由未完成。建议操作：保留不确定标签，勿自动补真值。")
        original=source[index]
        if original["result"]!=annotation["old_label"]:
            raise ValueError("[审阅错误] 旧标签与来源不符。建议操作：检查审阅文件的版本。")
        for coordinate,key in (("px","px"),("py","py")):
            if abs(float(original[key])-float(annotation[coordinate]))>0.51:
                raise ValueError("[坐标错误] 审阅位置与来源不符。建议操作：重新定位原图标记。")
        if label in ("ambiguous","boundary"):
            excluded.append({"row":index,"label":label,"reason":annotation["reason"]})
            continue
        # Numerical inputs and original coordinates are copied, never optimized.
        exported.append({**original,"original_row":index,"old_label":original["result"],
                         "result":label,"review":"VISUAL_SINGLE_RATER",
                         "review_reason":annotation["reason"],
                         "source_doi":"10.1016/j.ijmachtools.2004.11.015"})
    summary={"source_csv_sha256":actual,
             "review_sha256":hashlib.sha256(review_path.read_bytes()).hexdigest(),
             "review_revision":review.get("revision","unspecified"),
             "raster_sha256":review.get("raster_sha256"),
             "source_total_rows":len(source),"reviewed_rows":len(seen),
             "exported_rows":len(exported),"excluded":excluded,
             "definite_label_changes":sum(r["result"]!=r["old_label"] for r in exported),
             "label_counts":dict(Counter(r["result"] for r in exported)),
             "reference_status":"visually_reviewed_single_rater_subset",
             "full_set_experimental_accuracy_supported":False,
             "source_doi":"10.1016/j.ijmachtools.2004.11.015",
             "limitations":"Stratified32-row audit, ambiguous/boundary markers excluded; same assistant rater, some prior exposure. No independent redigitization or population accuracy claim."}
    return exported,summary


def export_review(source_path,review_path,output):
    output=Path(output)
    rows,summary=reviewed_rows(source_path,review_path)
    if not rows:
        raise ValueError("[审阅错误] 无可导出标签。建议操作：先完成可辨标记的审阅。")
    output.mkdir(parents=True,exist_ok=False)
    with (output/"reviewed_points.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary["export_csv_sha256"]=hashlib.sha256((output/"reviewed_points.csv").read_bytes()).hexdigest()
    (output/"manifest.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--review",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(export_review(args.source,args.review,args.output),ensure_ascii=False))


if __name__=="__main__": main()
