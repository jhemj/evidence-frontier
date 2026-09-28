"""Deterministic, source-diverse selection; ranking is never a verdict."""
from collections import deque
import json
from .retrieval import source_origin

VERSION = 'hypothesis-frontier-selection-2'


def stable_observation_key(item):
    """Sort without depending on the store-assigned observation id."""
    fields = item.get('fields', {})
    return (
        item.get('type', ''), source_origin({**item, 'evidence_id': None}),
        # Registry IDs vary across cases; optional/mixed-type coordinates must
        # remain totally orderable. This ordering does not assert chronology.
        json.dumps([fields.get(key) for key in ('partition_offset', 'inode', 'path',
            'line', 'byte_offset', 'source_sha256')] + [item.get('timestamp')],
            ensure_ascii=False, default=str),
        json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str),
    )


def round_robin(groups):
    queues=deque(deque(items) for items in groups if items)
    while queues:
        queue=queues.popleft()
        yield queue.popleft()
        if queue:queues.append(queue)


def diverse(items):
    buckets = {}
    for item in items:
        buckets.setdefault(item['type'], {}).setdefault(source_origin(item), []).append(item)
    by_type = [list(round_robin(sources.values())) for sources in buckets.values()]
    return list(round_robin(by_type))


def lead_families(items):
    """Interleave unpresented rule families before the broad baseline lane."""
    groups = {}
    for item in items:
        rule_id = item.get('fields', {}).get('rule_id')
        if rule_id:
            groups.setdefault(rule_id, []).append(item)
    # One representative (with source-diverse choice) per rule family prevents
    # a high-volume detector from filling the presentation budget before a
    # sparse, unreviewed family is seen. Keep this lane bounded independently
    # of the total observation count.
    queues = [diverse(group)[:1] for _, group in sorted(groups.items())][:32]
    return list(round_robin(queues))


def related_sources(observations, seeds, presented=()):
    """Bounded literal-path neighbours, not a causal or independent-source join.

    Bring retained invocation context alongside a configuration/static lead.
    No case-specific path, detector, or maliciousness ranking is involved.
    """
    from .evidence_semantics import observation_time
    def scope(o):
        f=o.get('fields',{})
        return (o.get('evidence_id'),) + tuple(
            f.get(k) for k in ('partition_offset','os_instance','volume_id','snapshot_id'))
    def paths(o):
        f=o.get('fields',{});values=[f.get('path')]
        refs=f.get('referenced_paths',[])
        if isinstance(refs,list):
            values.extend(r.get('absolute') if isinstance(r,dict) else r for r in refs)
        # Preserve exact paths; no inferred drive mapping, symlink or casefold.
        return {p for p in values if isinstance(p,str) and 4<=len(p)<=1024}
    index={};by_id={o['id']:o for o in observations};seen=set(presented)
    for o in observations:
        for path in paths(o):index.setdefault((scope(o),path),[]).append(o)
    candidates={}
    for seed in seeds[:32]:
        for path in sorted(paths(seed)):
            matches=index.get((scope(seed),path),[])
            # Suppress ubiquitous destinations by frequency, not a whitelist.
            if len(matches)>max(50,len(observations)//10):continue
            for other in matches:
                if other['id']==seed['id'] or source_origin(other)==source_origin(seed):continue
                if other.get('type')==seed.get('type'):continue
                rank=(observation_time(other)['time_kind']!='occurred',
                      other['id'] in seen,len(matches),stable_observation_key(other))
                old=candidates.get(other['id'])
                if old is None or rank<old:candidates[other['id']]=rank
    return [by_id[oid] for oid in sorted(candidates,key=candidates.get)[:3]]


def order(observations, hypotheses=(), preferred=(), presented=(), question=''):
    """Interleave requested, contrary, unpresented-relevant and baseline sources.

    No maliciousness score and no candidate/model text becomes evidence. Explicit
    contrary references are merely retrieval hints, not verified refutations.
    """
    by_id = {o['id']:o for o in observations}
    seen = set(presented)
    types = {t for h in hypotheses for t in h.get('expected_source_types',[])}
    contrary = {i for h in hypotheses for i in h.get('refuting_evidence_ids',[])}
    supporting = {i for h in hypotheses for i in h.get('supporting_evidence_ids',[])}
    terms = [t.casefold() for t in question.split() if len(t)>2][:20]
    # Only original path/excerpt/command are used for literal question hints.
    def question_hit(o):
        f=o.get('fields',{})
        text=' '.join(str(f.get(k,''))[:6000] for k in ('path','excerpt','command')).casefold()
        return any(t in text for t in terms)
    stable = sorted(observations,key=stable_observation_key)
    relevant = [o for o in stable if o['type'] in types or question_hit(o)]
    leads=lead_families([o for o in stable if o['id'] not in seen])
    requested=[by_id[i] for i in dict.fromkeys(preferred) if i in by_id]
    lanes = [
        ('requested_or_recent_result', requested),
        ('contrary_reference_unverified', diverse([o for o in stable if o['id'] in contrary])),
        ('literal_path_context_not_corroboration', related_sources(stable,requested[:8]+leads,presented)),
        ('unpresented_hypothesis_source', diverse([o for o in relevant if o['id'] not in seen])),
        ('unpresented_lead_family', leads),
        ('support_reference_unverified', diverse([o for o in stable if o['id'] in supporting])),
        ('unpresented_other_source', diverse([o for o in stable if o['id'] not in seen])),
        ('hypothesis_context', diverse(relevant)),
        ('baseline_context', diverse(stable)),
    ]
    queues=[(reason,deque(items)) for reason,items in lanes]
    used=set();result=[];reasons={}
    while any(q for _,q in queues):
        for reason,queue in queues:
            while queue and queue[0]['id'] in used:queue.popleft()
            if not queue:continue
            if reason in ('unpresented_lead_family','literal_path_context_not_corroboration'):
                # This bounded lane is the fairness barrier: expose each
                # unseen family before broad lanes can consume the budget.
                while queue:
                    item=queue.popleft()
                    if item['id'] in used:continue
                    used.add(item['id']);result.append(item);reasons[item['id']]=reason
                continue
            item=queue.popleft();used.add(item['id']);result.append(item);reasons[item['id']]=reason
    return result,reasons


def audit(observations, selected, reasons, hypotheses=(), presented=(), strategy='guided'):
    ids={o['id'] for o in selected};seen=set(presented)
    available_families={o.get('fields',{}).get('rule_id') for o in observations if o.get('fields',{}).get('rule_id')}
    selected_families={o.get('fields',{}).get('rule_id') for o in selected if o.get('fields',{}).get('rule_id')}
    omitted_families=available_families-selected_families
    return {'version':VERSION,'strategy':strategy,'available':len(observations),'included':len(ids),
        'omitted':len(observations)-len(ids),'not_previously_presented':len(ids-seen),
        'lead_family_audit':{'available':sorted(available_families),'included':sorted(selected_families),
                             'omitted':sorted(omitted_families),
                             'omitted_count':len(omitted_families)},
        'selected':[{'id':o['id'],'reason':reasons.get(o['id'],'baseline_rank')} for o in selected],
        'hypotheses':[{'id':h.get('id'),'number':h.get('number'),
            'available_sources':sum(o['type'] in h.get('expected_source_types',[]) for o in observations),
            'included_sources':sum(o['type'] in h.get('expected_source_types',[]) for o in selected)} for h in hypotheses],
        'scope':'Presentation only, not proof of review, independence, absence or investigation completeness.'}
