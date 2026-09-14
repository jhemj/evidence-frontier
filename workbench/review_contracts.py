"""Logical hypothesis tests stay separate even when they share a physical job."""
import hashlib
import json

REVIEW_BUDGET={'normal_model_attempts':576,'total_model_attempts':582,'reserved_repair_model_attempts':6,
               'normal_tool_jobs':72,'total_tool_jobs':76,'reserved_repair_tool_jobs':4}


def contract(call):
    conditions={k:call.get(k,'') for k in ('success_condition','refutation_condition','inconclusive_condition')}
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


def tools_exhausted(lifetime,current,repair):
    return lifetime>=REVIEW_BUDGET['total_tool_jobs'] or (current>=4 if repair else lifetime>=REVIEW_BUDGET['normal_tool_jobs'])
