"""Reproduce the real planner input locally, without model/tool transmission."""
import argparse
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def check(database,baseline_context):
    from workbench.controller import Controller
    from workbench.investigation_graph import Investigation
    from workbench.store import Store
    from workbench import review_context
    with tempfile.TemporaryDirectory(prefix='planner-input-') as td:
        local=Path(td)/'case.sqlite3'
        with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(local) as dst:
            src.backup(dst)
        store=Store(local);controller=Controller(store,Path(td))
        run=store.list('investigation_run')[-1]
        task=store.get(run['task_id']);evidence=store.get(run['evidence_id'])
        # Later judgment collections must not change the failing planner input.
        cutoff=task.get('ended_at')
        original_list=store.list
        if cutoff:
            store.list=lambda *a,**kw:[r for r in original_list(*a,**kw) if r['created_at']<=cutoff]
        captured={}
        original_fit=review_context.fit
        def capture(pack,maximum=36000):
            captured['before']=copy.deepcopy(pack)
            original_fit(pack,maximum)
            captured['after']=copy.deepcopy(pack)
        class NoTransmission(Exception):pass
        inv=Investigation(controller,run['case_id'],evidence,task)
        def no_model(*args,**kwargs):raise NoTransmission()
        inv.model=no_model
        try:
            review_context.fit=capture
            try:inv.plan({'run_id':run['id']})
            except NoTransmission:pass
        finally:review_context.fit=original_fit
        if 'after' not in captured:raise ValueError('Planner did not reach the model input boundary')
        spec=importlib.util.spec_from_file_location('workbench._baseline_context',baseline_context)
        baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
        rejected=copy.deepcopy(captured['before']);error=None
        try:baseline.fit(rejected)
        except ValueError as ex:error=str(ex)
        before=captured['before'];after=captured['after']
        def locators(pack):
            return [{key:o.get(key) for key in ('id','evidence_id','source_location','source_origin')}|
                {'fields':{key:o.get('fields',{}).get(key) for key in
                    ('path','artifact_path','inode','line','byte_offset','partition_offset','os_instance','source_sha256')}}
                for o in pack['observations']]
        return {'units':'serialized characters, not bytes/tokens','cutoff':cutoff,
            'before_characters':len(review_context.serialize(before)),
            'baseline_final_characters':len(review_context.serialize(rejected)),
            'baseline_error':error,'after_characters':len(review_context.serialize(after)),
            'observation_count':len(after['observations']),
            'ids_and_locators_preserved':locators(before)==locators(after),
            'all_hypotheses_preserved':all(before.get(k)==after.get(k) for k in ('hypotheses','dynamic_hypotheses')),
            'selection_audit':after.get('selection_audit'),
            'completed_tools_total':after.get('completed_tools_total'),
            'completed_tools_retained':len(after.get('completed_tools',[])),
            'completed_tools_omitted':after.get('completed_tools_omitted'),
            'completed_tools_scope':after.get('completed_tools_scope'),
            'model_calls':0,'worker_calls':0,'original_database_modified':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('database',type=Path)
    parser.add_argument('--baseline-context',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(check(args.database,args.baseline_context),ensure_ascii=False,indent=2))
