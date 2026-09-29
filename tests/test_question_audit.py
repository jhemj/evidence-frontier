from scripts.audit_question_progress import audit


def test_normalized_trace_and_unknown_semantics_do_not_claim_quality():
    task={'kind':'task','id':'t'}
    def job(i,limit=1,length=8192):return {'kind':'investigation_job','id':i,'task_id':'t','status':'ingested',
        'request':{'tool':'read_file','path':'/private/secret','limit':limit,'byte_length':length},
        'result_scope':{'status':'failed','complete':False,'error':'private raw error'},'observation_ids':[]}
    out=audit([task,job('1'),job('2',20),job('3',1,256)])
    assert out['same_effective_request_extra_executions']==1
    assert len(out['failed_read_traces'])==3
    assert 'private' not in str(out)
    assert out['analyst_oracle_metrics']['critical_recall'] is None
    assert out['analyst_oracle_metrics']['claim_precision'] is None


def test_recoverable_critical_items_are_not_all_unknown_questions():
    rows=[{'kind':'task','id':'t'}, {'kind':'dossier','id':'d','task_id':'t','status':'reviewed',
            'finding':{'observation_ids':['o']}}]
    oracle={'critical_items':[{'observation_ids':['o'],'recoverable':True},
        {'observation_ids':['unavailable'],'recoverable':False}],
        'accepted_claim_annotations':[{'correct':True},{'correct':False}]}
    out=audit(rows,oracle)['analyst_oracle_metrics']
    assert out['critical_recall']==1 and out['claim_precision']==0.5


def test_transport_failures_without_usage_do_not_hide_metered_requests():
    rows=[{'kind':'receipt','id':'failure','transport':'codex-luna',
           'request_attempted':True,'receipt_type':'dossier_model_error'},
          {'kind':'receipt','id':'success','transport':'codex-luna',
           'request_id':'request-1','usage':{'input_tokens':120,'output_tokens':8}},
          {'kind':'receipt','id':'summary','model_metadata':{
              'luna_test':{'request_id':'request-1'},
              'usage':{'input_tokens':120,'output_tokens':8}}}]
    result=audit(rows)
    assert result['model_receipts_explicitly_attempted']==1
    assert result['rejected_or_error_receipts']=={'dossier_model_error':1}
    assert result['metered_receipts']==1
    assert result['provider_usage']=={'input_tokens':120,'output_tokens':8}
