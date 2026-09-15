"""Fixed source/time distribution; no AI interpretation influences selection."""
from collections import defaultdict


def spread(items, limit):
    buckets=defaultdict(list)
    for item in items:
        f=item['fields'];buckets[(str(f.get('partition_offset')),f.get('path',''),item['type'])].append(item)
    ordered=[]
    for key in sorted(buckets):
        rows=sorted(buckets[key],key=lambda o:(o.get('timestamp') or '',o['fields'].get('image_file_byte_offset',o['fields'].get('byte_offset',0)) or 0,o['id']))
        take=min(len(rows),limit)
        indices=[round(i*(len(rows)-1)/max(1,take-1)) for i in range(take)]
        ordered.append([rows[i] for i in indices])
    result=[]
    for index in range(limit):
        for rows in ordered:
            if index<len(rows):result.append(rows[index])
            if len(result)==limit:return result
    return result
