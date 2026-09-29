"""Bounded, source-diverse review admission; deferred facts remain in the ledger."""
from collections import defaultdict


def family(rule,path,priority):
    if rule=='extra_uid_zero' or path.startswith(('/etc/ssh/','/etc/pam.d/','/etc/sudoers','/etc/security/')) or path in ('/etc/passwd','/etc/group'):
        return 'access'
    if rule in ('writable_persistence','preload_config','preload_environment') or path.startswith(('/etc/cron','/etc/systemd/','/etc/rc','/etc/ld.so','/etc/profile')):
        return 'persistence'
    if rule.startswith('ELF_') or rule in ('download_execute','reverse_shell','webshell_code') or rule=='package_digest_mismatch' and priority==0:
        return 'execution'
    return 'general_changes' if rule=='package_digest_mismatch' else 'other'


def schedule(dossiers,limit=96,general_limit=24,questions=()):
    selected=[]
    queues=defaultdict(list)
    unresolved=[q for q in questions if q.get('status') not in ('superseded','scoped_answered')]
    def question_rank(d):
        refs=set(d.get('observation_ids',[]))
        linked=[q for q in unresolved if refs.intersection(q.get('observation_ids',[])+q.get('related_observation_ids',[]))]
        return (not any(q.get('source_kind')=='objection' for q in linked),
                not any(q.get('investigation_priority')=='high' for q in linked),not bool(linked),
                d.get('review_priority',99),d.get('group_key',''))
    # Question-linked evidence first, while retaining a bounded source-coverage
    # reserve. Category round-robin is no longer the first admission decision.
    linked=sorted((d for d in dossiers if question_rank(d)[2] is False),key=question_rank)
    selected.extend(linked[:max(0,limit-limit//4)])
    selected_ids={d['id'] for d in selected}
    for d in dossiers:
        if d['id'] in selected_ids:continue
        name=d.get('review_family','baseline' if d['baseline'] else 'other')
        # Legacy small baselines keep their admission priority. New split
        # baselines share the family budget instead of bypassing the hard cap.
        if name=='baseline' and d['baseline'] and len(selected)<limit:selected.append(d)
        else:queues[name if name in ('access','persistence','execution','general_changes') else 'other'].append(d)
    for queue in queues.values():
        queue.sort(key=lambda d:(bool(d.get('previously_reviewed')),0 if d.get('deferred_since') else 1,d.get('deferred_since') or '',d['review_priority'],d['group_key']))
    order=('access','persistence','execution','other','general_changes')
    admitted=defaultdict(int)
    # Reserve up to 12 slots for each source family before distributing extras.
    for _ in range(12):
        for name in order:
            if queues[name] and len(selected)<limit:
                selected.append(queues[name].pop(0));admitted[name]+=1
    while len(selected)<limit:
        progressed=False
        for name in order:
            if not queues[name] or name=='general_changes' and admitted[name]>=general_limit:continue
            selected.append(queues[name].pop(0));admitted[name]+=1;progressed=True
            if len(selected)>=limit:break
        if not progressed:break
    return selected
