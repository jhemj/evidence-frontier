"""Hard-bound final model input while keeping each available source identifiable."""
import json


class InputBudgetError(ValueError):
    """The immutable evidence view cannot fit without paging or reselection."""


def serialize(value):
    """Use the same lossless representation for budget checks and model input."""
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def bounded(value, text=800, items=6):
    if isinstance(value,str):return value[:text]
    if isinstance(value,list):return [bounded(x,text,items) for x in value[:items]]
    if isinstance(value,dict):
        # A continuation request is a source/search-bound request object, not
        # explanatory prose. Never recursively shorten its cursor, path or
        # limit: a malformed displayed cursor invites an invalid retry. If a
        # caller cannot afford the exact object it must omit it entirely.
        return {k:(v if k == 'continuation_request' and isinstance(v,dict)
                   else bounded(v,text,items)) for k,v in value.items()}
    return value


def source_fields(fields, length, items=5):
    """Bound displayed content by serialized size; keep identity and time exact."""
    from .investigation import serialized_text_prefix
    from .temporal import ALIASES
    protected={'path','target_path','absolute','artifact_path','source_sha256','original_source_sha256','source_complete','source_member','json_pointer',
        'source_row','source_location','source_offset','byte_offset','image_file_byte_offset','byte_length',
        'source_range_start','line','inode','partition_offset','os_instance','volume_id','snapshot_id',
        'locator_basis','time_record','time_basis','time_kind','time_type','file_context'}
    protected.update(k for keys in ALIASES.values() for k in keys)
    omissions=dict(fields.get('context_omissions',{}))
    def content(value,key=None,pointer=''):
        if key in protected:return value
        if isinstance(value,str):
            result=serialized_text_prefix(value,length)
            if result!=value:omissions[pointer]={'display':'text_prefix','full_value':'retained source; not fully presented'}
            return result
        if isinstance(value,list):
            if len(value)>items:
                original=max(len(value),omissions.get(pointer,{}).get('input_items',0))
                omissions[pointer]={'display':'list_prefix','input_items':original,'shown_items':items,'omitted_items':original-items}
            return [x if key=='referenced_paths' and isinstance(x,str) else content(x,pointer=pointer+'/'+str(i)) for i,x in enumerate(value[:items])]
        if isinstance(value,dict):return {k:content(v,k,pointer+'/'+k.replace('~','~0').replace('/','~1')) for k,v in value.items()}
        return value
    result={k:content(v,k,'/'+k) for k,v in fields.items() if k!='context_omissions'}
    if omissions:result['context_omissions']=omissions
    if result!=fields:
        result['context_compacted']=True
        if result.get('excerpt')!=fields.get('excerpt'):result['excerpt_truncated']=True
    return result


def share_metadata(pack):
    """Factor identical explanatory metadata, without moving source identity.

    Defaults apply to every observation only when all originally had the exact
    same value. Source fields, raw times, precision and origins stay inline.
    This transformation is reversible and is not sampling or summarization.
    """
    from copy import deepcopy
    rows=pack.get('observations',[])
    if len(rows)<2 or pack.get('shared_observation_metadata'):return
    original=deepcopy(pack);shared={}
    for path in (('context_limit',),('time_semantics','kind_basis'),('time_semantics','timezone_basis')):
        parents=[o if len(path)==1 else o.get(path[0],{}) for o in rows]
        key=path[-1]
        if not all(isinstance(p,dict) and key in p for p in parents):continue
        value=parents[0][key]
        if len(serialize(value))<24 or any(p[key]!=value for p in parents):continue
        shared['/'.join(path)]=value
        for parent in parents:del parent[key]
    if not shared:return
    pack['shared_observation_metadata']=shared
    pack['shared_observation_metadata_scope']='Lossless defaults: each slash-delimited path/value applies to EVERY observation. Restore these exact values before reading its metadata. Raw times, source identity and fields remain inline. No source independence or completeness inferred.'
    if len(serialize(pack))>=len(serialize(original)):
        pack.clear();pack.update(original)


def expand_metadata(pack):
    from copy import deepcopy
    definitions={d.get('ref'):d.get('request') for d in pack.get('observation_context_request_definitions',[])}
    for row in pack.get('observations',[]):
        ref=row.pop('context_request_ref',None)
        if ref in definitions:row['context_request']=deepcopy(definitions[ref])
    expand_observation_fields(pack)
    expand_contract_conditions(pack)
    pack.pop('observation_context_request_definitions',None)
    pack.pop('observation_context_request_scope',None)
    for o in pack.get('observations',[]):
        for path,value in pack.get('shared_observation_metadata',{}).items():
            keys=path.split('/');parent=o
            for key in keys[:-1]:parent=parent.setdefault(key,{})
            if keys[-1] in parent:raise ValueError('Conflicting shared observation metadata')
            parent[keys[-1]]=deepcopy(value)
    pack.pop('shared_observation_metadata',None)
    pack.pop('shared_observation_metadata_scope',None)


def share_observation_fields(pack, minimum=3):
    """Factor repeated non-locator field values without losing provenance.

    Dossier packs can contain many copies of the same interpretation guard or
    source context. The value is removed only from rows whose exact ID is listed
    in the default entry; locators, hashes, timestamps and citation fields stay
    inline. A consumer can restore the original row fields with
    ``expand_observation_fields``.
    """
    if pack.get('shared_observation_field_defaults') is not None:return
    from collections import Counter
    # Locator-bearing coordinates remain inline. Other exact scalar metadata
    # may be moved to an ID-keyed dictionary when repeated; no provenance is
    # discarded and every value can be restored deterministically.
    protected={'path','target_path','absolute','source_sha256','original_source_sha256',
        'source_offset','byte_offset','image_file_byte_offset',
        'byte_length','source_range_start','line','inode','partition_offset','timestamp',
        'time_basis','time_kind','time_type','locator_basis','excerpt','command'}
    rows=pack.get('observations',[]); defaults=[]
    keys={key for row in rows for key in row.get('fields',{}) if key not in protected}
    for key in sorted(keys):
        values=[row.get('fields',{}).get(key) for row in rows if key in row.get('fields',{})]
        if not values or not all(isinstance(value,(str,int,float,bool,type(None))) for value in values):continue
        value,count=Counter(json.dumps(value,ensure_ascii=False,sort_keys=True) for value in values).most_common(1)[0]
        if count<minimum or len(value)<24:continue
        decoded=json.loads(value); ids=[row['id'] for row in rows if row.get('fields',{}).get(key)==decoded]
        if len(serialize(decoded)*len(ids)) <= len(serialize(decoded))+len(ids)*12:continue
        for row in rows:
            if row.get('id') in ids:row.get('fields',{}).pop(key,None)
        row_index={row.get('id'):index for index,row in enumerate(rows)}
        defaults.append({'field':key,'value':decoded,
                         'observation_indexes':sorted(row_index[oid] for oid in ids)})
    if defaults:
        pack['shared_observation_field_defaults']=defaults
        pack['shared_observation_field_defaults_scope']=('Each default applies only to its zero-based observation_indexes in the observations array; '
            'restore before interpreting fields. This is lossless metadata factoring, not evidence omission.')


def expand_observation_fields(pack):
    from copy import deepcopy
    rows={row.get('id'):row for row in pack.get('observations',[])}
    for entry in pack.get('shared_observation_field_defaults',[]):
        indexes=entry.get('observation_indexes',[])
        for index in indexes:
            if isinstance(index,int) and 0<=index<len(pack.get('observations',[])):
                pack['observations'][index].setdefault('fields',{})[entry['field']]=deepcopy(entry.get('value'))
    pack.pop('shared_observation_field_defaults',None)
    pack.pop('shared_observation_field_defaults_scope',None)


def share_contract_conditions(pack):
    """Factor repeated contract predicates while retaining every contract ID."""
    if pack.get('contract_definitions') is not None:return
    checks=pack.get('executed_checks',[]); definitions=[]; index={}
    for check in checks:
        compact=[]
        for contract in check.get('contracts',[]):
            conditions={key:contract.get(key,'') for key in
                        ('success_condition','refutation_condition','inconclusive_condition')}
            fingerprint=serialize(conditions)
            ref=index.get(fingerprint)
            if ref is None:
                ref=len(definitions);index[fingerprint]=ref
                definitions.append({'ref':ref,**conditions})
            compact.append({'dossier_id':contract.get('dossier_id'),
                            'contract_id':contract.get('contract_id'),
                            'condition_ref':ref})
        check['contracts']=compact
    if definitions:
        pack['contract_definitions']=definitions
        pack['contract_definitions_scope']=('Each contract keeps its exact dossier_id and contract_id. '
            'condition_ref resolves to the exact success/refutation/inconclusive predicates below; no contract was dropped.')


def expand_contract_conditions(pack):
    definitions={d.get('ref'):d for d in pack.get('contract_definitions',[])}
    for check in pack.get('executed_checks',[]):
        for contract in check.get('contracts',[]):
            definition=definitions.get(contract.get('condition_ref'))
            if definition:
                contract.update({key:definition.get(key,'') for key in
                                 ('success_condition','refutation_condition','inconclusive_condition')})
            contract.pop('condition_ref',None)
    pack.pop('contract_definitions',None)
    pack.pop('contract_definitions_scope',None)


def compact_check_requests(pack):
    """Keep exact tool locators while moving explanatory request prose out of rows."""
    locator_keys={'tool','path','query','source_offset','byte_offset','byte_length','partition_offset',
                  'inode','cursor','limit','time_from','time_to','account','source_sha256'}
    omitted={}
    for check in pack.get('executed_checks',[]):
        request=check.get('request')
        if not isinstance(request,dict):continue
        dropped=sorted(key for key in request if key not in locator_keys)
        if dropped:
            check['request']={key:value for key,value in request.items() if key in locator_keys}
            omitted[check.get('id')]=dropped
    if omitted:
        pack['check_request_omissions']=omitted
        pack['check_request_omissions_scope']=('Only explanatory request fields were moved from check rows. Exact tool, '
            'path/query, byte/time/inode and source locator fields remain; contract predicates remain in contract_definitions.')


def share_observation_context_requests(pack):
    from collections import Counter
    rows=pack.get('observations',[]); values=[row.get('context_request') for row in rows]
    encoded=Counter(serialize(value) for value in values if isinstance(value,dict))
    repeated=[(raw,count) for raw,count in encoded.items() if count>=2]
    if not repeated:return
    definitions=[]; lookup={}
    for raw,count in repeated:
        ref=len(definitions);value=json.loads(raw);definitions.append({'ref':ref,'request':value});lookup[raw]=ref
    for row in rows:
        request=row.get('context_request')
        raw=serialize(request) if isinstance(request,dict) else None
        if raw in lookup:
            row.pop('context_request',None);row['context_request_ref']=lookup[raw]
    pack['observation_context_request_definitions']=definitions
    pack['observation_context_request_scope']=('context_request_ref resolves to an exact retained-source locator; '
        'no locator or byte range is discarded.')


def fit(pack, maximum=36000):
    from .structured_context import group, expand
    group(pack)
    size=lambda:len(serialize(pack))
    if size()<=maximum:return
    pack['context_budget_compacted']=True
    share_metadata(pack)
    if size()<=maximum:return
    # Planner bookkeeping can dwarf the actual selected source. Preserve the
    # source IDs and explicit omission counts while replacing verbose audit
    # rationale with a bounded, truthful summary before touching observations.
    if isinstance(pack.get('selection_audit'), dict):
        audit = pack['selection_audit']
        selected_ids = [o.get('id') for o in pack.get('observations', []) if o.get('id')]
        families = audit.get('lead_family_audit', {})
        pack['selection_audit'] = {**{key:audit[key] for key in
            ('version','strategy','available','included','omitted','not_previously_presented') if key in audit},
            'compacted': True,
            'lead_family_audit': {**{key:families[key] for key in ('available_count','included_count','omitted_count') if key in families},
                **{key+'_count':len(families[key]) for key in ('available','included','omitted') if key in families}},
            'selected_observation_ids': selected_ids,
            'selected_count': len(selected_ids),
            'selection_audit_omitted': True,
            'scope': 'Presentation only, not proof of review, independence, absence or investigation completeness. Verbose selection rationale omitted for context budget; known candidate/omitted counts and exact selected IDs retained.'
        }
    # Planner history is not original evidence. Bound its aggregate size before
    # shortening any source text. Exact requests in retained entries stay intact;
    # scheduler fingerprints still prevent replay of omitted older jobs.
    if pack.get('completed_tools'):
        original=pack['completed_tools']
        prior_omitted=pack.get('completed_tools_omitted',0)
        compact=[{**row,'result_scope':bounded(row.get('result_scope',{}),120,2),
                  'result_scope_compacted':True} for row in original]
        pack['completed_tools']=[]
        available=max(0,min(6000,maximum-size()-800))
        retained=[]
        for row in reversed(compact):
            if len(serialize([row]+retained))>available:break
            retained.insert(0,row)
        pack['completed_tools']=retained
        pack['completed_tools_total']=max(pack.get('completed_tools_total',0),len(original)+prior_omitted)
        pack['completed_tools_omitted']=prior_omitted+len(original)-len(retained)
        pack['completed_tools_scope']=('Bounded recent tool history. Omitted jobs remain in the ledger; no inference of '
            'unperformed checks or negative results. Any displayed continuation_request is preserved verbatim; if its '
            'entry is omitted, no continuation is presented.')
        if size()<=maximum:return
    if pack.get('previous_assessment'):
        old=pack['previous_assessment']
        pack['previous_assessment']={'summary':old.get('summary','')[:500],
            'findings':[{k:(bounded(v,240,8) if k!='stages' else [
                {field:bounded(stage.get(field),240,8) for field in ('stage','judgment','statement','observation_ids')
                 if field in stage} for stage in v[:4]])
                for k,v in f.items() if k in ('dossier_id','title','judgment','reason','observation_ids','stages')}
                for f in old.get('findings',[])]}
    deferred=pack.get('deferred_checks',[])
    pack['deferred_checks_total']=len(deferred)
    pack['deferred_checks']=bounded(deferred[:8],400,8)
    # Keep the model view bounded by factoring repeated, exact metadata before
    # shortening source text. IDs, locators, timestamps and conditions remain
    # recoverable through their explicit dictionaries.
    share_contract_conditions(pack)
    compact_check_requests(pack)
    share_observation_context_requests(pack)
    share_observation_fields(pack)
    if size()<=maximum:return
    for check in pack.get('executed_checks',[]):
        scope=check.get('scope',{})
        check['scope']={key:scope[key] for key in ('status','complete','truncated','error') if key in scope}
    # Dictionary payloads must never be truncated while claiming reversibility.
    if size()>maximum:expand(pack)
    # IDs, source location, source_origin, context requests and citation allowlists
    # remain intact. Shorten content only, never replace a source with another.
    for length,items in ((2000,5),(1000,5),(500,5),(250,5),(128,2)):
        if size()<=maximum:return
        for observation in pack['observations']:
            fields=observation['fields']
            observation['fields']=source_fields(fields,length,items)
    if size()>maximum:
        largest=sorted(((key,len(serialize(value))) for key,value in pack.items()),key=lambda row:row[1],reverse=True)[:4]
        raise InputBudgetError('최종 근거 입력 예산을 초과했습니다. 원문/인용 범위를 축소해 재검토해야 합니다. '+
                         '항목별 문자 수: '+', '.join(f'{key}={length}' for key,length in largest))
