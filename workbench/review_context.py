"""Hard-bound final model input while keeping each available source identifiable."""
import json


class InputBudgetError(ValueError):
    """The immutable evidence view cannot fit without paging or reselection."""


def serialize(value):
    """Use the same lossless representation for budget checks and model input."""
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def model_view_size(pack):
    """Characters actually sent for the pack after exact reference projection.

    The canonical ledger retains full identifiers. Provider uses this same
    reversible transport codec; full-message token accounting is separate.
    """
    from .model_references import ReferenceProjection
    from .model_tables import encode
    from .model_text import encode as encode_text
    return len(serialize(encode(encode_text(ReferenceProjection(pack).encode(pack)))))


def model_view_breakdown(pack):
    """Measure the SAME transport, not raw bytes, tokens, or semantic progress.

    Per-key values exclude their key/delimiter overhead; the residual includes
    that overhead and lossless transport manifests. Values are not marginal
    costs: sharing dependencies can change when a source selection changes.
    No source text or model narrative is copied into these diagnostics.
    """
    from .model_references import ReferenceProjection
    from .model_tables import encode
    from .model_text import encode as encode_text
    projected=encode(encode_text(ReferenceProjection(pack).encode(pack)))
    components={key:len(serialize(value)) for key,value in projected.items()
                if key in ('observations','page_review_notes','executed_checks',
                           'question_context','open_objections','literal_fact_candidates')}
    total=len(serialize(projected))
    return {'unit':'characters','transport_total':total,'components':components,
            'other_and_envelope':total-sum(components.values()),
            'cost_kind':'serialized components; not additive marginal costs or provider tokens'}


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
        'locator_basis','time_record','time_basis','time_kind','time_type','file_context',
        'first_observed_source','last_observed_source','last_record_source',
        'occurrence_summary_version','occurrences_scope'}
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
    raw_definitions=pack.get('observation_context_request_definitions',[])
    if not isinstance(raw_definitions,list):raise ValueError('Invalid observation context definitions')
    definitions={}
    for definition in raw_definitions:
        if not isinstance(definition,dict) or isinstance(definition.get('ref'),bool) or not isinstance(definition.get('ref'),int):
            raise ValueError('Invalid observation context definition')
        if definition['ref'] in definitions:
            raise ValueError('Duplicate observation context definition ref')
        if not isinstance(definition.get('request'),dict):
            raise ValueError('Invalid observation context request')
        definitions[definition['ref']]=definition.get('request')
    for row in pack.get('observations',[]):
        if 'context_request_ref' not in row:continue
        if 'context_request' in row:
            raise ValueError('Conflicting observation context request')
        ref=row.pop('context_request_ref')
        if isinstance(ref,bool) or not isinstance(ref,int) or ref not in definitions:
            raise ValueError('Invalid observation context_request_ref')
        row['context_request']=deepcopy(definitions[ref])
    expand_observation_fields(pack)
    expand_observation_path_defaults(pack)
    expand_contract_conditions(pack)
    expand_check_requests(pack)
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


_PROVENANCE_PATHS = ('time_semantics/source_origin','time_semantics/kind_basis',
    'time_semantics/timezone_basis','time_semantics/precision',
    'time_semantics/timezone_assumed','time_semantics/time_kind','context_limit')


def share_observation_path_defaults(pack):
    """Factor exact repeated provenance explanations, never raw event times."""
    from collections import Counter
    from copy import deepcopy
    if pack.get('shared_observation_path_defaults') is not None:return
    original=deepcopy(pack)
    rows=pack.get('observations',[]); defaults=[]
    for path in _PROVENANCE_PATHS:
        keys=path.split('/'); values={}
        for index,row in enumerate(rows):
            parent=row
            for key in keys[:-1]:parent=parent.get(key,{}) if isinstance(parent,dict) else {}
            if isinstance(parent,dict) and keys[-1] in parent:
                values[index]=json.dumps(parent[keys[-1]],ensure_ascii=False,sort_keys=True)
        for value,count in Counter(values.values()).items():
            if count<2:continue
            indexes=[index for index,item in values.items() if item==value]
            entry={'path':path, 'value':json.loads(value), 'observation_indexes':indexes}
            # Repeated key names cost bytes too, even for short labels/bools.
            if (len(value)+len(serialize(keys[-1]))+2)*count<=len(serialize(entry))+20:continue
            for index in indexes:
                parent=rows[index]
                for key in keys[:-1]:parent=parent[key]
                del parent[keys[-1]]
            defaults.append(entry)
    if defaults:
        pack['shared_observation_path_defaults']=defaults
        pack['shared_observation_path_defaults_scope']=(
            'Lossless provenance defaults: restore each slash-delimited path/value ONLY '
            'at its zero-based observation_indexes. Raw times and locators remain unchanged. '
            'Shared provenance is not independent corroboration.')
    if len(serialize(pack))>=len(serialize(original)):
        pack.clear();pack.update(original)


def expand_observation_path_defaults(pack):
    from copy import deepcopy
    allowed=set(_PROVENANCE_PATHS)
    for entry in pack.get('shared_observation_path_defaults',[]):
        if not isinstance(entry,dict) or entry.get('path') not in allowed:
            raise ValueError('Invalid provenance path default')
        indexes=entry.get('observation_indexes')
        if not isinstance(indexes,list):raise ValueError('Invalid provenance indexes')
        keys=entry['path'].split('/')
        for index in indexes:
            if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(pack.get('observations',[])):
                raise ValueError('Invalid provenance index')
            parent=pack['observations'][index]
            for key in keys[:-1]:parent=parent.setdefault(key,{})
            if keys[-1] in parent:raise ValueError('Conflicting provenance default')
            parent[keys[-1]]=deepcopy(entry.get('value'))
    pack.pop('shared_observation_path_defaults',None)
    pack.pop('shared_observation_path_defaults_scope',None)


def share_observation_fields(pack, minimum=3, include_repeated_identity=False):
    """Factor repeated non-locator field values without losing provenance.

    Dossier packs can contain many copies of the same interpretation guard or
    source context. Values are bound to exact zero-based observation indexes;
    locators, timestamps and citations stay inline. Hashes and parsed commands
    also stay inline unless the judgment-only identity-sharing option is used.
    A consumer can restore the original row fields with
    ``expand_observation_fields``.
    """
    if pack.get('shared_observation_field_defaults') is not None:return
    from collections import Counter
    from copy import deepcopy
    original=deepcopy(pack)
    # Locator-bearing coordinates remain inline. Other exact scalar metadata
    # may be moved to an index-bound dictionary when repeated; no provenance is
    # discarded and every value can be restored deterministically. Repeated
    # structured metadata (e.g. parsed referenced-path candidates) is factored
    # exactly too; these are not source excerpts or a semantic summary.
    protected={'path','target_path','absolute','source_sha256','original_source_sha256',
        'source_offset','byte_offset','image_file_byte_offset',
        'byte_length','source_range_start','line','inode','partition_offset','timestamp',
        'time_basis','time_kind','time_type','locator_basis','excerpt','command'}
    if include_repeated_identity:
        # Judgment views may share exact repeated content hashes and parsed
        # command values too. Every observation retains its own ID, raw excerpt,
        # time and byte locator; the table binds each value to exact row indexes.
        # This does not merge records or confer source independence.
        protected-={'source_sha256','original_source_sha256','command'}
    rows=pack.get('observations',[]); defaults=[]
    keys={key for row in rows for key in row.get('fields',{}) if key not in protected}
    for key in sorted(keys):
        values=[row.get('fields',{}).get(key) for row in rows if key in row.get('fields',{})]
        if not values:continue
        value,count=Counter(json.dumps(value,ensure_ascii=False,sort_keys=True) for value in values).most_common(1)[0]
        if count<minimum:continue
        decoded=json.loads(value); indexes=[index for index,row in enumerate(rows)
                  if key in row.get('fields',{}) and
                  json.dumps(row['fields'][key],ensure_ascii=False,sort_keys=True)==value]
        entry={'field':key,'value':decoded,'observation_indexes':sorted(indexes)}
        if (len(serialize(decoded))+len(serialize(key))+2)*len(indexes)<=len(serialize(entry))+20:continue
        for index in indexes:
            rows[index].get('fields',{}).pop(key,None)
        defaults.append(entry)
    if defaults:
        pack['shared_observation_field_defaults']=defaults
        pack['shared_observation_field_defaults_scope']=('Each default applies only to its zero-based observation_indexes in the observations array; '
            'restore before interpreting fields. This is lossless metadata factoring, not evidence omission.')
    if len(serialize(pack))>=len(serialize(original)):
        pack.clear();pack.update(original)


def expand_observation_fields(pack):
    from copy import deepcopy
    for entry in pack.get('shared_observation_field_defaults',[]):
        if not isinstance(entry,dict) or not isinstance(entry.get('field'),str):
            raise ValueError('Invalid shared observation field definition')
        indexes=entry.get('observation_indexes',[])
        if not isinstance(indexes,list):raise ValueError('Invalid observation_indexes')
        for index in indexes:
            if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(pack.get('observations',[])):
                raise ValueError('Invalid observation index')
            fields=pack['observations'][index].setdefault('fields',{})
            if entry['field'] in fields and fields[entry['field']] != entry.get('value'):
                raise ValueError('Conflicting shared observation field')
            fields[entry['field']]=deepcopy(entry.get('value'))
    pack.pop('shared_observation_field_defaults',None)
    pack.pop('shared_observation_field_defaults_scope',None)


def share_contract_conditions(pack):
    """Factor repeated contract predicates while retaining every contract ID."""
    if pack.get('contract_definitions') is not None:return
    checks=pack.get('executed_checks',[]); definitions=[]; index={}
    for check in checks:
        compact=[]
        for contract in check.get('contracts',[]):
            if not isinstance(contract,dict):
                raise ValueError('Invalid contract')
            # Preserve the complete contract payload, not merely the three
            # predicates.  This makes factoring reversible for future fields
            # (counterevidence, timeline role, provenance, and extensions).
            details={key:value for key,value in contract.items()
                     if key not in ('dossier_id','contract_id','condition_ref')}
            fingerprint=serialize(details)
            ref=index.get(fingerprint)
            if ref is None:
                ref=len(definitions);index[fingerprint]=ref
                definitions.append({'ref':ref,'fields':details})
            compact_contract={'condition_ref':ref}
            for identity in ('dossier_id','contract_id'):
                if identity in contract:
                    compact_contract[identity]=contract[identity]
            compact.append(compact_contract)
        check['contracts']=compact
    if definitions:
        pack['contract_definitions']=definitions
        pack['contract_definitions_scope']=('Each contract keeps its exact dossier_id and contract_id. '
            'condition_ref resolves to the exact success/refutation/inconclusive predicates below; no contract was dropped.')


def expand_contract_conditions(pack):
    from copy import deepcopy
    raw_definitions=pack.get('contract_definitions',[])
    if not isinstance(raw_definitions,list):raise ValueError('Invalid contract definitions')
    definitions={}
    for definition in raw_definitions:
        if not isinstance(definition,dict) or isinstance(definition.get('ref'),bool) or not isinstance(definition.get('ref'),int):
            raise ValueError('Invalid contract definition')
        if definition['ref'] in definitions:
            raise ValueError('Duplicate contract definition ref')
        definitions[definition['ref']]=definition
    for check in pack.get('executed_checks',[]):
        for contract in check.get('contracts',[]):
            if 'condition_ref' not in contract:
                continue
            ref=contract.pop('condition_ref')
            definition=definitions.get(ref)
            if isinstance(ref,bool) or not isinstance(ref,int) or not isinstance(definition,dict):
                raise ValueError('Invalid contract condition_ref')
            # Accept envelopes produced before arbitrary contract extensions
            # were preserved, while still rejecting malformed references.
            fields=definition.get('fields')
            if fields is None:
                fields={key:definition[key] for key in
                        ('success_condition','refutation_condition','inconclusive_condition')
                        if key in definition}
            if not isinstance(fields,dict):
                raise ValueError('Invalid contract definition')
            for key,value in fields.items():
                if key in contract and contract[key] != value:
                    raise ValueError('Conflicting contract field')
                contract[key]=deepcopy(value)
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
    if pack.get('observation_context_request_definitions') is not None:return
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


def share_check_requests(pack):
    """Factor complete check request objects without dropping explanatory keys."""
    if pack.get('check_request_definitions') is not None:return
    from copy import deepcopy
    definitions=[]; lookup={}
    checks=[check for check in pack.get('executed_checks',[])
            if isinstance(check.get('request'),dict)]
    counts={serialize(check['request']):0 for check in checks}
    for check in checks:counts[serialize(check['request'])]+=1
    for check in checks:
        request=check.get('request')
        if not isinstance(request,dict):continue
        fingerprint=serialize(request)
        if counts[fingerprint] < 2:continue
        ref=lookup.get(fingerprint)
        if ref is None:
            ref=len(definitions);lookup[fingerprint]=ref
            definitions.append({'ref':ref,'request':deepcopy(request)})
        check.pop('request',None);check['request_ref']=ref
    if definitions:
        pack['check_request_definitions']=definitions
        pack['check_request_definitions_scope']='request_ref resolves to the complete original request object; no request field is omitted.'


def expand_check_requests(pack):
    from copy import deepcopy
    raw_definitions=pack.get('check_request_definitions',[])
    if not isinstance(raw_definitions,list):raise ValueError('Invalid check request definitions')
    definitions={}
    for definition in raw_definitions:
        if not isinstance(definition,dict) or isinstance(definition.get('ref'),bool) or not isinstance(definition.get('ref'),int):
            raise ValueError('Invalid check request definition')
        if definition['ref'] in definitions:
            raise ValueError('Duplicate check request definition ref')
        definitions[definition['ref']]=definition
    for check in pack.get('executed_checks',[]):
        if 'request_ref' not in check:continue
        ref=check.pop('request_ref'); definition=definitions.get(ref)
        if isinstance(ref,bool) or not isinstance(ref,int) or not isinstance(definition,dict) or not isinstance(definition.get('request'),dict):
            raise ValueError('Invalid check request_ref')
        if 'request' in check and check['request'] != definition['request']:
            raise ValueError('Conflicting check request')
        check['request']=deepcopy(definition['request'])
    pack.pop('check_request_definitions',None)
    pack.pop('check_request_definitions_scope',None)


def fit_metadata_only(pack, maximum=36000):
    """Create a reversible, metadata-only model view or raise for paging.

    Unlike :func:`fit`, this never truncates, samples, or projects evidence,
    assessments, checks, or request fields.  All transformations have an
    explicit inverse (``structured_context.expand`` and ``expand_metadata``).
    """
    if isinstance(maximum,bool) or not isinstance(maximum,(int,float)) or maximum<=0:
        raise ValueError('maximum must be positive')
    from .structured_context import group
    group(pack)
    size=lambda:len(serialize(pack))
    if size()<=maximum:return
    if model_view_size(pack)<=maximum:return
    share_metadata(pack)
    share_contract_conditions(pack)
    share_observation_context_requests(pack)
    share_observation_fields(pack, include_repeated_identity=True)
    share_check_requests(pack)
    share_observation_path_defaults(pack)
    if size()<=maximum:return
    # Do not reject a lossless view merely because its retained ledger IDs are
    # longer than the handles actually sent. Source strings are unchanged.
    if model_view_size(pack)<=maximum:return
    largest=sorted(((key,len(serialize(value))) for key,value in pack.items()),
                   key=lambda row:row[1],reverse=True)[:6]
    raise InputBudgetError('Lossless metadata envelope exceeds input budget; page or reselect without dropping source fields. '
                           +'items: '+', '.join(f'{key}={length}' for key,length in largest))


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
