import json
import shutil
from pathlib import Path

import pytest

from prototypes.thinwall_inverse_design import pilot


def test_frozen_run_and_tiny_end_to_end(tmp_path, monkeypatch):
    source = tmp_path/"source"
    source.mkdir()
    for name in ("mechanics.py", "pilot.py", "pilot_config.json"):
        shutil.copy2(pilot.ROOT/name, source/name)
    config = json.loads((source/"pilot_config.json").read_text(encoding="utf-8"))
    config["profile"]["min_mm"] = config["profile"]["max_mm"] = 2.0
    config["screening"].update(elements=6, modes=1, rpm_count=2, contacts=[1.0], frequency_resolution=200)
    config["verification"].update(mesh_elements=[6,12], mode_counts=[1], damping_ratios=[0.01],
                                   off_grid_rpms=[6750], floquet_steps=[16])
    pilot.write_json(source/"pilot_config.json", config)
    monkeypatch.setattr(pilot, "ROOT", source)
    output = tmp_path/"run"
    pilot.screen(output)
    pilot.verify(output)
    assert (output/"screening.png").stat().st_size > 1000
    summary = json.loads((output/"screening_summary.json").read_text(encoding="utf-8"))
    assert summary["design_count"] == 1
    assert summary["scenario_count"] == 2
    verification = json.loads((output/"verification.json").read_text(encoding="utf-8"))
    assert verification["physical_external_validation"] == "NOT PERFORMED"
    assert all(check["consistent_growth_sign"] for check in verification["time_domain_checks"])
    with pytest.raises(FileExistsError):
        pilot.freeze_run(output)
    (source/"mechanics.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="版本漂移"):
        pilot.load_frozen(output)


def test_mass_grid_and_cli(tmp_path, monkeypatch):
    config = json.loads((pilot.ROOT/"pilot_config.json").read_text(encoding="utf-8"))
    candidates = pilot.profiles(config)
    assert len(candidates) > 20
    assert all(sum(t) == pytest.approx(0.006) for t in candidates)
    called = []
    monkeypatch.setattr(pilot, "screen", lambda path: called.append(path))
    monkeypatch.setattr("sys.argv", ["pilot.py", "screen", "--output", str(tmp_path/"cli")])
    pilot.main()
    assert called == [(tmp_path/"cli").resolve()]
