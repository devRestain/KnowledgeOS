"""Offline ownership, bounded Work validation and inert storage preservation."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest
from support.control_factory import make_separate_portable_fixture_roots
from support.work_factory import WorkFixture

from vaultops.adapters.core import AdmissionError, canonical
from vaultops.adapters.legacy_archive import archive_runtime, inventory
from vaultops.application.knowledge import KnowledgeApplication, LocalIdentity
from vaultops.application.work import (
    WorkContracts,
    record_digest,
    segment_digest,
    validate_bundle_file,
    work_spec_digest,
)
from vaultops.interfaces.cli import build_parser, main
from vaultops.projection import (
    CURRENT_POINTER,
    _runtime_path,
    _state_path,
    generate_projection,
    read_current_projection,
)


def make_app(tmp_path):
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    return roots, KnowledgeApplication.from_roots(roots)


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.is_dir(), p.read_bytes() if p.is_file() else None) for p in root.rglob('*')}


def test_cli_has_only_maintenance_commands():
    commands = next(action.choices for action in build_parser()._actions if hasattr(action, "choices") and action.choices)
    assert set(commands) == {"version", "bootstrap", "configure", "doctor", "reconcile", "git",
                             "plugins", "foundation", "blueprint", "schema", "vault-artifacts",
                             "index", "note", "repair", "receipts", "operation", "work", "storage"}
    for forbidden in ("ai", "search", "retrieve", "ask", "capture", "project", "vector"):
        assert forbidden not in commands


def test_mcp_cannot_enter_other_writers(tmp_path):
    roots, _ = make_app(tmp_path)
    app = KnowledgeApplication.from_roots(roots, identity=LocalIdentity('local.mcp','mcp'))
    before = snapshot(roots.state)
    for action in ('capture','note','project','asset','index','storage','broker','scheduler','host','model','approve','apply'):
        with pytest.raises(AdmissionError, match='denies'):
            app.guard(action)
    assert snapshot(roots.state) == before


def test_explicit_storage_accessors_and_unavailable_read_make_no_files(tmp_path):
    roots, _ = make_app(tmp_path)
    assert _runtime_path(roots, CURRENT_POINTER).is_relative_to(roots.runtime)
    assert _state_path(roots, CURRENT_POINTER).is_relative_to(roots.state)
    before = snapshot(roots.state), snapshot(roots.runtime)
    report, code = read_current_projection(roots)
    assert code != 0, report
    assert (snapshot(roots.state), snapshot(roots.runtime)) == before
    report, code = generate_projection(roots)
    assert code == 0, report
    assert (roots.state / CURRENT_POINTER).is_file()
    assert not (roots.runtime / CURRENT_POINTER).exists()


def test_work_bundle_is_static_control_scoped_and_read_only(tmp_path, capsys):
    roots, app = make_app(tmp_path)
    fixture = WorkFixture()
    bundle = {'schema_version':1,'checks':[{'kind':'spec','arguments':{'spec':fixture.spec}}, {'kind':'segment','arguments':{'spec':fixture.spec,'run':fixture.run,'artifacts':fixture.current_artifacts,**fixture.bundle()}}]}
    path = roots.control / 'work-candidate.json'
    path.write_bytes(canonical(bundle))
    before = tuple(snapshot(p) for p in (roots.control,roots.vault,roots.state,roots.runtime))
    assert main(['work','validate','--bundle','work-candidate.json','--root',str(roots.control)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['authority'] == 'none' and not result['admission_performed'] and not result['execution_started']
    assert tuple(snapshot(p) for p in (roots.control,roots.vault,roots.state,roots.runtime)) == before
    for relative in ('../outside.json', str(path), 'linked.json'):
        if relative == 'linked.json':
            (roots.control / relative).symlink_to(path)
        assert validate_bundle_file(app.core, roots.control, relative)[1] != 0


@pytest.mark.parametrize('change', ['profile','executor','pin','lineage','budget','unknown','foreign','digest'])
def test_work_segment_semantic_denials(tmp_path, change):
    _, app = make_app(tmp_path)
    f = WorkFixture()
    spec, run, bundle = copy.deepcopy(f.spec), copy.deepcopy(f.run), copy.deepcopy(f.bundle())
    if change == 'profile': bundle['assignments'][0]['capability_version'] = '2.0.0'
    if change == 'executor': bundle['assignments'][0]['executor_id'] = 'knowledgeos.foreign'
    if change == 'pin':
        spec['explicit_assignments'] = [{'node_id':'node.pinned','executor_id':'knowledgeos.writer','criterion_ids':['criterion.a'],**f.profile}]
        spec['spec_digest'] = work_spec_digest(spec)
        run['work_spec_digest'] = bundle['segment']['work_spec_digest'] = bundle['assignments'][0]['work_spec_digest'] = spec['spec_digest']
        bundle['segment']['segment_digest'] = segment_digest(bundle['segment'])
    if change == 'lineage': bundle['segment']['segment_index'] = 2; bundle['segment']['segment_digest'] = segment_digest(bundle['segment'])
    if change == 'budget': run['profile_invocations'] = spec['continuation_policy']['max_profile_invocations'] + 1
    if change == 'unknown': run['unresolved_effect_ids'] = ['effect.unknown']
    if change == 'foreign': bundle['segment']['owner_operation_id'] = 'foreign'
    if change == 'digest': bundle['nodes'][1]['max_attempts'] += 1
    with pytest.raises(AdmissionError):
        WorkContracts(app.core).segment(spec=spec,run=run,artifacts=f.current_artifacts,**bundle)


@pytest.mark.parametrize('change', ['independence','criterion','evidence','checkpoint','semantic'])
def test_work_evaluation_and_evidence_denials(tmp_path, change):
    _, app = make_app(tmp_path)
    f = WorkFixture(); flow = f.flow()
    spec, assessment = copy.deepcopy(f.spec), copy.deepcopy(flow['assessment2'])
    evaluations = [flow['eval1'],flow['eval2']]
    artifacts = copy.deepcopy(f.current_artifacts)
    checkpoints = {e['run_id']:e['checkpoint_digest'] for e in evaluations}
    if change == 'independence': spec['eval_officer_executor_id'] = spec['manager_executor_id']; spec['spec_digest'] = work_spec_digest(spec)
    if change == 'criterion': assessment['criteria_revision'] += 1
    if change == 'evidence': artifacts[f.artifacts['a']['artifact_id']]['source_revision'] = 'revision.2'
    if change == 'checkpoint': checkpoints[evaluations[0]['run_id']] = record_digest({'wrong':True})
    if change == 'semantic': assessment['semantic_digest'] = record_digest({'wrong':True})
    with pytest.raises(AdmissionError):
        WorkContracts(app.core).assessment(assessment,spec,flow['complete'],segments=[flow['first']['segment'],flow['second']['segment']],evaluations=evaluations,artifacts=artifacts,checkpoints=checkpoints,candidate_assessor_id=f.spec['eval_officer_executor_id'])


def test_complete_candidate_flow_and_budget_require_human(tmp_path):
    _, app = make_app(tmp_path)
    f = WorkFixture()
    flow = f.flow()
    contracts = WorkContracts(app.core)
    args = {'artifacts': f.current_artifacts, 'candidate_assessor_id': 'knowledgeos.evaluator'}
    checkpoints = {e['run_id']: e['checkpoint_digest'] for e in (flow['eval1'], flow['eval2'])}
    contracts.assessment(flow['assessment1'], f.spec, flow['between'], segments=[flow['first']['segment']], evaluations=[flow['eval1']], checkpoints=checkpoints, **args)
    contracts.segment(spec=f.spec, run=flow['between'], artifacts=f.current_artifacts, predecessor=flow['first']['segment'], trigger=flow['remaining'], assessment=flow['assessment1'], **flow['second'])
    contracts.run(flow['complete'], f.spec, assessment=flow['assessment2'], segments=[flow['first']['segment'], flow['second']['segment']], evaluations=[flow['eval1'], flow['eval2']], checkpoints=checkpoints, **args)
    exhausted = {**flow['between'], 'repeated_gap_count': f.spec['continuation_policy']['max_repeated_gap_assessments']}
    assert contracts.continuation(flow['remaining'], f.spec, exhausted, assessment=flow['assessment1']) == 'human_required'
    active = {**flow['between'], 'active_graph_run_id': flow['first']['segment']['graph_run_id']}
    intent, receipt = f.effect(active)
    assert contracts.effect(intent, active, receipt=receipt) == 'succeeded'
    with pytest.raises(AdmissionError):
        contracts.effect(intent, active, receipt={**receipt, 'generation': 2})


@pytest.mark.parametrize('change', ['none', 'actor', 'generation', 'target', 'received_as_applied'])
def test_candidate_steering_binds_control_and_keeps_receipt_distinct(tmp_path, change):
    from support.work_factory import AT, UNTIL

    from vaultops.application.work import directive_digest, work_control_binding
    from vaultops.application.work_binding import _target_digest

    _, app = make_app(tmp_path)
    f = WorkFixture()
    run = f.flow()['between']
    directive = {**f.context, **f.pins, 'directive_id': 'directive.synthetic.one', 'actor_id': 'human.owner', 'target_control_revision': run['control_revision'], 'generation': run['generation'], 'fencing_token': run['fencing_token'], 'kind': 'pause', 'human_request_id': 'request.synthetic.control', 'human_response_id': 'response.synthetic.control', 'decision_receipt_id': 'decision.synthetic.control', 'effect_id': 'effect.synthetic.one', 'received_at': AT, 'expires_at': UNTIL, 'application_state': 'received'}
    target = {'action_id': directive['directive_id'], 'target_revision': f"revision.{run['control_revision']}", 'operative_package_version': '0.1.0', 'operative_package_digest': f.artifacts['binding']['content_digest'], 'mapping_id': 'knowledgeos.synthetic.control', 'mapping_version': '1.0.0', 'mapping_digest': f.artifacts['binding']['content_digest'], 'work_binding': work_control_binding(directive)}
    target_digest = _target_digest(target, f.pins['semantic_version'], f.pins['semantic_digest'])
    context = {key: directive[key] for key in ('owner_operation_id', 'work_run_id', 'work_spec_revision', 'work_spec_digest', 'target_control_revision')}
    request = {**context, 'request_id': directive['human_request_id'], 'request_revision': 1, 'question': 'Validate this synthetic pause?', 'impact': 'Candidate data only', 'allowed_actions': ['pause'], 'evidence_reference_ids': [], 'expires_at': UNTIL, 'lifecycle': 'resolved', 'semantic_version': f.pins['semantic_version'], 'semantic_digest': f.pins['semantic_digest'], 'target_digest': target_digest}
    response = {**context, 'actor_id': 'human.owner', 'chosen_action': 'pause', 'idempotency_key': 'synthetic.control', 'received_at': AT, 'request_id': request['request_id'], 'request_revision': 1, 'response_id': directive['human_response_id'], 'submission_state': 'submitted', 'target_digest': target_digest}
    decision = {'receipt_id': directive['decision_receipt_id'], 'owner_operation_id': 'knowledgeos', 'request_id': request['request_id'], 'request_revision': 1, 'decision_state': 'accepted', 'chosen_action': 'pause'}
    directive['decision_target_digest'] = target_digest
    directive['directive_digest'] = directive_digest(directive)
    intent, _ = f.effect(run, content_digest=directive['directive_digest'])
    if change == 'actor': response['actor_id'] = 'human.foreign'
    if change == 'generation': directive['generation'] += 1
    if change == 'target': target['target_revision'] = 'revision.99'
    if change == 'received_as_applied':
        directive.update(application_state='applied', applied_at=AT, applied_run_revision=5, execution_receipt_id='receipt.fake')
    args = {'request': request, 'response': response, 'decision': decision, 'target_binding': target, 'intent': intent, 'candidate_actor_id': 'human.owner', 'now': datetime.fromisoformat(AT), 'artifacts': f.current_artifacts}
    if change == 'none':
        WorkContracts(app.core).steering(directive, f.spec, run, **args)
    else:
        with pytest.raises(AdmissionError):
            WorkContracts(app.core).steering(directive, f.spec, run, **args)


def prepare_archive(roots, app):
    (roots.runtime / 'approved').mkdir(mode=0o700)
    (roots.runtime / 'approved/legacy.json').write_bytes(b'old approval is inert\n')
    (roots.runtime / 'approved/legacy.json').chmod(0o600)
    (roots.runtime / 'empty').mkdir(mode=0o700)
    report, _ = archive_runtime(app)
    evidence = {'source':str(roots.runtime),'inventory_digest':report['inventory_digest'], 'observed_at':datetime.now(UTC).isoformat(), 'writers_quiescent':True,'checks':['synthetic writer inventory; no external execution']}
    (roots.control / 'writer.json').write_bytes(canonical(evidence))
    return report


@pytest.mark.parametrize('phase', ['prepared','renamed','initialized','none'])
def test_archive_resume_preserves_payload_and_never_imports_authority(tmp_path, phase):
    roots, app = make_app(tmp_path)
    report = prepare_archive(roots, app)
    before = inventory(roots.runtime)
    def fault(current):
        if current == phase: raise RuntimeError('interrupted')
    if phase != 'none':
        with pytest.raises(RuntimeError): archive_runtime(app,apply=True,expected_digest=report['inventory_digest'],writer_evidence='writer.json',fault_hook=fault)
    replay, code = archive_runtime(app,apply=True,expected_digest=report['inventory_digest'],writer_evidence='writer.json')
    assert code == 0 and replay['archive_verified']
    from pathlib import Path
    assert inventory(Path(replay['target']) / 'payload') == before
    journal = app.journal.read()
    assert journal['generation'] == 1 and not any(journal[name] for name in ('intents','decisions','human_requests','outbox'))
    assert not (roots.runtime / 'approved').exists()
    assert not any((roots.state / 'approved').iterdir())
    assert app.recover()[1] == 0
    assert archive_runtime(app,apply=True,expected_digest=report['inventory_digest'],writer_evidence='writer.json')[0]['replayed']


@pytest.mark.parametrize('change', ['drift','unconfirmed','digest','conflict','active_lock','stale_writer_inventory'])
def test_archive_refuses_drift_and_unconfirmed_writers(tmp_path, change):
    roots, app = make_app(tmp_path)
    report = prepare_archive(roots, app)
    evidence, expected = 'writer.json', report['inventory_digest']
    if change == 'drift': (roots.runtime / 'changed').write_bytes(b'changed')
    if change == 'unconfirmed': evidence = None
    if change == 'digest': expected = 'sha256:' + '0'*64
    if change == 'conflict':
        from pathlib import Path
        Path(report['target']).mkdir(parents=True)
    if change == 'stale_writer_inventory':
        value = json.loads((roots.control / 'writer.json').read_bytes())
        value['observed_at'] = '2000-01-01T00:00:00Z'
        (roots.control / 'writer.json').write_bytes(canonical(value))
    held = None
    if change == 'active_lock':
        import fcntl
        lock = roots.runtime / 'worker.lock'
        lock.write_bytes(b'')
        held = lock.open('rb')
        fcntl.flock(held, fcntl.LOCK_EX)
        report = prepare_archive_lock_plan(roots, app)
        expected = report['inventory_digest']
    before = snapshot(roots.runtime)
    with pytest.raises(AdmissionError): archive_runtime(app,apply=True,expected_digest=expected,writer_evidence=evidence)
    assert snapshot(roots.runtime) == before
    if held is not None:
        held.close()


def prepare_archive_lock_plan(roots, app):
    report, _ = archive_runtime(app)
    value = json.loads((roots.control / 'writer.json').read_bytes())
    value['inventory_digest'] = report['inventory_digest']
    (roots.control / 'writer.json').write_bytes(canonical(value))
    return report
