"""Conservative source semantics. A locator, file hash or engine start is not intent."""
from datetime import datetime, timezone
import ntpath
import re

VERSION = 'evidence-semantics-3'
TIME_KINDS = {'occurred','file_metadata','observed','collected','ingested','derived','unknown'}

# Registered artifact meanings, not path/vendor/IOC rules. Raw tool excerpts
# are deliberately absent: they may contain a runtime log and need source
# interpretation, unlike an explicitly classified static setting or binary.
STATIC_CONTENT_TYPES = frozenset({
    'linux_configuration', 'linux_persistence', 'linux_path_match', 'linux_binary',
    'linux_account', 'linux_detection', 'linux_inspection_result', 'linux_ssh_trust',
    'filesystem_entry', 'filesystem_time', 'file_metadata',
    'windows_file', 'windows_task', 'windows_registry', 'windows_amcache',
    'windows_shimcache', 'windows_prior_interpretation',
})


def exact_utc_ns(raw):
    """Integer arithmetic only. No guessed timezone or subnanosecond rounding."""
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',str(raw))
    if not match:return None
    try:
        parsed=datetime.fromisoformat(match[1]+match[3].replace('Z','+00:00')).astimezone(timezone.utc)
        elapsed=parsed-datetime(1970,1,1,tzinfo=timezone.utc)
        return (elapsed.days*86400+elapsed.seconds)*1000000000+int((match[2] or '').ljust(9,'0'))
    except (ValueError,OverflowError):return None


def observation_time(observation):
    f=observation.get('fields',{});kind=f.get('time_kind')
    if kind is None:
        typ=observation.get('type','')
        if typ in ('filesystem_entry','filesystem_time','file_metadata','windows_file','windows_registry','windows_task','linux_configuration','linux_persistence'):
            kind='file_metadata'
        elif typ in ('linux_environment','windows_environment'):kind='collected'
        elif typ in ('linux_detection','windows_correlation','windows_counterevidence'):kind='derived'
        elif typ in ('linux_authentication','linux_command','linux_cron_call','linux_login_record','linux_audit','linux_audit_group','linux_session','windows_process','windows_event','windows_network','windows_powershell_start','windows_scriptblock'):
            kind='occurred'
        else:kind='unknown'
    saved=f.get('time_record') if isinstance(f.get('time_record'),dict) else {}
    result=time_record(saved.get('raw') or observation.get('timestamp'),f.get('time_basis','unspecified source time'),kind=kind)
    result['source_origin']={k:f.get(k) for k in ('artifact_path','source_sha256','partition_offset','os_instance')}
    result['kind_basis']='explicit source label' if 'time_kind' in f else 'registered artifact semantics; not actor attribution'
    return result


def compare_times(left, right):
    """Source-scoped time association; proximity never proves a common actor."""
    a,b=observation_time(left),observation_time(right)
    result={'observation_ids':[left['id'],right['id']],'comparable':False,'delta_nanoseconds':None,
            'independent_sources':None,'limitation':'Recorded time difference only; clock skew, common actor and causation unverified.'}
    scope=lambda o: [o.get('evidence_id')]+[o.get('fields',{}).get(k) for k in ('partition_offset','os_instance','boot_id','user')]
    if not left.get('evidence_id') or scope(left)!=scope(right):
        return {**result,'reason':'identity_scope_mismatch'}
    if a['time_kind']=='unknown' or a['time_kind']!=b['time_kind']:
        return {**result,'reason':'time_kind_mismatch_or_unknown'}
    if any(t['epoch_nanoseconds'] is None or re.search(r'assum|infer|추정',str(t['timezone_basis'] or ''),re.I) for t in (a,b)):
        return {**result,'reason':'timezone_or_precision_unestablished'}
    delta=int(b['epoch_nanoseconds'])-int(a['epoch_nanoseconds'])
    whole,fraction=divmod(abs(delta),1000000000)
    from .retrieval import source_origin
    result.update(comparable=True,time_kind=a['time_kind'],delta_nanoseconds=str(delta),
        delta_seconds_exact=('-' if delta<0 else '')+str(whole)+('.'+f'{fraction:09d}'.rstrip('0') if fraction else ''),
        independent_sources=False if source_origin(left)==source_origin(right) else None,
        reason='exact_encoded_timestamps_not_clock_accuracy')
    return result


def time_record(value, basis='source timestamp', assumed_zone=None, kind='unknown'):
    raw = value.isoformat() if isinstance(value, datetime) else str(value or '')
    result = {'raw': raw, 'normalized_utc': None, 'timezone_basis': basis,
              'precision': 'unspecified', 'timezone_assumed': False,
              'time_kind':kind if isinstance(kind,str) and kind in TIME_KINDS else 'unknown','epoch_nanoseconds':None}
    if not raw:
        return result
    exact=exact_utc_ns(raw)
    if exact is not None:result['epoch_nanoseconds']=str(exact)
    fraction = re.search(r'\.(\d+)', raw)
    result['precision'] = f'{len(fraction[1])} fractional digits' if fraction else 'second'
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            if assumed_zone is None:
                result['timezone_basis'] = 'timezone-naive; not converted'
                return result
            dt = dt.replace(tzinfo=assumed_zone)
            result['timezone_assumed'] = True
        result['normalized_utc'] = dt.astimezone(timezone.utc).isoformat()
        if fraction and len(fraction[1]) > 6:
            result['normalization_precision'] = 'microsecond; higher precision retained in raw'
    except (ValueError, OverflowError):
        result['error'] = 'invalid timestamp; raw value retained'
    return result


def windows_path(path):
    """Do not invent drive mappings, resolve links, expand environment or drop ADS."""
    value = str(path or '').replace('/', '\\')
    if value.startswith('\\\\?\\'):
        value = value[4:]
    return ntpath.normpath(value).casefold() if value else ''


def wow64_path(literal, caller_bits=None, os_bits=None, redirection_enabled=None,
               windows_directory='C:\\Windows'):
    path, root = windows_path(literal), windows_path(windows_directory)
    result = {'literal_path': literal, 'canonical_path': path, 'caller_bits': caller_bits,
              'os_bits': os_bits, 'resolved_path': None, 'candidates': [path],
              'basis': 'caller/OS/redirection state not established'}
    prefix = root + '\\system32\\'
    exceptions = ('catroot', 'catroot2', 'driverstore', 'drivers\\etc', 'logfiles', 'spool')
    if caller_bits == 32 and os_bits == 64 and path.startswith(root + '\\sysnative\\'):
        result.update(resolved_path=root + '\\system32\\' + path.split('\\sysnative\\', 1)[1],
                      basis='documented Sysnative alias for 32-bit caller')
    elif path.startswith(prefix) and caller_bits == 32 and os_bits == 64:
        tail = path[len(prefix):]
        if any(tail == e or tail.startswith(e + '\\') for e in exceptions):
            result.update(resolved_path=path, basis='documented non-redirected subtree')
        elif redirection_enabled is True:
            result.update(resolved_path=root + '\\syswow64\\' + tail,
                          basis='x86 caller on x64 with redirection enabled; verify file hash separately')
        elif redirection_enabled is False:
            result.update(resolved_path=path, basis='redirection explicitly disabled')
        else:
            result['candidates'].append(root + '\\syswow64\\' + tail)
    elif caller_bits in (32, 64) and os_bits in (32, 64) and not path.startswith('\\device\\'):
        result.update(resolved_path=path, basis='no applicable filesystem redirection')
    if result['resolved_path']:
        result['candidates'] = [result['resolved_path']]
    return result


def windows_support(observation):
    """Allowed narrow behavior stages, never a malware/authorization verdict."""
    kind = observation.get('type', '')
    if not kind.startswith('windows_'):
        return None
    f = observation.get('fields', {})
    if kind in ('windows_process', 'windows_powershell_start', 'windows_srum_application'):
        return {'execution'}
    if kind == 'windows_prefetch' and f.get('path_attribution') == 'exact_full_path':
        return {'execution'}
    if kind == 'windows_network':
        return {'connection'} if f.get('network_state') == 'succeeded' else set()
    if kind in ('windows_task', 'windows_registry'):
        return {'configuration'}
    if kind == 'windows_scriptblock':
        return {'invocation'}
    # Amcache/Shimcache, reputation, SRUM traffic totals, filenames, static PE,
    # raw excerpts and prior imported findings cannot prove execution/success.
    return set()


def stage_issues(stage, sources):
    if stage.get('judgment') != '확인' or not sources:
        return []
    requested = stage['stage']
    if requested == 'invocation' and all(o.get('type') in STATIC_CONTENT_TYPES for o in sources):
        return [{'code': 'static_content_not_invocation', 'stage': requested,
                 'source_ids': [o['id'] for o in sources if o.get('id')],
                 'detail': 'Cited sources contain only static content/settings or metadata, not a recorded call. '
                           'Reassess as configuration/content, or cite an actual invocation record. '
                           'Do not change confidence or drop citations merely to pass validation.'}]
    supports = [windows_support(o) for o in sources]
    if requested in ('execution', 'connection', 'objective') and all(s is not None for s in supports):
        if requested == 'connection' and stage.get('network_state') in ('attempted', 'failed', 'indeterminate'):
            if any(o.get('fields', {}).get('network_state') == stage['network_state'] for o in sources):
                return []
        if not any(requested in s for s in supports):
            return [{'code': 'unsupported_windows_proof_stage', 'stage': requested}]
    return []


def activity_context(fields):
    """Only explicit, source-backed labels; date or filename alone is not attribution."""
    category = fields.get('activity_class', 'unattributed')
    if category not in ('incident', 'post_incident', 'collection', 'unattributed'):
        category = 'unattributed'
    refs = fields.get('activity_basis_locators') or []
    if category != 'unattributed' and not refs:
        category = 'unattributed'
    return {'category': category, 'basis_locators': refs,
            'cutover_time': time_record(fields.get('cutover_time')),
            'limit': 'Temporal proximity and familiar tool names do not establish actor or authorization.'}
