import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
import httpx
from .store import now
from .worker import execute, safe_path
from .provider import Provider
from .evidence_access import metadata
from .evidence_access import ExecutionUnknown

GOOD={'covered','covered_zero'}
ROOT=Path(__file__).resolve().parent.parent


class Controller:
    def __init__(self,store,evidence_root):
        self.store=store
        self.evidence_root=Path(evidence_root)
        self.wake=threading.Event()
        self.stop=threading.Event()
        self.model_lock=threading.Lock()
        from .runtime_contract import code_identity
        self.runtime_code=code_identity()

    def runtime_binding(self):
        from .runtime_contract import binding
        configs=self.store.list('config');provider=configs[-1].get('provider',{}) if configs else {}
        return binding(self.runtime_code,provider)

    def recover(self):
        with self.store.tx():
            for task in self.store.list('task'):
                if task['status']=='running':
                    resumable = task['action'] in ('linux_investigate','ai_judgment') or task.get('worker_job_key')
                    status = 'queued' if resumable else 'failed'
                    self.store.update(task['id'],status=status,error='서버 재시작. 저장된 조사 단계·작업 상태에서 계속합니다.' if resumable else '실행 중 서버가 재시작되었습니다.')
                    self.store.update(task['cell_id'],status=status)
            for case in self.store.list('case'):
                if case['status'] in ('running','pause_requested'):
                    self.store.update(case['id'],status='paused')

    def profile(self,name):
        if name not in ('standard','triage'):raise ValueError('잘못된 프로파일')
        return json.loads((ROOT/'profiles'/f'{name}.json').read_text(encoding='utf-8'))

    def create(self,name,question,profile):
        with self.store.tx():
            c=self.store.add('case','',name=name,question=question,profile=profile,status='ready',epoch_id=None)
            c=self.store.update(c['id'],case_id=c['id'])
            self.store.audit(c['id'],'case_created',profile=profile)
            return c

    def register(self,case_id,path,openrelik_file_id=None):
        c=self.store.get(case_id,'case')
        if c['status'] in ('running','pause_requested'):raise ValueError('조사를 일시정지한 뒤 증거를 추가하세요.')
        info=metadata(self.evidence_root,path)
        signature=info['signature']
        with self.store.tx():
            if self.store.get(case_id,'case')['status']=='running':raise ValueError('조사를 일시정지한 뒤 증거를 추가하세요.')
            if any(e['path']==path for e in self.store.list('evidence',case_id)):
                raise ValueError('이미 등록된 증거입니다.')
            e=self.store.add('evidence',case_id,path=path,name=info['name'],size=info['size'],signature=signature,sha256=None,openrelik_file_id=openrelik_file_id,connected=True,segment_count=info.get('segment_count',1),total_size=info.get('total_size',info['size']))
            for spec in self.profile(c['profile'])['cells']:
                cell=self.store.add('coverage',case_id,evidence_id=e['id'],status='queued',**spec)
                fingerprint=hashlib.sha256(json.dumps([path,signature,spec['action'],c['profile'],'native-v1'],sort_keys=True).encode()).hexdigest()
                task=self.store.add('task',case_id,evidence_id=e['id'],cell_id=cell['id'],action=spec['action'],label=spec['label'],phase=spec['phase'],status='queued',attempts=0,fingerprint=fingerprint,depth=0)
                self.store.db.execute('INSERT INTO fingerprints VALUES(?,?,?)',(case_id,fingerprint,task['id']))
            self.store.audit(case_id,'evidence_registered',evidence_id=e['id'])
        return e

    def connection(self,case_id,evidence_id,connected):
        # Serialize with AI requests and task claiming; no files or history are deleted.
        if not self.model_lock.acquire(blocking=False):raise ValueError('AI 응답이 끝난 뒤 증거 연결을 변경하세요.')
        try:
            with self.store.tx():
                c=self.store.get(case_id,'case');e=self.store.get(evidence_id,'evidence')
                if e['case_id']!=case_id:raise ValueError('이 사건의 증거가 아닙니다.')
                if e.get('connected',True)==connected:return e
                if any(t.get('execution_unknown') and t['evidence_id']==evidence_id for t in self.store.list('task',case_id)):
                    raise ValueError('이전 실행의 종료 여부를 확인하기 전에는 연결을 변경할 수 없습니다.')
                if c['status']=='running':raise ValueError('조사를 일시정지한 뒤 연결을 변경하세요.')
                if any(t['status']=='running' and t['evidence_id']==evidence_id for t in self.store.list('task',case_id)):
                    raise ValueError('진행 중인 증거 작업이 끝난 뒤 연결을 변경하세요. 일시정지는 다음 작업부터 적용됩니다.')
                if c['status']=='pause_requested':
                    if any(t['status']=='queued' and t.get('worker_job_key') for t in self.store.list('task',case_id)) or any(j['status']=='submitted' for j in self.store.list('investigation_job',case_id)):
                        raise ValueError('진행 중인 작업을 마무리하고 있습니다. 종료 후 연결을 변경하세요.')
                    c=self.store.update(case_id,status='paused')
                if any(evidence_id in j.get('evidence_ids',[]) and str(j['status']).lower() not in ('success','failure','failed','cancelled','canceled') for j in self.store.list('remote_job',case_id)):
                    raise ValueError('외부 작업의 종료 상태를 확인한 뒤 연결을 변경하세요.')
                if connected:
                    info=metadata(self.evidence_root,e['path'])
                    if info['signature']!=e['signature']:raise ValueError('등록 이후 증거 구성이 변경되었습니다. 새 사건에서 다시 등록하세요.')
                if c.get('epoch_id'):
                    epoch=self.store.get(c['epoch_id'],'epoch')
                    if epoch['status']=='running':self.store.update(epoch['id'],status='scope_changed',ended_at=now())
                e=self.store.update(evidence_id,connected=connected,disconnected_at=None if connected else now())
                self.store.update(case_id,status='ready',epoch_id=None)
                self.store.audit(case_id,'evidence_reconnected' if connected else 'evidence_disconnected',evidence_id=evidence_id)
                return e
        finally:self.model_lock.release()

    def active_ids(self,case_id):
        return {e['id'] for e in self.store.list('evidence',case_id) if e.get('connected',True)}

    def active_observations(self,case_id):
        active=self.active_ids(case_id)
        superseded={t['id'] for t in self.store.list('task',case_id) if t.get('superseded')}
        receipts={r['id'] for r in self.store.list('receipt',case_id) if r.get('task_id') in superseded}
        selected={o['id']:o for o in self.store.list('observation',case_id) if o['evidence_id'] in active and o.get('receipt_id') not in receipts}
        # Correlations can depend on more than one evidence source, transitively.
        while True:
            selected_ids=set(selected)
            invalid=[id for id,o in selected.items() if not set(o.get('fields',{}).get('related_observation_ids',[])).issubset(selected_ids)]
            if not invalid:return list(selected.values())
            for id in invalid:del selected[id]

    def start(self,case_id):
        with self.store.tx():
            c=self.store.get(case_id,'case')
            if c['status'] in ('running','pause_requested'):return c
            if not self.active_ids(case_id):raise ValueError('먼저 증거를 연결하세요.')
            current=self.runtime_binding()
            if c.get('runtime_binding') and c['runtime_binding']['fingerprint']!=current['fingerprint']:
                raise ValueError('조사 코드 또는 모델 조합이 변경되었습니다. 기존 기록을 보존하고 새 사건에서 시작하세요.')
            if not c.get('runtime_binding'):self.store.update(case_id,runtime_binding=current)
            self.ensure_investigation(case_id)
            if c['status']=='paused' and c.get('epoch_id'):
                epoch=self.store.get(c['epoch_id'])
            else:
                epoch=self.store.add('epoch',case_id,status='running',max_jobs=64,jobs=0,started_at=now())
            c=self.store.update(case_id,status='running',epoch_id=epoch['id'])
            self.store.audit(case_id,'epoch_started',epoch_id=epoch['id'])
        self.wake.set();return c

    def ensure_investigation(self,case_id):
        """Upgrade existing disk cases by adding content work; keep integrity receipts."""
        from .investigation import seed
        from .detection import VERSION
        for evidence in self.store.list('evidence',case_id):
            if not evidence.get('connected',True) or Path(evidence['path']).suffix.lower() not in ('.e01','.raw','.dd','.img'):continue
            seed(self,case_id,evidence['id'])
            actions={'linux_scan','linux_investigate','ai_judgment','investigation_report'}
            for old in self.store.list('task',case_id):
                if old['evidence_id']==evidence['id'] and old['action'] in actions and old.get('analysis_version')!=VERSION and not old.get('superseded'):
                    if old['status']=='running' or old.get('execution_unknown'):raise ValueError('이전 분석 작업 종료 상태를 먼저 확인해야 합니다.')
                    self.store.update(old['id'],superseded=True)
                    self.store.update(old['cell_id'],superseded=True)
            existing={t['action'] for t in self.store.list('task',case_id) if t['evidence_id']==evidence['id'] and not t.get('superseded')}
            for phase,action,label in ((5,'linux_scan','로그·계정·지속성 내용 조사'),(6,'linux_investigate','AI 추가 조사와 반대가설 검토'),(7,'ai_judgment','AI 최종 판단'),(8,'investigation_report','조사 결과와 증거 패키지')):
                if action in existing:continue
                cell=self.store.add('coverage',case_id,evidence_id=evidence['id'],status='queued',action=action,label=label,phase=phase)
                upstream=next((t for t in self.store.list('task',case_id) if t['evidence_id']==evidence['id'] and t['action']=='linux_scan' and not t.get('superseded')),None)
                revision=[upstream['id'],upstream.get('retry_generation',0)] if upstream and action!='linux_scan' else None
                fingerprint=hashlib.sha256(json.dumps([evidence['id'],evidence['signature'],action,VERSION,revision]).encode()).hexdigest()
                task=self.store.add('task',case_id,evidence_id=evidence['id'],cell_id=cell['id'],action=action,label=label,phase=phase,status='queued',attempts=0,fingerprint=fingerprint,depth=0,analysis_version=VERSION)
                self.store.db.execute('INSERT INTO fingerprints VALUES(?,?,?)',(case_id,fingerprint,task['id']))
            evidence_tasks=[t for t in self.store.list('task',case_id) if t['evidence_id']==evidence['id'] and not t.get('superseded')]
            for report_task in evidence_tasks:
                if report_task['action']=='investigation_report' and report_task['phase']!=8:
                    self.store.update(report_task['id'],phase=8)
                    self.store.update(report_task['cell_id'],phase=8)
            if any(t['action'] in ('linux_scan','linux_investigate','ai_judgment') and t['status']=='queued' for t in evidence_tasks):
                for report_task in evidence_tasks:
                    if report_task['action']=='investigation_report' and report_task['status'] in GOOD:
                        self.store.update(report_task['id'],status='queued',started_at=None)
                        self.store.update(report_task['cell_id'],status='queued')
            self.store.audit(case_id,'linux_investigation_prepared',evidence_id=evidence['id'],integrity_policy='기존 검증 영수증 보존; 매 작업 전 세그먼트 구성 변경 검사')

    def pause(self,case_id):
        with self.store.tx():
            c=self.store.get(case_id,'case')
            if c['status']=='running':
                active=any(t['status']=='running' or (t['status']=='queued' and t.get('worker_job_key')) for t in self.store.list('task',case_id)) or any(j['status']=='submitted' for j in self.store.list('investigation_job',case_id))
                self.store.update(case_id,status='pause_requested' if active else 'paused')
            self.store.audit(case_id,'pause_requested')
        return self.store.get(case_id)

    def retry(self,task_id,failed_dossiers_only=False):
        with self.store.tx():
            task=self.store.get(task_id,'task')
            if task.get('superseded'):raise ValueError('이전 스캔에 속한 작업은 재시도할 수 없습니다.')
            if task.get('execution_unknown'):raise ValueError('이전 실행의 종료 여부를 확인하기 전에는 재실행할 수 없습니다.')
            if task['evidence_id'] not in self.active_ids(task['case_id']):raise ValueError('증거를 다시 연결한 뒤 재시도하세요.')
            if self.store.get(task['case_id'])['status'] in ('running','pause_requested'):raise ValueError('실행이 종료된 뒤 재시도하세요.')
            repair={}
            if failed_dossiers_only:
                if task['action']!='ai_judgment' or task.get('analysis_version')!='linux-hunt-2':raise ValueError('단서 검토 작업만 제한 복구할 수 있습니다.')
                if task.get('dossier_repair_used'):raise ValueError('이 작업의 제한 복구는 이미 요청되었습니다.')
                from .dossiers import belongs
                failed=[d for d in self.store.list('dossier',task['case_id']) if belongs(d,task) and d['status']=='model_failed']
                if not failed:raise ValueError('현재 검토에 복구할 실패 단서가 없습니다.')
                failed.sort(key=lambda d:(d.get('previously_reviewed',False),d.get('review_priority',6),d['id']))
                repair={'dossier_repair_used':True,'repair_generation':task.get('retry_generation',0)+1,
                    'repair_source_generation':task.get('retry_generation',0),
                    'repair_group_keys':[d['group_key'] for d in failed[:3]],'repair_source_dossier_ids':[d['id'] for d in failed[:3]],
                    'repair_budget':{'dossiers':3,'jobs':4,'rounds':3,'attempts_per_round':2}}
            rejudge=task['action']=='ai_judgment' and task['status'] in GOOD|{'partial'}
            if not rejudge and (task['status'] not in ('failed','unsupported','blocked') or task['attempts']>=3):
                raise ValueError('재시도 가능한 실패 작업이 아니거나 재시도 상한에 도달했습니다.')
            self.store.update(task_id,status='queued',error=None,worker_job_key=None,started_at=None,retry_generation=task.get('retry_generation',0)+1,**repair)
            if task['action']=='linux_scan':
                for child in self.store.list('task',task['case_id']):
                    if child['evidence_id']==task['evidence_id'] and child['action'] in ('linux_investigate','ai_judgment','investigation_report') and not child.get('superseded'):
                        self.store.update(child['id'],superseded=True)
                        self.store.update(child['cell_id'],superseded=True)
            self.store.update(task['cell_id'],status='queued',error=None)
            self.store.audit(task['case_id'],'task_retry_requested',task_id=task_id,failed_dossiers_only=failed_dossiers_only,repair=repair)

    def snapshot(self,case_id):
        with self.store.lock:
            c=self.store.get(case_id,'case')
            data={kind:self.store.list(kind,case_id) for kind in ('evidence','coverage','task','observation','claim','message','report','epoch','receipt','audit','hypothesis','lineage','dossier','dossier_batch')}
            data['case']=c
            superseded={t['id'] for t in data['task'] if t.get('superseded')}
            old_receipts={r['id'] for r in data['receipt'] if r.get('task_id') in superseded}
            data['observation']=[o for o in data['observation'] if o.get('receipt_id') not in old_receipts]
            selected={o['id']:o for o in data['observation']}
            while True:
                selected_ids=set(selected)
                invalid=[oid for oid,o in selected.items() if not set(o.get('fields',{}).get('related_observation_ids',[])).issubset(selected_ids)]
                if not invalid:break
                for oid in invalid:del selected[oid]
            data['observation']=list(selected.values())
            data['task']=[t for t in data['task'] if not t.get('superseded')]
            data['coverage']=[t for t in data['coverage'] if not t.get('superseded')]
            cells=[x for x in data['coverage'] if x['evidence_id'] in self.active_ids(case_id)]
            data['summary']={'total':len(cells),'covered':sum(x['status'] in GOOD for x in cells),'gaps':sum(x['status'] in ('failed','unsupported','blocked','partial') for x in cells)}
            from .judgment import current
            data['judgments']=current(self,case_id,data['observation'])
            from .review_progress import project as review_progress
            data['review_progress']=review_progress(data)
            from .visual_timeline import project
            data['visual_timeline']=project(data)
            return data

    def finish(self,case_id,epoch,status):
        self.store.update(epoch['id'],status=status,ended_at=now())
        self.store.update(case_id,status=status)
        self.store.audit(case_id,'epoch_ended',status=status)

    def expand(self,case_id,parent_task):
        """One bounded deterministic recipe. No agent can recursively invoke itself."""
        recipe=json.loads((ROOT/'recipes/correlate-path.json').read_text(encoding='utf-8'))
        groups={}
        for ob in self.active_observations(case_id):
            path=ob.get('fields',{}).get('path')
            if ob['type'] in recipe['triggers'] and isinstance(path,str):
                groups.setdefault(path.casefold(),[]).append(ob)
        children=0
        for path,observations in groups.items():
            if len(observations)<2 or children>=recipe['max_children']:continue
            ids=sorted(o['id'] for o in observations)
            fingerprint=hashlib.sha256(json.dumps([recipe['id'],ids]).encode()).hexdigest()
            if self.store.db.execute('SELECT 1 FROM fingerprints WHERE case_id=? AND fingerprint=?',(case_id,fingerprint)).fetchone():continue
            depth=parent_task.get('depth',0)+1
            if depth>recipe['max_depth']:continue
            cell=self.store.add('coverage',case_id,evidence_id=parent_task['evidence_id'],status='queued',action='correlate',label='동일 경로의 대안 설명 확인',phase=5)
            task=self.store.add('task',case_id,evidence_id=parent_task['evidence_id'],cell_id=cell['id'],action='correlate',label=cell['label'],phase=5,status='queued',attempts=0,fingerprint=fingerprint,depth=depth,parent_task_id=parent_task['id'],observation_ids=ids,path_key=path)
            self.store.db.execute('INSERT INTO fingerprints VALUES(?,?,?)',(case_id,fingerprint,task['id']))
            self.store.add('hypothesis',case_id,text=f'동일 경로 관측의 관계 확인: {path}',status='open',observation_ids=ids,task_id=task['id'],uncertainty='동일 경로만으로 행위나 악성을 확정할 수 없습니다.')
            children+=1

    def step(self,case_id):
        with self.store.tx():
            c=self.store.get(case_id)
            if c['status'] not in ('running','pause_requested'):return False
            if c.get('runtime_binding') and c['runtime_binding']['fingerprint']!=self.runtime_binding()['fingerprint']:
                self.store.update(case_id,status='paused',investigation_stage='실행 버전 변경으로 중단 · 기존 기록 보존')
                self.store.audit(case_id,'runtime_contract_mismatch')
                return False
            epoch=self.store.get(c['epoch_id'])
            active=self.active_ids(case_id)
            tasks=[t for t in self.store.list('task',case_id) if t['evidence_id'] in active and not t.get('superseded')]
            queued=sorted([t for t in tasks if t['status']=='queued'],key=lambda t:(t['phase'],t['created_at']))
            if c['status']=='pause_requested':
                queued=[t for t in queued if t.get('worker_job_key') or t['action']=='linux_investigate']
                if not queued:self.store.update(case_id,status='paused');return False
            if not queued:
                status='complete' if all(t['status'] in GOOD for t in tasks) else 'quiescent'
                self.finish(case_id,epoch,status);return False
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(epoch['started_at'])).total_seconds()
            if epoch['jobs']>=epoch['max_jobs'] or age>21600:
                self.finish(case_id,epoch,'resource_limit');return False
            task=queued[0];e=self.store.get(task['evidence_id'])
            continuing=bool(task.get('started_at')) and task['action'] in ('linux_investigate','ai_judgment') or bool(task.get('worker_job_key'))
            self.store.update(task['id'],status='running',attempts=task['attempts']+(0 if continuing else 1),started_at=task.get('started_at') or now())
            self.store.update(task['cell_id'],status='running')
            if not continuing:self.store.update(epoch['id'],jobs=epoch['jobs']+1)
        try:
            from .runtime_contract import guard
            guard(self,case_id,task)
            info=metadata(self.evidence_root,e['path'])
            if info['signature']!=e['signature']:
                raise ValueError('등록 이후 증거가 변경되었습니다. 새 사건에서 다시 등록하세요.')
            integrity=next(t for t in tasks if t['evidence_id']==e['id'] and t['action']=='integrity')
            if task['action']!='integrity' and integrity['status'] not in GOOD:
                result={'status':'blocked','complete':False,'observations':[],'error':'증거 무결성 확인이 완료되지 않았습니다.'}
            elif task['action'] in ('linux_investigate','ai_judgment') and not any(t['action']=='linux_scan' and t['evidence_id']==e['id'] and t['status'] in GOOD|{'partial'} for t in tasks):
                result={'status':'blocked','complete':False,'observations':[],'error':'현재 분석 버전의 원문 조사가 완료되지 않았습니다. 원문 조사를 다시 실행하세요.'}
            elif task['action']=='linux_investigate':
                from .investigation import run
                result=run(self,case_id,e,task)
            elif task['action']=='ai_judgment':
                from .judgment import finish
                result=finish(self,case_id,e,task)
            elif task['action']=='investigation_report':
                from .reporting import build_report
                report=build_report(self,case_id,Path(os.getenv('DATA_ROOT',str(ROOT/'data')))/'reports')
                result={'status':'covered','complete':True,'observations':[],'tool':'investigation-package','version':'1','report_record_id':report['id']}
            elif task['action']=='correlate':
                if not set(task['observation_ids']).issubset({o['id'] for o in self.active_observations(case_id)}):raise ValueError('연결 해제된 증거를 참조하는 작업입니다.')
                references=[self.store.get(id,'observation') for id in task['observation_ids']]
                result={'status':'covered','complete':True,'truncated':False,'tool':'correlate-path-recipe','version':'1','observations':[{'type':'path_correlation','source_location':'recipe:correlate-path-v1','timestamp':None,'fields':{'path':task['path_key'],'related_observation_ids':task['observation_ids'],'source_types':sorted({o['type'] for o in references}),'interpretation':'경로 연관만 확인됨. 정상 관리 활동을 포함한 대안 설명 검토 필요.'}}]}
            elif os.getenv('WORKER_URL'):
                from .evidence_access import worker_request
                key=task.get('worker_job_key') or hashlib.sha256(json.dumps([case_id,task['fingerprint'],'worker-jobs-v1',task.get('retry_generation',0)]).encode()).hexdigest()
                self.store.update(task['id'],worker_job_key=key)
                try:
                    reply=worker_request('POST','/jobs',json={'job_key':key,'signature':e['signature'],'action':task['action'],'path':e['path']},timeout=20)
                    if reply['status'] in ('queued','running'):result=None
                    elif reply['status']=='execution_unknown':raise ExecutionUnknown('이전 작업의 실행 여부가 불명확해 자동 재실행을 중단했습니다.')
                    elif reply['status']=='failed':raise ValueError(reply.get('error'))
                    else:
                        result=reply['result']
                        if hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=reply['result_sha256']:raise ValueError('작업 결과 해시 불일치')
                except httpx.TransportError:result=None
            else:
                result=execute(self.evidence_root,task['action'],e['path'])
        except Exception as ex:
            result={'status':'blocked' if isinstance(ex,ExecutionUnknown) else 'failed','execution_unknown':isinstance(ex,ExecutionUnknown),'complete':False,'observations':[],'error':str(ex)}
        if result is None:
            with self.store.tx():
                self.store.update(task['id'],status='queued')
                if self.store.get(case_id)['status']=='pause_requested' and task['action']=='linux_investigate':
                    pending=any(j['status']=='submitted' for j in self.store.list('investigation_job',case_id))
                    if not pending:self.store.update(case_id,status='paused')
            self.stop.wait(1)
            return True
        with self.store.tx():
            # Receipts exist even for failure/empty output. A zero result never means absence globally.
            receipt=self.store.add('receipt',case_id,task_id=task['id'],evidence_id=e['id'],result={k:v for k,v in result.items() if k!='observations'},output_count=len(result.get('observations',[])))
            status=result['status']
            if status in GOOD and (not result.get('complete') or result.get('truncated')):status='failed'
            self.store.update(task['id'],status=status,receipt_id=receipt['id'],error=result.get('error'),execution_unknown=result.get('execution_unknown',False),ended_at=now())
            self.store.update(task['cell_id'],status=status,receipt_id=receipt['id'],error=result.get('error'))
            if task['action']=='integrity' and status in GOOD:
                manifest=result['manifest']
                content_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
                self.store.update(e['id'],sha256=content_hash,segment_manifest=manifest)
            if status in GOOD or status=='partial':
                known={o['digest']:o['id'] for o in self.store.list('observation',case_id)}
                for ob in result.get('observations',[]):
                    digest=hashlib.sha256(json.dumps([e['id'],ob],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                    if digest not in known:
                        added=self.store.add('observation',case_id,evidence_id=e['id'],receipt_id=receipt['id'],cell_id=task['cell_id'],digest=digest,**ob)
                        known[digest]=added['id']
                    self.store.add('lineage',case_id,observation_id=known[digest],receipt_id=receipt['id'],cell_id=task['cell_id'])
                if task['action']=='normalize':self.expand(case_id,task)
            self.store.audit(case_id,'task_finished',task_id=task['id'],status=status)
            if self.store.get(case_id)['status']=='pause_requested':self.store.update(case_id,status='paused')
        return True

    def loop(self):
        while not self.stop.is_set():
            progress=False
            for c in self.store.list('case'):
                if c['status'] in ('running','pause_requested'):
                    try:progress=self.step(c['id']) or progress
                    except Exception as ex:
                        with self.store.tx():
                            self.store.update(c['id'],status='paused')
                            self.store.audit(c['id'],'controller_error',error=str(ex))
            if not progress:self.wake.wait(1);self.wake.clear()

    def pack(self,case_id,question=''):
        if any(o['type'].startswith('linux_') for o in self.active_observations(case_id)):
            from .investigation import evidence_pack
            pack=evidence_pack(self,case_id,question)
            pack['coverage']=[c for c in self.store.list('coverage',case_id) if c['evidence_id'] in self.active_ids(case_id)]
            return pack
        data=self.snapshot(case_id)
        data['observation']=self.active_observations(case_id)
        data['coverage']=[c for c in data['coverage'] if c['evidence_id'] in self.active_ids(case_id)]
        selected=[];size=0
        for ob in data['observation']:
            encoded=json.dumps(ob,ensure_ascii=False)
            if len(encoded)>8000 or size+len(encoded)>18000:continue
            selected.append(ob);size+=len(encoded)
            if len(selected)>=40:break
        return {'observations':selected,'coverage':data['coverage'],'total_observations':len(data['observation']),'included_observations':len(selected),'selection_is_partial':len(selected)<len(data['observation'])}

    def analyze(self,case_id,question,config):
        if not self.model_lock.acquire(blocking=False):raise ValueError('다른 AI 요청이 진행 중입니다.')
        try:
            self.store.get(case_id,'case')
            self.store.add('message',case_id,role='user',text=question)
            pack=self.pack(case_id,question)
            if not pack['observations']:
                text='아직 확인된 관측 기록이 없습니다. 증거를 등록하고 조사를 실행하면 확인된 근거를 바탕으로 답할 수 있습니다.'
                return self.store.add('message',case_id,role='assistant',text=text,mode='system')
            if not config.get('model'):
                text=f"확인된 관측 {pack['total_observations']}건을 보관하고 있습니다. AI 설정에서 로컬 모델을 연결하면 질문 해석과 근거 검토를 시작합니다."
                return self.store.add('message',case_id,role='assistant',text=text,mode='system')
            output,receipt=Provider(config).generate(question,pack)
            with self.store.tx():
                self.store.add('receipt',case_id,receipt_type='model',**receipt)
                ids={o['id'] for o in pack['observations']}
                for candidate in output['claims']:
                    if candidate['claim_type']=='absence' or not set(candidate['observation_ids']).issubset(ids):
                        self.store.audit(case_id,'claim_rejected_by_gate',reason='absence_or_invalid_reference')
                        continue
                    self.store.add('claim',case_id,status='candidate',falsification=None,**candidate)
                return self.store.add('message',case_id,role='assistant',text=output['summary'],mode='ai_candidate',partial=pack['selection_is_partial'])
        except Exception as ex:
            self.store.add('receipt',case_id,receipt_type='model_error',error=str(ex))
            message='AI 응답 시간이 제한을 넘었습니다. 모델 상태를 확인한 뒤 다시 질문하세요.' if isinstance(ex,httpx.TimeoutException) else f'AI 분석을 완료하지 못했습니다: {ex}'
            self.store.add('message',case_id,role='assistant',text=message,mode='error')
            raise
        finally:self.model_lock.release()

    def falsify(self,claim_id,config):
        if not self.model_lock.acquire(blocking=False):raise ValueError('다른 AI 요청이 진행 중입니다.')
        try:
            claim=self.store.get(claim_id,'claim')
            if claim['status']!='candidate':raise ValueError('검토 중인 주장만 반증 검토할 수 있습니다.')
            if not set(claim['observation_ids']).issubset({o['id'] for o in self.active_observations(claim['case_id'])}):raise ValueError('연결 해제된 증거의 주장입니다. 증거를 다시 연결하세요.')
            pack=self.pack(claim['case_id'])
            selected={o['id']:o for o in pack['observations']}
            for id in claim['observation_ids']:selected[id]=self.store.get(id,'observation')
            pack['observations']=list(selected.values())
            output,receipt=Provider(config).generate(claim['text'],pack,'falsifier')
            if not set(output['contradicting_observation_ids']).issubset(selected):raise ValueError('반증 검토가 존재하지 않는 근거를 참조했습니다.')
            with self.store.tx():
                self.store.add('receipt',claim['case_id'],receipt_type='model',**receipt)
                return self.store.update(claim_id,falsification=output,review_type='모델 문맥 분리 검토 · 독립 도구 검증 아님')
        finally:self.model_lock.release()

    def decide(self,claim_id,decision,rationale):
        with self.store.tx():
            c=self.store.get(claim_id,'claim')
            if c['status']!='candidate':raise ValueError('이미 검토가 끝난 주장입니다.')
            if decision=='approve':
                if not set(c['observation_ids']).issubset({o['id'] for o in self.active_observations(c['case_id'])}):raise ValueError('연결 해제된 증거의 주장은 승인할 수 없습니다.')
                if not c.get('falsification'):raise ValueError('반증 검토 후 승인할 수 있습니다.')
                if c['falsification']['missing_checks']:raise ValueError('미완료 구분 검사가 남아 있어 승인할 수 없습니다.')
                for id in c['observation_ids']:
                    ob=self.store.get(id,'observation')
                    if ob['case_id']!=c['case_id'] or self.store.get(ob['cell_id'])['status'] not in GOOD:raise ValueError('유효한 사건 근거가 아닙니다.')
            self.store.audit(c['case_id'],'claim_decision',claim_id=claim_id,decision=decision,rationale=rationale)
            return self.store.update(claim_id,status='approved' if decision=='approve' else 'rejected',rationale=rationale)
