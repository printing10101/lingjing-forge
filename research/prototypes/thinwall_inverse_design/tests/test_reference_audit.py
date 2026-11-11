import hashlib,json

import pytest

from prototypes.thinwall_inverse_design import reference_audit


def inputs(tmp_path):
    source=tmp_path/"source.csv"
    source.write_text("fig,ae_label,ae_mm,n_rpm,ap_mm,result,review,margin,px,py\n"
                      "Fig7,D,8,14000,0.4,stable,,0.9,163,198\n"
                      "Fig7,D,8,18000,0.05,stable,,0.9,249,233\n",encoding="utf-8")
    review=tmp_path/"review.json"
    value={"source_csv_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
           "annotations":[{"row":0,"visual_label":"chatter","old_label":"stable","reason":"clear cross","px":163,"py":198},
                          {"row":1,"visual_label":"ambiguous","old_label":"stable","reason":"curve overlap","px":249,"py":233}]}
    review.write_text(json.dumps(value),encoding="utf-8")
    return source,review,value


def test_review_export_preserves_input_and_rejects_ambiguous_as_truth(tmp_path):
    source,review,value=inputs(tmp_path)
    before=source.read_bytes()
    summary=reference_audit.export_review(source,review,tmp_path/"output")
    assert summary["exported_rows"]==1 and summary["definite_label_changes"]==1
    assert summary["full_set_experimental_accuracy_supported"] is False
    assert summary["review_sha256"]==hashlib.sha256(review.read_bytes()).hexdigest()
    assert source.read_bytes()==before
    assert "chatter" in (tmp_path/"output/reviewed_points.csv").read_text(encoding="utf-8")
    assert len(summary["excluded"])==1
    with pytest.raises(FileExistsError): reference_audit.export_review(source,review,tmp_path/"output")
    source.write_text("changed",encoding="utf-8")
    with pytest.raises(ValueError,match="来源漂移"): reference_audit.reviewed_rows(source,review)


@pytest.mark.parametrize("change",["duplicate","out_of_range","unreviewed","no_reason","wrong_old","wrong_pixel","none_resolved"])
def test_invalid_review_cannot_become_automatic_ground_truth(tmp_path,change):
    source,review,value=inputs(tmp_path)
    row=value["annotations"][0]
    if change=="duplicate": value["annotations"].append(row)
    if change=="out_of_range": row["row"]=10
    if change=="unreviewed": row["visual_label"]="PENDING"
    if change=="no_reason": row["reason"]=""
    if change=="wrong_old": row["old_label"]="chatter"
    if change=="wrong_pixel": row["px"]=200
    if change=="none_resolved": row["visual_label"]="boundary"
    review.write_text(json.dumps(value),encoding="utf-8")
    with pytest.raises(ValueError): reference_audit.export_review(source,review,tmp_path/"output")


def test_cli(tmp_path,monkeypatch):
    source,review,value=inputs(tmp_path)
    monkeypatch.setattr("sys.argv",["reference_audit.py","--source",str(source),"--review",str(review),"--output",str(tmp_path/"cli")])
    reference_audit.main()
    assert (tmp_path/"cli/manifest.json").exists()
