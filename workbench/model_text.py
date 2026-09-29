"""Lossless run-length transport for repetitive retained text.

No binary/source classification or semantic filtering. Explicit path manifests
and a whole-pack hash distinguish encoded values from source-shaped objects.
Canonical source fields, literal validation and receipts remain ordinary text.
"""
from copy import deepcopy
import hashlib
from itertools import groupby
import json

VERSION='model-text-runs-1'
MANIFEST='lossless_transport_text'


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def encode(pack):
    if MANIFEST in pack:raise ValueError('Reserved text transport manifest')
    paths=[]
    def visit(value,path):
        if isinstance(value,dict):return {key:visit(item,path+[key]) for key,item in value.items()}
        if isinstance(value,list):return [visit(item,path+[index]) for index,item in enumerate(value)]
        if not isinstance(value,str) or not 128<=len(value)<=10_000_000:return value
        runs=[];literal=[]
        for char,items in groupby(value):
            count=sum(1 for _ in items)
            if count>=8:
                if literal:runs.append(''.join(literal));literal=[]
                runs.append({'repeat':count,'text':char})
            else:literal.append(char*count)
        if literal:runs.append(''.join(literal))
        encoded={'text_runs':runs,'unicode_characters':len(value),
                 'utf8_sha256':hashlib.sha256(value.encode()).hexdigest()}
        if len(_json(encoded))+len(_json(path))+160>=len(_json(value)):return value
        paths.append(path)
        return encoded
    result=visit(pack,[])
    if not paths:return result
    result[MANIFEST]={'version':VERSION,'paths':paths,'input_sha256':_hash(pack),
        'semantics':'At exactly these paths concatenate text_runs in order. A string is literal; {repeat:N,text:C} means the exact Unicode character C repeated N times. No source text is omitted. Hashes address retained UTF-8 text, not acquired bytes.'}
    return result if len(_json(result))<len(_json(pack)) else deepcopy(pack)


def decode(pack):
    result=deepcopy(pack);manifest=result.pop(MANIFEST,None)
    if manifest is None:return result
    if not isinstance(manifest,dict) or manifest.get('version')!=VERSION or not isinstance(manifest.get('paths'),list):
        raise ValueError('Invalid text transport manifest')
    paths=manifest['paths']
    if not all(isinstance(path,list) and path and all(isinstance(step,str) or
            isinstance(step,int) and not isinstance(step,bool) and step>=0 for step in path) for path in paths):
        raise ValueError('Invalid text transport path')
    if len({_json(p) for p in paths})!=len(paths):raise ValueError('Duplicate text transport path')
    for path in paths:
        try:
            parent=result
            for step in path[:-1]:parent=parent[step]
            value=parent[path[-1]]
            if not isinstance(value,dict) or set(value)!={'text_runs','unicode_characters','utf8_sha256'}:
                raise ValueError('Invalid text run envelope')
            length=value['unicode_characters'];runs=value['text_runs']
            if not isinstance(length,int) or isinstance(length,bool) or not 0<=length<=10_000_000 or not isinstance(runs,list):
                raise ValueError('Invalid text run length')
            total=0;parts=[]
            for run in runs:
                if isinstance(run,str):part=run;count=len(part)
                elif isinstance(run,dict) and set(run)=={'repeat','text'}:
                    count=run['repeat'];char=run['text']
                    if not isinstance(count,int) or isinstance(count,bool) or count<=0 or not isinstance(char,str) or len(char)!=1 or count>length-total:
                        raise ValueError('Invalid repeated character')
                    part=char*count
                else:raise ValueError('Invalid text run')
                total+=count
                if total>length:raise ValueError('Text run length exceeded')
                parts.append(part)
            text=''.join(parts)
            if total!=length or hashlib.sha256(text.encode()).hexdigest()!=value['utf8_sha256']:
                raise ValueError('Text run hash/length mismatch')
            parent[path[-1]]=text
        except (KeyError,TypeError,IndexError) as exc:
            raise ValueError('Invalid text transport path') from exc
    if _hash(result)!=manifest.get('input_sha256'):raise ValueError('Text pack round-trip hash mismatch')
    return result


INSTRUCTION=(
    'Lossless text runs: decode the columnar tables first, then evidence_pack.lossless_transport_text '
    'lists EXACT paths whose original strings use {text_runs:[...],unicode_characters:N,utf8_sha256:H}. '
    'Concatenate runs in order; a string run is literal and {repeat:N,text:C} repeats the exact Unicode character C N times. '
    'This is an exact encoding, not an omitted excerpt or evidence summary. Retained-field hashes/coordinates do not become acquired-file coordinates. '
    'Long padding is not meaningful behavioral evidence. Do not transcribe padding runs into output. '
    'In working reviews, metadata-only propositions use source_selections mode=metadata_only and no body span; '
    'other roles must follow only their declared output schema. '
    'for a meaningful excerpt quote use actual decoded source text, never the encoding object or an invented expansion. '
    'Return the normal output schema, never run-encoded output.'
)
