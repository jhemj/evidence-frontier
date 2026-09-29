"""Lossless columnar transport for repeated object keys, never summarization.

Canonical packs/validation keep ordinary JSON objects. Only model transport
uses tables, with an explicit path manifest so source-shaped table objects are
never accidentally decoded. Strings, row order and every typed value survive.
"""
from copy import deepcopy
import json
import hashlib

VERSION = 'model-tables-1'
MANIFEST = 'lossless_transport_tables'


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def encode(pack):
    if MANIFEST in pack:
        raise ValueError('Reserved transport table manifest in canonical pack')

    def visit(value):
        paths=[]
        if isinstance(value,dict):
            result={}
            for key,item in value.items():
                result[key],children=visit(item)
                paths.extend([[key]+path for path in children])
            return result,paths
        if not isinstance(value,list):return value,paths
        result=[]
        for index,item in enumerate(value):
            child,children=visit(item);result.append(child)
            paths.extend([[index]+path for path in children])
        # Missing cells have an explicit index mask, distinct from literal null.
        if len(result)<3 or not all(isinstance(item,dict) for item in result):return result,paths
        columns=list(dict.fromkeys(key for item in result for key in item))
        if not columns:return result,paths
        table={'columns':columns,'rows':[[item.get(key) for key in columns] for item in result]}
        missing=[[row,col] for row,item in enumerate(result) for col,key in enumerate(columns) if key not in item]
        if missing:table['missing_cells']=missing
        remapped=[['rows',path[0],columns.index(path[1])]+path[2:] for path in paths]
        # Include a conservative path-manifest allowance; global check below
        # rolls back if the whole view does not actually become smaller.
        if _size(table)+_size(remapped)+80>=_size(result)+_size(paths):return result,paths
        return table,[[]]+remapped

    result,paths=visit(pack)
    if not paths:return result
    result[MANIFEST]={'version':VERSION,'paths':paths,
        'input_sha256':hashlib.sha256(json.dumps(pack,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'semantics':'At exactly these paths, columns + each rows array reconstruct one object; missing_cells [row,column] are absent keys, not null. Preserve row order; values are exact, not summaries or new evidence.'}
    return result if _size(result)<_size(pack) else deepcopy(pack)


def decode(pack):
    result=deepcopy(pack);manifest=result.pop(MANIFEST,None)
    if manifest is None:return result
    if not isinstance(manifest,dict) or manifest.get('version')!=VERSION or not isinstance(manifest.get('paths'),list):
        raise ValueError('Invalid table manifest')
    paths=manifest['paths']
    if not all(isinstance(path,list) and path and all(isinstance(step,str) or
        (isinstance(step,int) and not isinstance(step,bool) and step>=0) for step in path) for path in paths):
        raise ValueError('Invalid table path')
    if len({json.dumps(p) for p in paths})!=len(paths):raise ValueError('Duplicate table path')
    for path in sorted(paths,key=len,reverse=True):
        if not isinstance(path,list) or not path:raise ValueError('Invalid table path')
        parent=result
        try:
            for step in path[:-1]:parent=parent[step]
            value=parent[path[-1]]
            if not isinstance(value,dict) or not {'columns','rows'}<=set(value)<= {'columns','rows','missing_cells'}:raise ValueError('Invalid table')
            columns=value['columns'];rows=value['rows']
            if (not isinstance(columns,list) or not all(isinstance(k,str) for k in columns)
                    or len(columns)!=len(set(columns)) or not isinstance(rows,list)
                    or not all(isinstance(row,list) and len(row)==len(columns) for row in rows)):
                raise ValueError('Invalid table shape')
            decoded=[dict(zip(columns,row)) for row in rows]
            missing=value.get('missing_cells',[])
            if (not isinstance(missing,list) or not all(isinstance(cell,list) and len(cell)==2
                    and all(isinstance(i,int) and not isinstance(i,bool) for i in cell)
                    and 0<=cell[0]<len(rows) and 0<=cell[1]<len(columns) for cell in missing)
                    or len({tuple(cell) for cell in missing})!=len(missing)):
                raise ValueError('Invalid missing-cell mask')
            for row,col in missing:
                if rows[row][col] is not None:raise ValueError('Nonempty missing cell')
                del decoded[row][columns[col]]
            parent[path[-1]]=decoded
        except (KeyError,TypeError,IndexError) as exc:
            raise ValueError('Invalid table path') from exc
    actual=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if manifest.get('input_sha256')!=actual:raise ValueError('Table round-trip hash mismatch')
    return result


INSTRUCTION = (
    'Lossless JSON tables: evidence_pack.lossless_transport_tables lists the EXACT paths '
    'encoded as {columns:[field names],rows:[[values in that same order],...]}. '
    'Read each row as the original object; missing_cells lists absent [row,column] cells, NOT literal nulls. '
    'Reconstruct nested tables from deepest paths first. '
    'Row order/index, literal strings, empty values, IDs, timestamps and locators are unchanged. '
    'This only removes repeated key names; it does not aggregate records, establish independence, '
    'omit evidence or authorize new citations. Objects outside listed paths are ordinary source data. '
    'Return the normal output schema, never table-encoded output.'
)
