from argparse import Namespace
from copy import deepcopy
import json
import sqlite3

import pytest

from scripts.probe_judgment_input import main


def fixture_args(tmp_path):
    source={'id':'o','kind':'observation','case_id':'case','type':'linux_persistence','fields':{}}
    pack={'required_dossiers':[{'id':'d'}],'allowed_observation_ids':['o'],
          'allowed_observation_ids_by_dossier':{'d':['o']},'observations':[source],
          'executed_checks':[{'id':'completed-job','status':'covered','observation_ids':['o']}],
          'previous_assessment':{'scope':'exact later round, not parent input'}}
    output={'findings':[{'dossier_id':'d','judgment':'확인','observation_ids':['o'],
              'stages':[{'stage':'invocation','judgment':'확인','observation_ids':['o']}]}],
            'check_assessments':[{'check_id':'completed-job','outcome':'inconclusive','observation_ids':['o']}]}
    rows=[source,{'id':'config','kind':'config','provider':{}},
          {'id':'input','kind':'review_input','case_id':'case','batch_id':'batch','input_sha256':'bound','pack':pack},
          {'id':'receipt','kind':'receipt','case_id':'case','input_record_id':'input','input_sha256':'bound','output':output}]
    path=tmp_path/'case.db'
    with sqlite3.connect(path) as db:
        db.execute('create table records(id text, created_at text, body text)')
        db.executemany('insert into records values (?, ?, ?)',[(r['id'],'2026-01-01',json.dumps(r)) for r in rows])
    return Namespace(database=path,case_id='case',batch_id='batch',runtime_root=None,
                     input_record_id='input',rejected_receipt_id='receipt',out=tmp_path/'probe',feedback=None),output


def test_exact_round_probe_preserves_checks_and_never_rebuilds_or_writes_case(tmp_path,monkeypatch):
    args,output=fixture_args(tmp_path);before=args.database.read_bytes();captured={}
    def no_rebuild(*a,**k):raise AssertionError('saved round must not rebuild parent batch')
    def fake_consult(config,question,pack,**kwargs):
        captured.update(deepcopy(pack))
        corrected=deepcopy(output);corrected['findings'][0]['stages'][0]['stage']='configuration'
        return corrected,{'model':'synthetic-no-network'}
    monkeypatch.setattr('scripts.check_dossier_input.main',no_rebuild)
    monkeypatch.setattr('scripts.run_assisted_e2e.install_assisted_provider',lambda *a,**k:None)
    monkeypatch.setattr('workbench.investigator.consult',fake_consult)
    assert not main(args)
    assert captured['executed_checks'][0]['id']=='completed-job'
    assert captured['previous_assessment']['scope']=='exact later round, not parent input'
    assert captured['validation_feedback']['errors'][0]['code']=='static_content_not_invocation'
    assert args.database.read_bytes()==before
    assert json.loads((args.out/'result.json').read_text())['worker_calls']==0


def test_exact_round_rejects_other_case_input_before_model_call(tmp_path,monkeypatch):
    args,_=fixture_args(tmp_path);args.case_id='different-case'
    monkeypatch.setattr('scripts.run_assisted_e2e.install_assisted_provider',
                        lambda *a,**k:pytest.fail('must not install external transport'))
    with pytest.raises(ValueError,match='case/batch binding mismatch'):
        main(args)


@pytest.mark.parametrize('diagnostic_case',['case','wrong-case'])
def test_rejected_receipt_uses_only_its_bound_diagnostic(tmp_path,monkeypatch,diagnostic_case):
    args,output=fixture_args(tmp_path)
    with sqlite3.connect(args.database) as db:
        receipt=json.loads(db.execute("select body from records where id='receipt'").fetchone()[0])
        receipt.pop('output');receipt['diagnostic_id']='diag'
        db.execute("update records set body=? where id='receipt'",(json.dumps(receipt),))
        diagnostic={'id':'diag','kind':'review_diagnostic','case_id':diagnostic_case,'batch_id':'batch',
                    'input_record_id':'input','input_sha256':'bound','rejected_output':output}
        db.execute('insert into records values (?,?,?)',('diag','2026-01-01',json.dumps(diagnostic)))
    before=args.database.read_bytes()
    monkeypatch.setattr('scripts.run_assisted_e2e.install_assisted_provider',lambda *a,**k:None)
    def fake_consult(*a,**k):
        assert diagnostic_case=='case'
        corrected=deepcopy(output);corrected['findings'][0]['stages'][0]['stage']='configuration'
        return corrected,{'model':'synthetic-no-network'}
    monkeypatch.setattr('workbench.investigator.consult',fake_consult)
    if diagnostic_case=='case':assert not main(args)
    else:
        with pytest.raises(ValueError,match='diagnostic does not belong'):
            main(args)
    assert args.database.read_bytes()==before
