"""Explicitly authorized, bounded Luna replay of one immutable review input.

Reads SQLite in mode=ro, never instantiates Store on the source. No worker,
evidence execution, adoption, Git operation, or implicit model fallback.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--input-id',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--authorize-luna-test',action='store_true',required=True)
    parser.add_argument('--refresh-literal-candidates',action='store_true',
        help='Rebuild bounded hints from the immutable input presentation, without adding sources')
    parser.add_argument('--test-admission-contract',action='store_true',
        help='Present current immediate tool capabilities and validate proposed tests; no tools execute')
    args=parser.parse_args()
    if args.out.exists():raise SystemExit('Preserve prior replay; select a new output directory')
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT body FROM records WHERE id=? AND kind IN ('review_input','synthesis_input')",(args.input_id,)).fetchone()
        if not row:raise SystemExit('Immutable review input not found')
        source=json.loads(row[0]);pack=source['pack']
        role='synthesis' if source['kind']=='synthesis_input' else 'judgment'
        config=json.loads(db.execute("SELECT body FROM records WHERE kind='config' ORDER BY created_at DESC LIMIT 1").fetchone()[0])['provider']
        canonical={d['id']:d for (body,) in db.execute(
            "SELECT body FROM records WHERE kind='observation' AND case_id=?",(source['case_id'],))
            for d in [json.loads(body)]}
    from scripts.run_assisted_e2e import install_assisted_provider
    from workbench.provider import Provider
    from workbench.review_stream import resolved
    from workbench.review_validation import errors,check_errors
    from workbench.review_focus import project
    from workbench.objection_ledger import errors as objection_errors
    original_hash=hashlib.sha256(json.dumps(pack,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    candidates_before=len(pack.get('literal_fact_candidates',[]))
    if args.refresh_literal_candidates:
        from workbench.semantic_contract import source_facts
        from workbench.review_context import fit_metadata_only
        pack=resolved(pack)
        pack['literal_fact_candidates']=source_facts(pack['observations'])
        fit_metadata_only(pack,36000)
    if args.test_admission_contract:
        from workbench.test_admission import catalog
        from workbench.review_context import fit_metadata_only
        pack['tool_capabilities']=catalog(pack.get('target_os','linux'))
        fit_metadata_only(pack,36000)
    args.out.mkdir(parents=True)
    install_assisted_provider(args.out,transport_root=args.out/'transport',luna_roles=(role,))
    provider=Provider(config)
    # Any inherited Ollama network dependency is a hard replay failure.
    import httpx
    provider.client.close()
    def forbidden(request):raise AssertionError('Luna replay tried to reach Ollama')
    provider.client=httpx.Client(transport=httpx.MockTransport(forbidden))
    question=(
        '기본 점검 영역과 단서 각각을 독립적으로 검토하세요. 같은 자료의 반복을 독립 근거로 세지 마세요. '
        '가장 타당한 설명·반대 근거·확인 가능한 다음 검사를 작성하세요. 제공된 각 dossier_id당 하나의 판단이 필요합니다.')
    if role=='synthesis':
        question='현재 질문을 원문과 경쟁 설명에 대조하세요. 필요하면 판별 가능한 다음 검사를 제안하고, 가설을 사실로 전제하지 마세요. 분할 페이지는 중간 검토입니다.'
    output,receipt=provider.generate(question,pack,role=role)
    view=resolved(pack);observations={o['id']:o for o in view['observations']}
    owners=view['allowed_observation_ids_by_dossier']
    issues=errors(output,list(owners),list(observations),owners,observations,require_literals=True,
        canonical_observations=canonical)
    issues+=check_errors(output,view.get('executed_checks',[]),owners)
    issues+=objection_errors(output,pack.get('open_objections',[]),owners,
        intermediate=pack.get('review_stream',{}).get('phase','synthesis')!='synthesis')
    if role=='synthesis':
        supporting=set(output.get('supporting_evidence_ids',[]))
        refuting=set(output.get('refuting_evidence_ids',[]))
        if (supporting|refuting)-set(observations):issues.append({'code':'synthesis_citation_scope'})
        if supporting&refuting:issues.append({'code':'synthesis_conflicting_evidence_roles'})
        if any(f['judgment']!='미확인' for f in output['findings']) and not supporting:
            issues.append({'code':'synthesis_missing_support'})
        from workbench.incident_status import assessment_errors
        from workbench.scenarios import accepted
        issues+=assessment_errors(output,observations)
        try:accepted(output.get('scenario_assessment'),supporting,refuting)
        except ValueError:issues.append({'code':'scenario_assessment_invalid'})
        foreign={o['id'] for o in pack.get('open_objections',[])
            if o.get('origin_dossier_id',o['dossier_id'])!=o['dossier_id']}
        if any(a['objection_id'] in foreign and a['outcome']=='resolved'
               for a in output.get('objection_assessments',[])):
            issues.append({'code':'synthesis_cannot_resolve_foreign_objection'})
        if pack.get('final_pass') and output.get('next_checks'):
            issues.append({'code':'synthesis_assessment_budget_reserved'})
    try:project(pack,output)
    except ValueError as ex:issues.append({'code':'excerpt_selection_invalid','detail':str(ex)})
    admission=[];display=[]
    if args.test_admission_contract:
        from workbench.test_admission import assess
        admission=[assess(c,pack.get('target_os','linux'),observations) for c in output.get('next_checks',[])]
        if pack.get('review_stream',{}).get('phase','synthesis')=='synthesis' and not issues:
            from workbench.presentation_claims import bind
            display=[bind(f,observations) for f in output['findings']]
    result={'scope':'one input component replay, not E2E or a case verdict',
        'source_input_id':args.input_id,'source_database':str(args.database.resolve()),
        'source_pack_sha256':original_hash,
        'replay_pack_sha256':hashlib.sha256(json.dumps(pack,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'role':role,'refresh_literal_candidates':args.refresh_literal_candidates,
        'literal_candidates_before':candidates_before,
        'literal_candidates_after':len(pack.get('literal_fact_candidates',[])),
        'ollama_access_forbidden':True,'original_case_writes':0,
        'validation_issues':issues,'receipt':receipt,'output':output}
    result.update(test_admission=admission,display_findings=display)
    (args.out/'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('output','receipt','test_admission','display_findings')},ensure_ascii=False),flush=True)
    print(json.dumps({'model':receipt['model'],'usage':receipt['usage']},ensure_ascii=False),flush=True)
    return 1 if issues else 0


if __name__=='__main__':raise SystemExit(main())
