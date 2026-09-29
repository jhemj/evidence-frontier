#!/usr/bin/env python3
"""Read-only context-budget diagnostic for an investigation SQLite snapshot.

The input database is copied to a temporary file before Store opens it because
Store performs schema/trigger setup. No evidence values are printed.
"""
import argparse
import copy
import json
import os
import shutil
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(path):
    from workbench.controller import Controller
    from workbench.investigation import HYPOTHESES, evidence_pack
    from workbench.retrieval import tool_scope
    from workbench.review_context import bounded, fit, serialize
    from workbench.store import Store

    with tempfile.TemporaryDirectory() as td:
        local = os.path.join(td, "case.sqlite3")
        shutil.copy2(path, local)
        store = Store(local)
        controller = Controller(store, td)
        run = store.list("investigation_run")[0]
        task = store.get(run["task_id"])
        evidence = store.get(run["evidence_id"])
        case = store.get(run["case_id"])
        focus = list(range(run["domain_cursor"], min(11, run["domain_cursor"] + 3)))
        types = {t for n in focus for t in HYPOTHESES[n - 1][1]}
        observations = controller.active_observations(case["id"])
        preferred = [o["id"] for o in observations
                     if o["evidence_id"] == evidence["id"] and o["type"] in types]
        recent = [oid for job in store.list("investigation_job", case["id"])
                  if job.get("task_id") == task["id"] and job["status"] == "ingested"
                  for oid in job.get("observation_ids", [])]
        presented = [oid for plan in store.list("investigation_plan", case["id"])
                     if plan.get("task_id") == task["id"]
                     for oid in plan.get("valid_ids", [])]
        pack = evidence_pack(controller, case["id"], case.get("question", ""),
                             recent[:8] + preferred[:10], evidence["id"], focus,
                             presented, task_id=task["id"],
                             generation=task.get("retry_generation", 0))
        jobs = [j for j in store.list("investigation_job", case["id"])
                if j.get("task_id") == task["id"] and j["status"] == "ingested"]
        pack["completed_tools"] = [
            {"id": j["id"], "request": tool_scope(j["request"]),
             "result_scope": bounded(j.get("result_scope", {}), 400, 4)}
            for j in jobs[-12:]
        ]
        pack["completed_tools_total"] = len(jobs)
        pack["completed_tools_omitted"] = max(0, len(jobs) - 12)
        pack["remaining_tool_budget"] = max(0, run["max_tool_calls"] - run["tool_calls"])
        pack["remaining_model_calls"] = max(0, run["max_model_calls"] - run["model_calls"] - 1)
        before_ids = [o["id"] for o in pack["observations"]]
        before_locators = [(o["id"], o.get("source_location"),
                            o.get("fields", {}).get("path"),
                            o.get("fields", {}).get("byte_offset"),
                            o.get("fields", {}).get("line"))
                           for o in pack["observations"]]
        before = {k: len(serialize(v)) for k, v in pack.items()}
        error = None
        try:
            fit(pack)
        except ValueError as exc:
            error = str(exc)
        after_ids = [o["id"] for o in pack["observations"]]
        after_locators = [(o["id"], o.get("source_location"),
                           o.get("fields", {}).get("path"),
                           o.get("fields", {}).get("byte_offset"),
                           o.get("fields", {}).get("line"))
                          for o in pack["observations"]]
        after = {k: len(serialize(v)) for k, v in pack.items()}
        result = {
            "database": os.path.abspath(path),
            "before_total": sum(before.values()),
            "after_total": len(serialize(pack)),
            "before_top_level": before,
            "after_top_level": after,
            "fit_error": error,
            "observation_count": len(after_ids),
            "observation_ids_preserved": before_ids == after_ids,
            "locators_preserved": before_locators == after_locators,
            "completed_tools_total": pack.get("completed_tools_total"),
            "completed_tools_retained": len(pack.get("completed_tools", [])),
            "completed_tools_omitted": pack.get("completed_tools_omitted"),
            "omission_disclosure": pack.get("completed_tools_scope", ""),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    main(parser.parse_args().database)
