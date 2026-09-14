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


def schedule(dossiers,limit=96,general_limit=24):
    selected=[d for d in dossiers if d['baseline']]
    queues=defaultdict(list)
    for d in dossiers:
        if not d['baseline']:queues[d['review_family']].append(d)
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
