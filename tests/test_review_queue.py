from workbench.review_queue import schedule


def test_normal_change_flood_cannot_exhaust_reserved_review_slots():
    rows=[{'id':str(i),'baseline':False,'review_family':'general_changes','review_priority':6,'group_key':str(i)} for i in range(20000)]
    for family in ('access','persistence','execution','other'):
        rows += [{'id':family+str(i),'baseline':False,'review_family':family,'review_priority':i,'group_key':family+str(i)} for i in range(20)]
    rows += [{'id':'baseline'+str(i),'baseline':True} for i in range(3)]
    first=schedule(rows);second=schedule(list(reversed(rows)))
    assert len(first)==96
    assert {r['id'] for r in first}=={r['id'] for r in second}
    assert sum(r.get('review_family')=='general_changes' for r in first)<=24
    for family in ('access','persistence','execution','other'):
        assert sum(r.get('review_family')==family for r in first)>=12
    oldest={'id':'old','baseline':False,'review_family':'general_changes','review_priority':99,
            'group_key':'old','deferred_since':'2026-01-01'}
    assert oldest in schedule(rows+[oldest])
