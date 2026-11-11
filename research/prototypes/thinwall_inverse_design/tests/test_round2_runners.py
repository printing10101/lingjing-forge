import json
import shutil

import pytest

from prototypes.thinwall_inverse_design import benchmark,plate_pilot,spline_pilot


def small_config(root,name):
    config=json.loads((root/name).read_text(encoding="utf-8"))
    config["thickness_mm"].update(min=2,max=2)
    config["modes"]=2
    config["screen_contacts"]=[[0.85,0.5]]
    config["screen_rpms"]=[6000]
    config["verification_contacts"]=[[0.75,0.5]]
    config["verification_rpms"]=[6750]
    return config


def test_global_plate_runner_freezes_and_checks_pipeline(tmp_path,monkeypatch):
    source=tmp_path/"global_source"
    source.mkdir()
    for name in plate_pilot.SOURCES: shutil.copy2(plate_pilot.ROOT/name,source/name)
    config=small_config(source,"plate_config.json")
    config["plate"].update(order_x=2,order_y=2,quadrature=6)
    config["checks"].update(ritz_orders=[[2,2]],modes=[2],floquet_steps=[16])
    plate_pilot.write_json(source/"plate_config.json",config)
    monkeypatch.setattr(plate_pilot,"ROOT",source)
    output=tmp_path/"global_run"
    plate_pilot.screen(output)
    plate_pilot.verify(output)
    summary=json.loads((output/"screening.json").read_text(encoding="utf-8"))
    assert summary["design_count"]==summary["feasible_count"]==1
    assert summary["selected"]["uniform"]["service_minimax"]==pytest.approx(1)
    data=json.loads((output/"verification.json").read_text(encoding="utf-8"))
    assert len(data["periodic"])==5
    (source/"plate.py").write_text("drift",encoding="utf-8")
    with pytest.raises(ValueError,match="版本漂移"): plate_pilot.frozen_config(output)


def test_spline_runner_rechecks_constraints_and_deduplicates(tmp_path,monkeypatch):
    source=tmp_path/"spline_source"
    source.mkdir()
    for name in spline_pilot.SOURCES: shutil.copy2(spline_pilot.ROOT/name,source/name)
    config=small_config(source,"spline_config.json")
    config["mesh_levels"]=[2]
    config["floquet_steps"]=[16]
    spline_pilot.write_json(source/"spline_config.json",config)
    monkeypatch.setattr(spline_pilot,"ROOT",source)
    output=tmp_path/"spline_run"
    spline_pilot.screen(output)
    spline_pilot.verify(output)
    data=json.loads((output/"verification.json").read_text(encoding="utf-8"))
    assert len(data["periodic"])==5
    assert sum(bool(r.get("reused_identical_profile")) for r in data["periodic"])==4
    assert data["new_contribution_established"] is False
    (source/"plate.py").write_text("drift",encoding="utf-8")
    with pytest.raises(ValueError,match="版本漂移"): spline_pilot.load_config(output)


def test_external_benchmark_confusion_and_tiny_actual_solver_run(tmp_path,monkeypatch):
    examples=[{"stable_true":True,"pred":True},{"stable_true":True,"pred":False},
              {"stable_true":False,"pred":True},{"stable_true":False,"pred":False}]
    assert benchmark.confusion(examples,"pred")["balanced_accuracy"]==0.5
    assert benchmark.confusion([],"pred")["accuracy"] is None
    source=tmp_path/"benchmark_source"
    source.mkdir()
    for name in ("benchmark.py","benchmark_config.json","coupled_milling.py","mechanics.py"):
        shutil.copy2(benchmark.ROOT/name,source/name)
    config=json.loads((source/"benchmark_config.json").read_text(encoding="utf-8"))
    config.update(source_csv="test.csv",source_local_pdf="fixture.pdf",steps=[16,32],
                  expected_reference_sha256=None,reference_status="test_fixture_not_scientific_evidence")
    benchmark.write_json(source/"benchmark_config.json",config)
    repo=tmp_path/"repo"
    repo.mkdir()
    (repo/"fixture.pdf").write_bytes(b"hash-only test fixture, not scientific evidence")
    (repo/"test.csv").write_text("fig,ae_label,ae_mm,n_rpm,ap_mm,result,review\n"
                               "Fig7,0.5D,4,20000,0.1,stable,\n"
                               "Fig7,D,8,25000,0.3,chatter,CHECK\n",encoding="utf-8")
    monkeypatch.setattr(benchmark,"ROOT",source)
    monkeypatch.setattr(benchmark,"REPO",repo)
    output=tmp_path/"benchmark_run"
    benchmark.run(output)
    data=json.loads((output/"results.json").read_text(encoding="utf-8"))
    assert data["all_rows"]["n"]==2 and data["excluding_CHECK"]["n"]==1
    assert data["thinwall_design_validation"] is False
    assert data["population_experimental_accuracy_supported"] is False
    config["expected_reference_sha256"]="wrong"
    benchmark.write_json(source/"benchmark_config.json",config)
    with pytest.raises(ValueError,match="来源漂移"): benchmark.run(tmp_path/"bad_hash")
    config["expected_reference_sha256"]=None
    benchmark.write_json(source/"benchmark_config.json",config)
    (repo/"test.csv").write_text("fig,ae_label,ae_mm,n_rpm,ap_mm,result,review\n"
                               "Fig8,D,8,25000,0.3,chatter,\n",encoding="utf-8")
    with pytest.raises(ValueError): benchmark.run(tmp_path/"bad_input")


def test_runner_cli_dispatch(tmp_path,monkeypatch):
    for module in (plate_pilot,spline_pilot):
        observed=[]
        monkeypatch.setattr(module,"screen",lambda output:observed.append(output))
        monkeypatch.setattr("sys.argv",["runner.py","screen","--output",str(tmp_path)])
        module.main()
        assert observed==[tmp_path.resolve()]
    observed=[]
    monkeypatch.setattr(benchmark,"run",lambda output:observed.append(output))
    monkeypatch.setattr("sys.argv",["benchmark.py","--output",str(tmp_path)])
    benchmark.main()
    assert observed==[tmp_path.resolve()]
