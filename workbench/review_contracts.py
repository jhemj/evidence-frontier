"""Logical hypothesis tests stay separate even when they share a physical job."""
import hashlib
import json

REVIEW_BUDGET={'normal_model_attempts':576,'total_model_attempts':582,'reserved_repair_model_attempts':6,
               'normal_tool_jobs':72,'total_tool_jobs':76,'reserved_repair_tool_jobs':4}


def contract(call):
    conditions={k:call.get(k,'') for k in ('success_condition','refutation_condition','inconclusive_condition')}
    from .test_contract_v2 import is_v2,digest
    if is_v2(call.get('test_design')):
        from .models import TestDesignV2
        design=TestDesignV2.model_validate(call['test_design']).model_dump()
        owner=call.get('hypothesis_id') or design['owner_ref']['id']
        payload={'contract_version':2,'owner':owner,'question_id':call.get('question_id'),'test_design':design,'conditions':conditions}
        lineage=call.get('_controller_owner_lineage')
        if lineage:payload['controller_owner_lineage']=lineage
        fingerprint=digest(payload)
        result={'dossier_id':owner,'contract_id':fingerprint,
                'contract_version':2,'question_id':call.get('question_id'),'test_design':design,**conditions}
        if lineage:result['controller_owner_lineage']=lineage
        return result
    fingerprint=hashlib.sha256(json.dumps(conditions,sort_keys=True).encode()).hexdigest()
    return {'dossier_id':call['hypothesis_id'],'contract_id':fingerprint,**conditions}


def contracts(job):
    return job.get('contracts') or ([contract(job['request'])] if job.get('request',{}).get('hypothesis_id') else [])


def attach(existing, call):
    result=contracts(existing);new=contract(call)
    if new not in result:result.append(new)
    return result


def model_exhausted(count,repair):
    return count>=REVIEW_BUDGET['total_model_attempts'] or (not repair and count>=REVIEW_BUDGET['normal_model_attempts'])


def model_attempts(store,cid,task):
    # Final synthesis uses the SAME lifetime review budget, including failures.
    return sum(r.get('task_id')==task['id'] for kind in ('review_input','synthesis_input') for r in store.list(kind,cid))


def review_exhausted(store,cid,task,extra=0):
    repair=task.get('repair_generation')==task.get('retry_generation',0)
    reserve=20 if task.get('review_policy')=='autonomous-v1' and not repair else 0
    return model_exhausted(model_attempts(store,cid,task)+extra+reserve,repair)


def tools_exhausted(lifetime,current,repair):
    return lifetime>=REVIEW_BUDGET['total_tool_jobs'] or (current>=4 if repair else lifetime>=REVIEW_BUDGET['normal_tool_jobs'])
