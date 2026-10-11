from dataclasses import replace
import json

import numpy as np
import pytest

from experiment import (candidate_actions, choose_candidate, draft_tasks, enumerate_candidates, run)
from geometry import Contract, Patch, audit_edit, base_geometry, fit_displacement, seam_residuals
from kernel import refine, roundtrip, to_surface
import language_baseline as language


@pytest.mark.parametrize('seed', [1, 18, 20261010])
def test_nonflat_baseline_has_exact_seams(seed):
    patches = base_geometry(seed)
    assert max(seam_residuals(patches, 2)) < 1e-10
    # Kernel and SciPy evaluate the same original polynomial independently.
    for patch in patches:
        p = to_surface(patch).Value(.23, .61)
        assert np.allclose([p.X(), p.Y(), p.Z()], patch.point(.23, .61), atol=1e-12)


@pytest.mark.parametrize('action', ['insert_knots', 'elevate_degree'])
def test_actual_refinement_preserves_shape_and_unlocks_edit(action):
    original = base_geometry(1)
    refined = list(original)
    refined[1] = refine(original[1], action)
    assert refined[1].poles.shape[0] == 7
    for u in np.linspace(0, 1, 23):
        assert np.allclose(original[1].point(u, .37), refined[1].point(u, .37), atol=1e-12)
    contract = Contract(1, 2, True, False)
    edited, evidence = fit_displacement(refined, contract, (1,))
    assert evidence['feasible']
    assert audit_edit(original, edited, contract, (1,))['passed']
    assert np.max(np.abs(edited[0].poles - original[0].poles)) < 1e-10


def test_conflicting_pin_has_certificate():
    patches = base_geometry(2)
    contract = Contract(1, 2, True, True, pin_center=True)
    candidates = enumerate_candidates(patches, contract)
    assert choose_candidate(candidates, contract) is None
    for record, _ in candidates:
        assert record['solver']['certificate_null_error'] < 1e-9
        assert abs(record['solver']['certificate_rhs']) > .01


def test_permissions_and_priorities_change_action():
    original = base_geometry(3)
    local = Contract(1, 2, True, True)
    candidates = enumerate_candidates(original, local)
    selected, _ = choose_candidate(candidates, local)
    assert selected['action'] in ('insert_knots', 'elevate_degree')
    selected, _ = choose_candidate(candidates, replace(local, priority='complexity'))
    assert selected['action'].startswith('expand_')
    assert [a for a, _ in candidate_actions(Contract(1, 2, False, False))] == ['local']
    assert choose_candidate(enumerate_candidates(original, Contract(1, 2, False, False)), local) is None


def test_detects_broken_seam_and_forbidden_neighbor_edit():
    original = base_geometry(7)
    contract = Contract(1, 2, True, False)
    selected, edited = choose_candidate(enumerate_candidates(original, contract), contract)
    edited[0].poles[3, 1, 2] += .1
    assert not audit_edit(original, edited, contract, (1,))['passed']
    edited[0].poles[1, 1, 2] += .1
    assert audit_edit(original, edited, contract, (1,))['locked_control_bound_mm'] > .01


def test_step_roundtrip_preserves_geometry_and_derivatives(tmp_path):
    original = base_geometry(9)
    contract = Contract(1, 2, True, False)
    _, edited = choose_candidate(enumerate_candidates(original, contract), contract)
    restored, check = roundtrip(edited, tmp_path / 'roundtrip.step')
    assert check['passed'] and check['face_count'] == 3
    assert check['step_bytes'] > 1000
    assert audit_edit(original, restored, contract, (1,))['passed']


def test_draft_pairs_keep_oracle_labels_separate():
    tasks = draft_tasks()
    assert len(tasks) == 60
    for i in range(0, 60, 2):
        assert tasks[i]['geometry_id'] == tasks[i + 1]['geometry_id']
        assert tasks[i]['input_text'] != tasks[i + 1]['input_text']
        assert tasks[i]['oracle_contract'] != tasks[i + 1]['oracle_contract']
    assert len({t['seed'] for t in tasks}) == 30


def test_small_complete_run_and_no_overwrite(tmp_path):
    summary = run(tmp_path / 'run', 5)
    assert summary['completed'] and summary['expectation_matches'] == 10
    public = [json.loads(line) for line in (tmp_path / 'run/public_tasks.jsonl').read_text(encoding='utf-8').splitlines()]
    assert all('oracle_contract' not in item and 'expected_status' not in item for item in public)
    with pytest.raises(FileExistsError):
        run(tmp_path / 'run', 5)


@pytest.mark.parametrize('kwargs', [dict(amplitude_mm=float('nan')), dict(continuity=3),
                                   dict(allow_refine='false'), dict(priority='anything')])
def test_invalid_contract(kwargs):
    fields = dict(amplitude_mm=1, continuity=2, allow_refine=True, allow_expand=False)
    fields.update(kwargs)
    with pytest.raises(ValueError):
        Contract(**fields)


def test_invalid_geometry_scope_and_action():
    patches = base_geometry(5)
    with pytest.raises(ValueError):
        replace(patches[0], u_knots=np.zeros(3))
    with pytest.raises(ValueError):
        replace(patches[0], length=-1)
    with pytest.raises(ValueError):
        fit_displacement(patches, Contract(1, 2, True, False), (0,))
    with pytest.raises(ValueError):
        refine(patches[0], 'invalid')
    with pytest.raises(ValueError):
        draft_tasks(0)
    with pytest.raises(ValueError):
        replace(patches[0], poles=np.zeros((3,)))
    with pytest.raises(ValueError):
        replace(patches[0], width=float('nan'))
    moved = patches[0].poles.copy()
    moved[1, 1, 0] += .1
    with pytest.raises(ValueError, match='UNSUPPORTED_PARAMETERIZATION'):
        replace(patches[0], poles=moved)


def test_zero_edit_with_center_pin_is_feasible():
    original = base_geometry(6)
    contract = Contract(0, 2, False, False, pin_center=True)
    edited, evidence = fit_displacement(original, contract, (1,))
    assert evidence['feasible']
    assert audit_edit(original, edited, contract, (1,))['passed']


def test_language_parsers_and_invalid_outputs():
    for task in draft_tasks(5):
        fields = task['oracle_contract']
        assert language.parse_contract(json.dumps(fields)) == Contract(**fields)
        assert language.parse_contract('```json\n' + json.dumps(fields) + '\n```') == Contract(**fields)
        assert language.rule_contract(task['input_text']) == Contract(**fields)
    with pytest.raises(ValueError):
        language.parse_contract('{}')
    fields['continuity'] = True
    with pytest.raises(ValueError):
        language.parse_contract(json.dumps(fields))
    with pytest.raises(ValueError):
        language.rule_contract('make this beautiful')


def test_prediction_boundary_and_evaluator(tmp_path, monkeypatch):
    class FakeBackend:
        model = 'fake-test-only'
        temperature = 0.
        max_tokens = 512

        def chat(self, text, system, retries):
            assert retries == 0 and system == language.SYSTEM_PROMPT
            return json.dumps(language.asdict(language.rule_contract(text)))

    monkeypatch.setattr(language, 'existing_backend', lambda: (FakeBackend(), 'fake-source-hash'))
    tasks = draft_tasks(1)
    public = tmp_path / 'public.jsonl'
    public.write_text('\n'.join(json.dumps(dict(task_id=t['task_id'], geometry_id=t['geometry_id'],
                           input_text=t['input_text'], geometry_path='g.step')) for t in tasks), encoding='utf-8')
    predictions = tmp_path / 'predictions.jsonl'
    language.predict(public, predictions)
    oracle = tmp_path / 'oracle.json'
    oracle.write_text(json.dumps(dict(tasks=tasks)), encoding='utf-8')
    scored = language.evaluate(oracle, predictions, tmp_path / 'scores.json')
    assert scored['llm_exact'] == scored['rule_exact'] == 2
    assert scored['llm_both_correct_pairs'] == 1
    with pytest.raises(FileExistsError):
        language.predict(public, predictions)
    with pytest.raises(ValueError):
        language.predict(public, tmp_path / 'invalid', limit=61)
    public.write_text(json.dumps(tasks[0]), encoding='utf-8')
    with pytest.raises(ValueError, match='ORACLE_LEAKAGE'):
        language.predict(public, tmp_path / 'leaked.jsonl')
    with predictions.open('a', encoding='utf-8') as handle:
        handle.write(predictions.read_text(encoding='utf-8').splitlines()[0] + '\n')
    with pytest.raises(ValueError, match='DUPLICATE'):
        language.evaluate(oracle, predictions, tmp_path / 'duplicate.json')


def test_transport_failure_is_preserved(tmp_path, monkeypatch):
    class FailingBackend:
        model, temperature, max_tokens = 'fake-test-only', 0., 512

        def chat(self, *args, **kwargs):
            raise TimeoutError('do not write potentially sensitive exception details')

    monkeypatch.setattr(language, 'existing_backend', lambda: (FailingBackend(), 'fake-source-hash'))
    task = draft_tasks(1)[0]
    public = tmp_path / 'public.jsonl'
    public.write_text(json.dumps(dict(task_id=task['task_id'], geometry_id='g000',
                                    input_text=task['input_text'], geometry_path='g.step')), encoding='utf-8')
    predictions = tmp_path / 'failed.jsonl'
    language.predict(public, predictions, limit=1)
    record = json.loads(predictions.read_text(encoding='utf-8'))
    assert record['status'] == 'failed' and record['error_type'] == 'TimeoutError'
    assert 'sensitive' not in predictions.read_text(encoding='utf-8')


def test_existing_backend_configuration_without_network(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'unit-test-placeholder-not-a-real-key')
    backend, source_hash = language.existing_backend()
    assert backend.max_tokens == 512 and backend.temperature == 0
    assert backend.timeout_seconds == 60 and len(source_hash) == 64


def test_empty_prediction_input(tmp_path):
    public = tmp_path / 'empty.jsonl'
    public.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='EMPTY_TASKS'):
        language.predict(public, tmp_path / 'predictions.jsonl')
