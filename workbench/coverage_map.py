"""Collection and parser coverage are separate from AI lead review."""
from collections import Counter, defaultdict
import json
import stat

SOURCE_AREAS={
    'authentication':('/var/log/secure','/var/log/auth','/.ssh/'),
    'accounts_privilege':('/etc/passwd','/etc/group','/etc/sudo','/etc/pam','/etc/security'),
    'persistence':('/etc/cron','/var/spool/','/etc/systemd/','/etc/init','/etc/rc'),
    'execution':('/var/log/audit/','history','/bin/','/sbin/'),
    'network':('/var/log/','/etc/hosts','/etc/resolv','/etc/sysconfig/network'),
    'applications':('/opt/','/srv/','/var/www/','/var/lib/'),
}


def source_matrix(inventory, files, scopes, errors):
    """Inventory-derived scope exists even when no detector produced a lead."""
    processed={(f['partition_offset'],f['path']):f for f in files}
    rows={area:Counter() for area in (*SOURCE_AREAS,'unclassified')}
    with inventory.open(encoding='utf-8') as stream:
        for line in stream:
            item=json.loads(line)
            if not stat.S_ISREG(item['mode']):continue
            areas=[area for area,patterns in SOURCE_AREAS.items() if any(p in item['path'] for p in patterns)] or ['unclassified']
            work=processed.get((item['partition_offset'],item['path']))
            for area in areas:
                row=rows[area];row['discovered']+=1
                row[work['status'] if work else 'not_selected']+=1
    incomplete=bool(errors) or any(s.get('status')!='read_only' for s in scopes)
    return [{'area':area,**counts,'status':'enumeration_incomplete' if incomplete else 'not_observed_in_enumerated_scope' if not counts else 'scope_recorded',
        'check':'bounded source scan and source review; not all events or actions',
        'support':'allocated Linux XFS/ext; text and selected static formats',
        'absence_is_refutation':False} for area,counts in rows.items()]+[
        {'area':area,'status':'unsupported','absence_is_refutation':False} for area in ('memory','deleted_unallocated','journal_packet_semantics','windows_evidence_parsers')]


def critical(path):
    return path.startswith(('/etc/ssh/','/etc/cron','/var/spool/cron/','/var/log/secure','/var/log/auth',
        '/etc/systemd/','/etc/rc','/etc/pam.d/','/root/.ssh/')) or path in ('/etc/passwd','/etc/sudoers','/etc/ld.so.preload')


def summarize(files, sources):
    retained={(s.get('partition_offset'),s.get('path')) for s in sources if s.get('relative_path')}
    families=defaultdict(Counter);gaps=[]
    for f in files:
        family=f.get('category','other');c=families[family];c['discovered']+=1
        c['read']+=bool(f.get('bytes_scanned'));c['fully_scanned']+=f['status']=='scanned'
        c['retained']+=(f.get('partition_offset'),f['path']) in retained
        c['text_parsed']+=bool(f.get('text_stream_parsed'))
        c['unprocessed']+=f['status'] in ('deferred','pending','error','format_unparsed')
        if critical(f['path']) and f['status']!='scanned':
            gaps.append({k:f.get(k) for k in ('path','partition_offset','status','reason','bytes_scanned','size')})
    return {'families':dict(families),'critical_gaps':sorted(gaps,key=lambda f:(f['path'],str(f['partition_offset'])))[:100],
        'critical_gap_count':len(gaps),'scope':'Candidate allocated files. Read, retained, parsed and AI-reviewed are distinct. A parsed file may contain unrecognized events.',
        'time_coverage':'Only parsed event timestamps are indexed; original gaps cannot be distinguished from missing collection without independent retention records.',
        'unsupported':['deleted/unallocated','memory/swap','unsupported binary event formats','other mounted systems not included']}


def gap_checks(observations, maximum=8):
    seen=set();calls=[]
    for o in observations:
        if o['type']!='linux_environment':continue
        for gap in o['fields'].get('coverage_map',{}).get('critical_gaps',[]):
            path=gap['path']
            identity=(gap.get('partition_offset'),path)
            if identity in seen or gap['status']=='format_unparsed':continue
            seen.add(identity)
            calls.append({'tool':'read_file','path':path,'partition_offset':gap.get('partition_offset'),'byte_offset':gap.get('bytes_scanned') or 0,
                'byte_length':8192,'reason':'중요 자료 미처리 구간 보완. 전체 파일 처리나 부재 확인을 뜻하지 않음.'})
            if len(calls)>=maximum:return calls
    return calls
