"""Fixed source/time distribution; no AI interpretation influences selection."""
from collections import defaultdict


def spread(items, limit):
    if limit<=0:return []
    buckets=defaultdict(list)
    for item in items:
        f=item['fields'];buckets[(str(f.get('partition_offset')),f.get('path',''),item['type'],bool(item.get('timestamp')))].append(item)
    keys=sorted(buckets)
    if len(keys)>limit:
        selected=[keys[round(i*(len(keys)-1)/max(1,limit-1))] for i in range(limit)]
        # Reserve a share for undated sources even when sources exceed the pack.
        if any(not k[-1] for k in keys) and all(k[-1] for k in selected):
            selected[-1]=next(k for k in keys if not k[-1])
        keys=sorted(set(selected))
    rows=[sorted(buckets[k],key=lambda o:(o.get('timestamp') or '',o['fields'].get('image_file_byte_offset',o['fields'].get('byte_offset',0)) or 0,o['id'])) for k in keys]
    quotas=[0]*len(rows);remaining=min(limit,sum(map(len,rows)))
    while remaining:
        for i,bucket in enumerate(rows):
            if quotas[i]<len(bucket):quotas[i]+=1;remaining-=1
            if not remaining:break
    # Allocate each source's final quota BEFORE spreading over its full time range.
    return [bucket[round(j*(len(bucket)-1)/(take-1)) if take>1 else len(bucket)//2]
        for bucket,take in zip(rows,quotas) for j in range(take)]
