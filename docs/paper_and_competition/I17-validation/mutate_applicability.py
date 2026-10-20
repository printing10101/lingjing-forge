"""Test guard mutations in memory against isolated synthetic records only."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types

directory = Path(__file__).resolve().parent
root = directory.parents[2]
os.environ.pop("PYTHONPATH", None)
sys.path[:0] = [str(root / "engineering/python"), str(root)]
from app.rag import process_applicability as policy, process_quadruple as quads, process_review as review  # noqa: E402

spec = importlib.util.spec_from_file_location("applicability_mutation_tests", root / "engineering/python/tests/unit/test_process_applicability.py")
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)


@contextmanager
def stored():
    with tempfile.TemporaryDirectory(prefix="i17-guard-") as path:
        index = quads.ProcessQuadrupleIndex(str(Path(path) / "knowledge"))
        index.add(quads.ProcessQuadruple("hole", "drill", "fixture_d8", {"spindle_rpm": 1200}, material="fixture_steel"))
        record = index.list_review_records()[0]
        record = index.update_review(record["record_id"], 1, actor="synthetic_engineer", decision="approved", note="Synthetic", evidence=tests.evidence())
        yield (index, record), Path(path)


def mismatch(key, value, reason):
    return lambda item, path: tests.test_each_mismatch_blocks_parameters(item, key, value, reason)


mutations = [
    ("missing_conditions", policy, 'return {**result, "status": "conditions_missing", "missing": missing}', 'return {**result, "status": "matched"}', tests.test_approval_requires_task_conditions_and_restarts_preserve_limits),
    ("tool", policy, 'if actual.tool != record["tool"]:', 'if False:', mismatch("tool", "other", "tool")),
    ("hardness", policy, 'if not limits.hardness_min_hrc <= actual.hardness_hrc <= limits.hardness_max_hrc:', 'if False:', mismatch("hardness_hrc", 24.1, "hardness_hrc")),
    ("exclusive_boundary", policy, 'elif not limits.hardness_max_inclusive and actual.hardness_hrc == limits.hardness_max_hrc:', 'elif False:', mismatch("hardness_hrc", 24.0, "hardness_hrc")),
    ("diameter", policy, 'if not limits.diameter_min_mm <= actual.diameter_mm <= limits.diameter_max_mm:', 'if False:', mismatch("diameter_mm", 8.00001, "diameter_mm")),
    ("depth", policy, 'if actual.depth_mm > limits.max_depth_to_diameter * actual.diameter_mm:', 'if False:', mismatch("depth_mm", 32.0001, "depth_mm")),
    ("coolant", policy, 'if actual.coolant not in limits.coolants:', 'if False:', mismatch("coolant", "dry", "coolant")),
    ("workholding", policy, 'if actual.workholding not in limits.workholdings:', 'if False:', mismatch("workholding", "chuck", "workholding")),
    ("scope", policy, 'if record["feature"] != "hole" or record["process"] not in ("drill", "drilling"):', 'if False:', lambda item, path: tests.test_evidence_tampering_and_legacy_approval_cannot_be_used(item)),
    ("evidence_binding", review, 'if review.get("evidence_digest") != evidence_digest(review["evidence"]):', 'if False:', lambda item, path: tests.test_evidence_tampering_and_legacy_approval_cannot_be_used(item)),
    ("require_limits", review, 'DrillingLimits.model_validate(evidence.get("constraints"))', 'pass', lambda item, path: tests.test_missing_limits_cannot_be_approved(item)),
    ("outer_gate", quads, 'if check["status"] != "matched":', 'if False:', mismatch("coolant", "dry", "coolant")),
    ("bounds", policy, 'self.hardness_min_hrc > self.hardness_max_hrc', 'False', lambda item, path: tests.test_bad_review_limits_rejected("hardness_min_hrc", 25.0)),
]
sources = {module: Path(module.__file__).read_text(encoding="utf-8") for module in (policy, review, quads)}
hashes = {module: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for module in sources}
for _, _, _, _, test in mutations:
    with stored() as (item, path):
        test(item, path)
results = []
for number, (name, module, old, new, test) in enumerate(mutations):
    source = sources[module]
    assert source.count(old) == 1, name
    mutated = types.ModuleType(f"app.rag._i17_mutant_{number}")
    mutated.__package__ = "app.rag"
    mutated.__file__ = module.__file__
    sys.modules[mutated.__name__] = mutated
    exec(compile(source.replace(old, new, 1), module.__file__, "exec"), mutated.__dict__)
    replacements = []
    if module is policy:
        replacements = [(quads, "check_applicability", mutated.check_applicability),
                        (tests, "check_applicability", mutated.check_applicability),
                        (tests, "DrillingLimits", mutated.DrillingLimits)]
    elif module is review:
        replacements = [(quads, "validate_approval", mutated.validate_approval),
                        (quads, "is_approved", mutated.is_approved), (tests, "is_approved", mutated.is_approved)]
    else:
        replacements = [(quads.ProcessQuadrupleIndex, "assess_recommendations", mutated.ProcessQuadrupleIndex.assess_recommendations)]
    backups = [(owner, key, getattr(owner, key)) for owner, key, _ in replacements]
    # Construct baseline storage before substituting policy; no actual approvals.
    with stored() as (item, path):
        for owner, key, value in replacements:
            setattr(owner, key, value)
        try:
            test(item, path)
        except BaseException as error:
            results.append({"name": name, "caught": isinstance(error, AssertionError) or type(error).__name__ == "Failed", "failure_type": type(error).__name__})
        else:
            results.append({"name": name, "caught": False})
        finally:
            for owner, key, value in backups:
                setattr(owner, key, value)
            del sys.modules[mutated.__name__]
for module, digest in hashes.items():
    assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == digest
with stored() as (item, path):
    tests.test_approval_requires_task_conditions_and_restarts_preserve_limits(item, path)
(directory / "mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(row["caught"] for row in results) else 1)
