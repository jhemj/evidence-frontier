"""Collection and parser coverage are separate from AI lead review."""
from collections import Counter, defaultdict


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
            if path in seen or gap['status']=='format_unparsed':continue
            seen.add(path)
            calls.append({'tool':'read_file','path':path,'byte_offset':gap.get('bytes_scanned') or 0,
                'byte_length':8192,'reason':'중요 자료 미처리 구간 보완. 전체 파일 처리나 부재 확인을 뜻하지 않음.'})
            if len(calls)>=maximum:return calls
    return calls
