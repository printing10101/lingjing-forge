"""Exercise source-review guards with isolated in-memory code mutations."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]
sys.path.insert(0, str(ROOT / "engineering/python"))
os.environ.pop("PYTHONPATH", None)

from app.rag import source_review_packet as source_module  # noqa: E402

spec = importlib.util.spec_from_file_location("review_packet_mutation_tests",
    ROOT / "engineering/python/tests/unit/test_source_review_packet.py")
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
source_path = Path(source_module.__file__)
original = source_path.read_text(encoding="utf-8")
before = hashlib.sha256(source_path.read_bytes()).hexdigest()
mutations = [
    ("source_hash", 'if source.document_sha256 != manifest["sha256"]:', "if False:", "hash"),
    ("material_scope", "if set(source.materials) != materials:", "if False:", "extra_material"),
    ("duplicate_fact", "if len(set(keys)) != len(keys):", "if False:", "duplicate_key"),
    ("page_bound", "if any(page < 1 or page > page_count for page in fact.pdf_pages):", "if False:", "page_overflow"),
    ("retain_blockers", '[*candidate["unresolved"], *source.open_questions]', '[*source.open_questions]', None),
    ("no_approval", '"eligible_for_recommendation": False,', '"eligible_for_recommendation": True,', None),
    ("preserve_parameters", 'packet = deepcopy(candidate)', 'packet = deepcopy(candidate)\n        packet["parameters"]["spindle_rpm"] = 1000', None),
    ("forbid_trust_fields", 'model_config = ConfigDict(extra="forbid")', 'model_config = ConfigDict(extra="ignore")', "unknown_trust"),
]
tests.test_review_packet_preserves_identity_parameters_and_every_original_blocker()
for _, _, _, case in mutations:
    if case:
        tests.test_invalid_supplements_fail_closed(case)
results = []
for number, (label, old, new, case) in enumerate(mutations):
    assert old in original, label
    module_name = f"app.rag._review_packet_mutation_{number}"
    module = types.ModuleType(module_name)
    module.__package__ = "app.rag"
    sys.modules[module_name] = module
    mutated = original.replace(old, new) if label == "forbid_trust_fields" else original.replace(old, new, 1)
    exec(compile(mutated, str(source_path), "exec"), module.__dict__)
    tests.build_vendor_review_packet = module.build_vendor_review_packet
    try:
        if case:
            tests.test_invalid_supplements_fail_closed(case)
        else:
            tests.test_review_packet_preserves_identity_parameters_and_every_original_blocker()
    except BaseException as error:
        # Only explicit failed assertions count. Unexpected errors are not proof.
        caught = isinstance(error, AssertionError) or type(error).__name__ == "Failed"
        results.append({"name": label, "caught": caught, "failure_type": type(error).__name__})
    else:
        results.append({"name": label, "caught": False})
    finally:
        tests.build_vendor_review_packet = source_module.build_vendor_review_packet
        del sys.modules[module_name]
assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before
tests.test_review_packet_preserves_identity_parameters_and_every_original_blocker()
(DIRECTORY / "mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(row["caught"] for row in results) else 1)
