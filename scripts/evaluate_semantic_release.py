#!/usr/bin/env python3
"""Evaluate semantic release contracts on synthetic packs.

Default mode is deterministic and local: it checks source binding, scope and
uncertainty contracts, but does not claim to measure model semantic quality.
With ``--live --base-url URL --model NAME`` it uses the repository's normal
Provider/consult/JudgmentReport path against an explicitly supplied endpoint.
Only counts, validation codes and bounded output summaries are written; raw
prompts, raw model output and credentials are never printed.
"""
import argparse
import json
from pathlib import Path
import sys
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_cases(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def pack_for(case):
    rows=[]
    for row in case['observations']:
        rows.append({'id':row['id'], 'type':row['type'], 'timestamp':row.get('timestamp'),
                     'source_location':f"fixture:{row['id']}", 'fields':dict(row['fields'])})
    ids=[row['id'] for row in rows]
    from workbench.semantic_contract import source_facts
    return {'target_os':'linux','observations':rows,'case_question':case['question'],
            'literal_fact_candidates':source_facts(rows),
            'required_dossiers':[{'id':case['id'],'title':case['question'],'observation_ids':ids}],
            'allowed_observation_ids':ids,
            'allowed_observation_ids_by_dossier':{case['id']:ids},
            'review_mode':'blind_source_review','selection_is_partial':False,
            'finalization_allowed':False}


def deterministic_checks(case, pack):
    expected=case.get('expected',{}); ids={o['id'] for o in pack['observations']}; issues=[]
    if len(ids)!=len(pack['observations']): issues.append('duplicate_observation_id')
    missing=set(expected.get('must_cite',[]))-ids
    if missing: issues.append('fixture_missing_expected_ids')
    if any(not o.get('fields',{}).get('path') for o in pack['observations']): issues.append('missing_locator')
    if pack['finalization_allowed']: issues.append('unsafe_finalization_default')
    expected = case.get('expected', {})
    for row in pack['observations']:
        fields = row.get('fields', {})
        if expected.get('requires_partial_scope') and not (fields.get('source_complete') is False or fields.get('excerpt_truncated')):
            issues.append('fixture_missing_partial_scope_marker')
    if expected.get('requires_time_uncertainty'):
        for row in pack['observations']:
            fields=row.get('fields', {})
            if row.get('timestamp') is not None or fields.get('time_kind') not in ('inferred','unknown'):
                issues.append('fixture_missing_time_uncertainty')
    for o in pack['observations']:
        if 'IGNORE PREVIOUS' in o.get('fields',{}).get('excerpt','').upper() and o['id'] not in ids:
            issues.append('prompt_injection_not_data_bound')
    return {'passed':not issues,'issues':issues,'observation_count':len(ids),
            'expected_semantics_fields':sorted(expected)}


def _semantic_contract(case, output, pack):
    from workbench.review_validation import errors, check_errors
    from workbench.models import JudgmentReport
    ids={o['id'] for o in pack['observations']}; observations={o['id']:o for o in pack['observations']}
    report=JudgmentReport.model_validate(output)
    issues=errors(report.model_dump(),[case['id']],ids,{case['id']:ids},observations)
    issues += check_errors(report.model_dump(),[],{case['id']:ids})
    finding=report.findings[0] if report.findings else None
    expected=case.get('expected',{})
    cited=set()
    if finding:
        cited.update(finding.observation_ids);cited.update(finding.counterevidence_ids)
        cited.update(x for stage in finding.stages for x in stage.observation_ids)
        cited.update(x.observation_id for x in finding.fact_assertions)
    for oid in expected.get('must_cite',[]):
        if oid not in cited: issues.append('missing_required_citation:'+oid)
    # The model may legitimately confirm a narrow configuration fact while
    # leaving behavior unknown. Evaluate each proof stage, not one broad label.
    for stage in finding.stages if finding else []:
        if stage.judgment in expected.get('forbidden_stage_judgments',{}).get(stage.stage,[]):
            issues.append('unsupported_stage:'+stage.stage)
    stage_names={stage.stage:stage for stage in (finding.stages if finding else [])}
    required=expected.get('required_stage')
    if required and required not in stage_names: issues.append('missing_required_stage:'+required)
    if required and expected.get('required_stage_judgment') and (required not in stage_names or stage_names[required].judgment != expected['required_stage_judgment']):
        issues.append('wrong_required_stage_judgment')
    if expected.get('must_have_remaining_check') and not ((finding and finding.remaining_checks) or report.next_checks):
        issues.append('missing_remaining_check')
    if expected.get('must_have_alternative') and not (finding and finding.alternatives):
        issues.append('missing_competing_explanation')
    if expected.get('requires_partial_scope'):
        if not (finding and (finding.remaining_checks or report.next_checks)): issues.append('partial_scope_not_disclosed')
    return issues, report


def _signature(report):
    finding=report.findings[0]
    # Optional explicit unknown stages do not constitute a semantic reversal.
    return (finding.judgment, tuple(sorted((s.stage,s.judgment) for s in finding.stages if s.judgment!='미확인')))


def live_check(case, pack, base_url, model, config_overrides=None, include_findings=False):
    from workbench.investigator import consult
    from workbench.models import ProviderConfig, JudgmentReport
    config={'protocol':'ollama','base_url':base_url,'model':model,'falsifier_model':model,
            'trusted_lan':False,'investigator':'native','investigation_strategy':'guided',
            'think':'off','num_ctx':32768,'assistant_num_ctx':16384,
            'num_predict':4000,'assistant_num_predict':2500,'temperature':0.1}
    config.update(config_overrides or {})
    normalized=ProviderConfig.model_validate(config).model_dump()
    output, receipt=consult(normalized, case['question'], pack, role='judgment')
    issues, report = _semantic_contract(case, output, pack)
    ids={o['id'] for o in pack['observations']}; cited=set()
    for finding in report.findings:
        cited.update(finding.observation_ids); cited.update(finding.counterevidence_ids)
        for stage in finding.stages:cited.update(stage.observation_ids)
    invalid=sorted(cited-ids)
    issues += ['out_of_scope_citation'] if invalid else []
    result={'passed':not issues,'issues':issues,
            'cited_observation_count':len(cited),'out_of_scope_ids':len(invalid),
            'usage':receipt.get('usage',{}),'elapsed_seconds':receipt.get('elapsed_seconds'),
            'output_findings':len(report.findings),'output_checks':len(report.check_assessments),
            'semantic_signature':_signature(report)}
    if include_findings:
        result['findings']=[{'title':f.title[:240],'judgment':f.judgment,
            'reason':f.reason[:240],'observation_count':len(f.observation_ids),
            'stages':[{'stage':s.stage,'judgment':s.judgment,'statement':s.statement,
                       'network_state':s.network_state} for s in f.stages]}
            for f in report.findings[:3]]
    return result


def evaluate(cases, *, live=False, base_url=None, model=None, config=None, repeatable=False, trusted_lan=False, include_findings=False):
    if repeatable: cases=sorted(cases,key=lambda c:c['id'])
    results=[]
    attempts=0
    for case in cases:
        pack=pack_for(case); row={'id':case['id'],'deterministic':deterministic_checks(case,pack)}
        if live:
            attempts += 1
            try:
                row['live']=live_check(case,pack,base_url,model,{**(config or {}),'trusted_lan':trusted_lan},include_findings)
            except Exception as exc:
                row['live']={'passed':False,'issues':['live_exception'],'exception_type':type(exc).__name__}
        results.append(row)
    if live:
        by_id={case['id']:row for case,row in zip(cases,results)}
        for case,row in zip(cases,results):
            parent=case.get('variant_of')
            if not parent or parent not in by_id: continue
            other=by_id[parent]
            if row.get('live',{}).get('semantic_signature') != other.get('live',{}).get('semantic_signature'):
                row.setdefault('live',{}).setdefault('issues',[]).append('semantic_order_variant_drift')
                row['live']['passed']=False
    return {'version':'semantic-release-1','mode':'live' if live else 'deterministic_contract_only',
            'semantic_quality_claim':False,'cases':results,
            'passed':all(r['deterministic']['passed'] and (not live or r['live']['passed']) for r in results),
            'model_call_attempts':attempts,'model_calls':attempts}


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--cases',default=str(Path(__file__).resolve().parents[1]/'tests/fixtures/semantic_release_cases.json'))
    p.add_argument('--output',required=True)
    p.add_argument('--live',action='store_true');p.add_argument('--base-url');p.add_argument('--model')
    p.add_argument('--repeatable',action='store_true');p.add_argument('--trusted-lan',action='store_true')
    p.add_argument('--include-findings',action='store_true',help='include bounded synthetic-safe finding summaries')
    p.add_argument('--case',action='append',dest='selected_cases',help='run only selected fixture IDs')
    args=p.parse_args(argv)
    if args.live and (not args.base_url or not args.model):p.error('--live requires --base-url and --model')
    cases=load_cases(args.cases)
    if args.selected_cases:cases=[c for c in cases if c['id'] in args.selected_cases]
    if not cases:p.error('no matching cases')
    result=evaluate(cases,live=args.live,base_url=args.base_url,model=args.model,
                    repeatable=args.repeatable,trusted_lan=args.trusted_lan,include_findings=args.include_findings)
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'passed':result['passed'],'model_calls':result['model_calls']}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
