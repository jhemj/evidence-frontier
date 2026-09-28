"""Persist accepted reader-card changes without turning interpretations into sources."""
from copy import deepcopy
from .store import now


def assessment_revision(dossier, finding, receipt_id, *, published=False):
    history=deepcopy(dossier.get('assessment_history', []))
    previous=history[-1] if history else None
    # Polling/replaying the same accepted response must not manufacture changes.
    if previous and previous['finding']==finding and previous.get('published')==published:
        return history
    before=(previous or {}).get('finding') or dossier.get('finding') or {}
    old=set(before.get('observation_ids', [])+before.get('counterevidence_ids', []))
    new=set(finding.get('observation_ids', [])+finding.get('counterevidence_ids', []))
    changed=[k for k in ('title','card_summary','reason','judgment','timeline_role') if before.get(k)!=finding.get(k)]
    change='first_assessment' if not before else 'sources_added' if new-old else 'reinterpretation' if changed or old-new else 'published'
    history.append({'revision':len(history)+1,'at':now(),'receipt_id':receipt_id,
        'published':published,'change_type':change,'changed_fields':changed,
        'added_observation_ids':sorted(new-old),'removed_observation_ids':sorted(old-new),
        'change_reason':finding.get('change_reason') or finding.get('reason',''),
        'finding':deepcopy(finding)})
    return history


def lifecycle(finding, *, final=False, action=None, basis='positive_evidence'):
    """Display lifecycle is separate from narrow fact confidence."""
    refs=finding.get('supporting_evidence_ids') or finding.get('observation_ids', [])
    contrary=finding.get('refuting_evidence_ids') or finding.get('counterevidence_ids', [])
    if action=='hold':return 'inconclusive'
    if (action=='refute' or finding.get('timeline_role')=='반증됨') and contrary and basis!='absence':return 'refuted'
    if final and finding.get('judgment') in ('확인','확정') and refs:return 'supported'
    if refs and (action=='reinforce' or finding.get('judgment')=='유력'):return 'strengthened'
    if final:return 'inconclusive'
    return 'investigating'
