from copy import deepcopy

import pytest

from workbench.evidence_spans import field_span_manifest, manifest, utf8_spans
from workbench.review_paging import ProjectionTooLarge, build_span_pages
from workbench.review_stream import reduction_pack


def test_utf8_spans_reassemble_and_keep_exact_byte_coordinates():
    value = "prefix 한글 decisive-tail 🚩" * 30
    spans = utf8_spans(value, 31)
    assert "".join(item["text"] for item in spans) == value
    encoded = value.encode("utf-8")
    assert b"".join(item["text"].encode("utf-8") for item in spans) == encoded
    assert [(item["byte_start"], item["byte_end"]) for item in spans] == [
        (0, spans[0]["byte_end"]),
        *[(spans[i]["byte_start"], spans[i]["byte_end"]) for i in range(1, len(spans))],
    ]
    assert all(item["byte_end"] > item["byte_start"] for item in spans)


def test_span_manifest_preserves_full_digest_and_late_decisive_text():
    row = {"id": "obs-1", "fields": {"excerpt": "header " + "x" * 500 + " निर्णायक"}}
    entries = field_span_manifest(row, maximum_bytes=64)
    assert entries[-1]["byte_end"] == len(row["fields"]["excerpt"].encode())
    assert entries[-1]["full_sha256"] == entries[0]["full_sha256"]
    assert entries[-1]["observation_id"] == row["id"]


def test_span_pages_reassemble_long_observation_and_disclose_partial_coverage():
    text = "header " + "한글" * 3000 + " DECISIVE-LATE-CONDITION"
    pack = {"observations": [{"id": "obs-1", "fields": {"excerpt": text,
                                                               "source_sha256": "a" * 64,
                                                               "path": "/source"}}],
            "required_dossiers": [], "executed_checks": [],
            "allowed_observation_ids_by_dossier": {}}
    pages = build_span_pages(pack, maximum=7000)
    assert len(pages) > 1
    fragments = [row for page in pages for row in page["observations"]]
    assert "".join(row["fields"]["excerpt"] for row in fragments) == text
    assert all(row["source_span"]["observation_id"] == "obs-1" for row in fragments)
    assert all(page["finalization_allowed"] is False for page in pages)
    assert all(page["span_coverage"]["source_spans"] for page in pages)
    assert pack["observations"][0]["fields"]["excerpt"] == text


def test_unsplittable_oversized_observation_fails_explicitly():
    pack = {"observations": [{"id": "obs-1", "fields": {"binary": "z" * 10000}}],
            "required_dossiers": [], "executed_checks": [],
            "allowed_observation_ids_by_dossier": {}}
    with pytest.raises(ProjectionTooLarge):
        build_span_pages(pack, maximum=500)


def test_synthesis_uses_presented_tail_span_and_never_reloads_full_canonical_row():
    text = "header " + "x" * 16000 + " DECISIVE-TAIL-CONDITION"
    pack = {"observations": [{"id": "obs-1", "fields": {"excerpt": text}}],
            "required_dossiers": [], "executed_checks": [],
            "allowed_observation_ids_by_dossier": {}}
    pages = build_span_pages(pack, maximum=6000)
    for index, page in enumerate(pages):
        page["id"] = f"page-{index}"
        page["receipt_id"] = f"receipt-{index}"
        page["included_ids"] = ["obs-1"]
        page["output"] = {"findings": [], "check_assessments": []}
    tail_span=pages[-1]["observations"][0]["source_span"]["span_id"]
    pages[-1]["output"] = {"findings": [{"observation_ids": ["obs-1"],
                                          "evidence_span_ids": [tail_span],
                                          "counterevidence_ids": []}],
                            "check_assessments": []}
    stream = {"id": "stream", "maximum": 7000, "page_count": len(pages),
              "canonical_sha256": "digest", "canonical": pack,
              "open_objections": []}
    reduced = reduction_pack(stream, pages)
    assert len(reduced["observations"]) == 1
    assert reduced["observations"][0]["source_span"]
    assert "DECISIVE-TAIL-CONDITION" in reduced["observations"][0]["fields"]["excerpt"]
    assert len(reduced["observations"][0]["fields"]["excerpt"]) < len(text)
    presentation = manifest({"observations": reduced["observations"]})
    assert len(presentation["spans"]) == 1
    assert presentation["spans"][0]["extent"] == "partial_field"


def test_explicit_span_selection_is_order_independent_and_legacy_retains_all_or_blocks():
    text = "EARLY-COUNTER " + "x" * 16000 + " LATE-TAIL"
    pack = {"observations": [{"id": "obs-1", "fields": {"excerpt": text}}],
            "required_dossiers": [], "executed_checks": [],
            "allowed_observation_ids_by_dossier": {}}
    pages = build_span_pages(pack, maximum=6000)
    for index, page in enumerate(pages):
        page.update(id=f"p-{index}", receipt_id=f"r-{index}", included_ids=["obs-1"],
                    output={"findings": [], "check_assessments": []})
    early=pages[0]["observations"][0]["source_span"]["span_id"]
    late=pages[-1]["observations"][0]["source_span"]["span_id"]
    pages[0]["output"]={"findings":[{"observation_ids":["obs-1"],
        "evidence_span_ids":[early]}],"check_assessments":[]}
    pages[-1]["output"]={"findings":[{"observation_ids":["obs-1"],
        "evidence_span_ids":[late]}],"check_assessments":[]}
    stream={"id":"s","maximum":12000,"page_count":len(pages),"canonical_sha256":"d",
            "canonical":pack,"open_objections":[]}
    reduced=reduction_pack(stream,list(reversed(pages)))
    assert reduced["observations"][0]["fields"]["excerpt_spans"]
    assert {item["span_id"] for item in reduced["observations"][0]["fields"]["excerpt_spans"]} == {early,late}


def test_pinned_counter_span_survives_tail_only_model_selection():
    pack={"observations":[{"id":"obs-1","fields":{"excerpt":"canonical"}}],
          "required_dossiers":[],"executed_checks":[],"allowed_observation_ids_by_dossier":{}}
    def row(sid,text,start):
        return {"id":"obs-1","fields":{"excerpt":text},"source_span":{
            "span_id":sid,"observation_id":"obs-1","byte_start":start,
            "byte_end":start+len(text.encode()),"full_sha256":"f","sha256":sid}}
    early=row("span-early","EARLY COUNTER",0); tail=row("span-tail","LATE TAIL",100)
    pages=[]
    for index,(item,ids) in enumerate(((early,[]),(tail,["span-tail"]))):
        pages.append({"id":f"p{index}","receipt_id":f"r{index}","included_ids":["obs-1"],
            "observations":[item],"output":{"findings":[{"observation_ids":["obs-1"],
                "evidence_span_ids":ids}],"check_assessments":[]}})
    stream={"id":"s","maximum":12000,"page_count":2,"canonical_sha256":"d","canonical":pack,
        "open_objections":[{"id":"ob","dossier_id":"d","observation_ids":["obs-1"],
            "span_ids":["span-early"]}],
        "source_span_catalog":{"span-early":early,"span-tail":tail}}
    reduced=reduction_pack(stream,pages)
    assert {item["span_id"] for item in reduced["observations"][0]["fields"]["excerpt_spans"]} == {"span-early","span-tail"}


def test_comparison_excerpt_spans_reach_final_reduction_unchanged():
    pack={"observations":[{"id":"obs-1","fields":{"excerpt":"canonical"}}],
          "required_dossiers":[],"executed_checks":[],"allowed_observation_ids_by_dossier":{}}
    def page(index,sid,text,start):
        row={"id":"obs-1","fields":{"excerpt_spans":[{"span_id":sid,
            "byte_start":start,"byte_end":start+len(text.encode()),"full_sha256":"f",
            "sha256":sid,"text":text}]}}
        return {"id":f"p{index}","receipt_id":f"r{index}","included_ids":["obs-1"],
            "observations":[row],"output":{"findings":[{"observation_ids":["obs-1"],
                "evidence_span_ids":[sid]}],"check_assessments":[]}}
    pages=[page(0,"span-a","EARLY",0),page(1,"span-b","LATE",100)]
    stream={"id":"s","maximum":12000,"page_count":2,"canonical_sha256":"d","canonical":pack,
            "open_objections":[]}
    reduced=reduction_pack(stream,pages)
    spans=reduced["observations"][0]["fields"]["excerpt_spans"]
    assert [(item["span_id"],item["text"]) for item in spans] == [("span-a","EARLY"),("span-b","LATE")]
