"""Source-bound repetition summaries over immutable parsed event records.

A retained artifact can be one chunk of an original file. A later record's
chunk-relative offset must never inherit the first record's artifact identity.
Chronological extrema and the last record encountered are distinct anchors.
"""
from copy import deepcopy
import json
from .evidence_semantics import exact_utc_ns

VERSION = 'source-bound-occurrences-1'
SCOPE = ('Parsed matching records, not unique executions or independent sources. '
         'First/last observed are bounded temporal extrema, not continuous activity, '
         'registration time or complete-source coverage. Each endpoint has its own '
         'retained artifact/hash and byte coordinate; never reuse the representative '
         'artifact for another endpoint. Full parsed records remain in the event ledger.')


def grouping_key(event):
    fields=event['fields'];typ=event['type']
    return json.dumps([fields.get('partition_offset'),fields.get('inode'),fields.get('path'),typ,
        fields.get('command'),fields.get('cwd'),fields.get('user'),fields.get('address'),
        fields.get('outcome'),fields.get('state'),fields.get('excerpt') if typ not in
        ('linux_authentication','linux_cron_call','linux_command','linux_login_record') else None,
        fields.get('host'),fields.get('record_type')],ensure_ascii=False)


def source_anchor(event):
    fields=event['fields']
    keys=('artifact_path','source_sha256','source_complete','path','partition_offset','inode',
          'byte_offset','byte_length','image_file_byte_offset','source_range_start',
          'line','locator_basis','time_basis','time_record')
    return {'timestamp':event.get('timestamp'),
            'source_location':event.get('source_location'),
            **{key:deepcopy(fields[key]) for key in keys if key in fields}}


def initialize(event):
    fields=event['fields'];stamp=event.get('timestamp')
    valid=stamp if exact_utc_ns(stamp) is not None else None
    fields.update(occurrences=1,first_observed=valid,last_observed=valid)


def merge(group,event):
    """Extend summary without altering its representative source or raw fields."""
    fields=group['fields']
    if fields.get('occurrences',1)==1:
        original=source_anchor(group)
        if exact_utc_ns(original['timestamp']) is not None:
            fields['first_observed_source']=deepcopy(original)
            fields['last_observed_source']=deepcopy(original)
    fields['occurrences']=fields.get('occurrences',1)+1
    anchor=source_anchor(event);stamp=anchor['timestamp'];ns=exact_utc_ns(stamp)
    if ns is not None:
        first=exact_utc_ns(fields.get('first_observed'))
        last=exact_utc_ns(fields.get('last_observed'))
        if first is None or ns<first:
            fields['first_observed']=stamp;fields['first_observed_source']=deepcopy(anchor)
        if last is None or ns>last:
            fields['last_observed']=stamp;fields['last_observed_source']=deepcopy(anchor)
    fields['last_record_source']=anchor
    fields['occurrence_summary_version']=VERSION
    fields['occurrences_scope']=SCOPE
    # Legacy flat offsets silently used the representative's artifact even
    # across chunks, and the scan-last row need not be the latest timestamp.
    fields.pop('last_byte_offset',None)
    fields.pop('last_line',None)
