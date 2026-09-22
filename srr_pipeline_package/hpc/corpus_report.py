#!/usr/bin/env python3
"""Add the batches back together, and say what the corpus actually got.

Every array task reports against its own manifest, so a run of seventy tasks
can be seventy clean N/N reports and still have lost samples between them: a
batch nobody submitted, a task killed before it wrote anything, a stale batch
list that ran the same samples twice. Nothing that reads one outcome.json can
see any of that. This reads the plan and every outcome together.

    python3 corpus_report.py --plan ./plan --results ./stage3_results

It reconciles rather than sums. The plan says which samples were supposed to
run and in which batch; the outcomes say what each run credited. Reported
separately:

    complete     a run credited it -- its own work, reused work, or work it
                 released on the record
    incomplete   a run reached it and did not credit it, with the reason the
                 run gave
    unaccounted  no outcome mentions it at all. The dangerous one: nothing
                 failed, because nothing ran
    duplicated   ANALYSED by more than one run. Queue time spent twice, and
                 usually a stale batch list. A retry that reports `cache_hit`
                 is not this: reusing an earlier run's output is the mechanism
                 that makes re-running a failed batch safe, and counting it as
                 a duplicate turned every normal retry into a failed corpus

Reads files and prints. It connects to nothing and changes nothing.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# What the verifier counts as done. Released is completion: the work was done
# and verified, and the artefact was deliberately let go afterwards.
COMPLETE = ("fresh", "cache_hit", "released")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_NOTHING = 3
EXIT_PARTIAL = 4


def planned_samples(plan_dir: Path):
    """Sample -> batch number, and the plan it came from.

    A sample in two batch lists is refused, not folded into one entry: keeping
    only the first lets the plan ask for the same work twice and still be
    reported as a tidy corpus. Exact-once starts with the plan being exact-once.

    plan.json, where there is one, is the authority on which samples the plan
    was for. A sample it names that no batch list contains is work that fell
    out of the plan between deciding it and writing it down.
    """
    planned, seen_twice = {}, []
    lists = sorted(plan_dir.glob("batch_*.txt"))
    if not lists:
        raise SystemExit("no batch_*.txt under %s; this is not a plan directory"
                         % plan_dir)
    for one in lists:
        number = int(one.stem.split("_")[1])
        for line in one.read_text().splitlines():
            sample = line.strip()
            if not sample:
                continue
            if sample in planned:
                seen_twice.append((sample, planned[sample], number))
            else:
                planned[sample] = number
    if seen_twice:
        raise SystemExit(
            "the plan lists the same sample in more than one batch, so it asks "
            "for the same\n  work twice: %s"
            % "; ".join("%s in batches %d and %d" % row for row in seen_twice[:6]))

    plan_json, unlisted = {}, []
    path = plan_dir / "plan.json"
    if path.is_file():
        try:
            plan_json = json.loads(path.read_text())
        except ValueError:
            plan_json = {}
        unlisted = sorted(set(plan_json.get("sample_names") or []) - set(planned))
    return planned, len(lists), plan_json, unlisted


def outcomes(results_dir: Path, run_id):
    """Every run record this bundle wrote, and only those.

    Reading whatever outcome.json happens to be in the tree meant a single
    record from an earlier run was enough to report a corpus complete that had
    not been started. A results directory is reused between runs; the run id is
    what separates them, and every task of one array carries it as a prefix.
    """
    return [(path, outcome) for path, outcome in _all_outcomes(results_dir)
            if _belongs(outcome, path, run_id)]


def _belongs(outcome, path, run_id):
    found = outcome.get("run_id") or path.parent.name
    return found == run_id or found.startswith(run_id + "-")


def _all_outcomes(results_dir: Path):
    """Every run record under a results tree, wherever the batches put them.

    A resident run writes results/runs/<id>/; an array writes
    results/batch_<n>/runs/<id>/. Both are found by looking for the file rather
    than by assuming the shape, so a tree retrieved with `--only summary`
    reconciles the same as a full one.
    """
    found = []
    for path in sorted(results_dir.rglob("outcome.json")):
        try:
            found.append((path, json.loads(path.read_text())))
        except (OSError, ValueError) as exc:
            sys.stderr.write("skipping unreadable %s (%s)\n" % (path, exc))
    return found


def reconcile(planned, records):
    """What the corpus got, per sample, against what it was promised.

    A sample can appear in several runs: a batch re-submitted after a failure,
    a run that met work an earlier one had done. **The last verdict is the one
    that stands.** Taking the first favourable one instead let an earlier
    `fresh` hide a later `missing` -- an output lost since -- or a later
    `stale`, which is what an environment change looks like; and treating any
    disagreement as a conflict turned every ordinary retry into a failed
    corpus.

    Runs are ordered by what they say they finished at, and by where they sit
    when they do not say.
    """
    def when(pair):
        """Attempt first, then the run directory. Never the wall clock alone.

        Array tasks land on different nodes, and `finished_at` is read on the
        node. With ten minutes of skew a failed first attempt's verdict
        overwrote the successful retry's -- and a requeue collision fails in
        seconds, so the gap the skew has to beat is tiny.

        An array run id ends `-b<batch>-<job><restart>`, which increases with
        every attempt at the same batch, so it orders attempts correctly
        without trusting any clock.
        """
        path, outcome = pair
        run_id = outcome.get("run_id") or str(path.parent.name)
        # Only an array run id, whose shape is `...-b<batch>-<job><restart>`.
        # Anything else -- a single-job bundle, a hand-made id -- has no
        # attempt to read, and falls through to the clock and the path.
        shaped = re.search(r"-b\d+-(\w+)$", run_id)
        attempt = shaped.group(1).rjust(24, "0") if shaped else ""
        # The directory, not the file: `.../run-b1/outcome.json` sorts AFTER
        # `.../run-b1-retry/outcome.json`, because '-' precedes '/'.
        return (attempt, outcome.get("finished_at") or "", str(path.parent))

    latest, reasons, analysed_by = {}, {}, {}
    for path, outcome in sorted(records, key=when):
        run_id = outcome.get("run_id") or str(path.parent.name)
        for sample, rec in (outcome.get("samples") or {}).items():
            status = rec.get("status")
            latest[sample] = status
            reasons[sample] = (status, rec.get("reason", ""))
            # Work actually done again, which `cache_hit` and `released` are
            # not: reuse is the mechanism that makes re-running a batch safe.
            if status == "fresh":
                analysed_by.setdefault(sample, []).append(run_id)

    complete = {s: st for s, st in latest.items()
                if s in planned and st in COMPLETE}
    incomplete = {s: reasons[s] for s, st in latest.items()
                  if s in planned and st not in COMPLETE}
    unaccounted = sorted(s for s in planned if s not in latest)
    duplicated = sorted(s for s, runs in analysed_by.items()
                        if len(set(runs)) > 1)
    unplanned = sorted(s for s, st in latest.items()
                       if s not in planned and st in COMPLETE)
    return {"complete": complete, "incomplete": incomplete,
            "unaccounted": unaccounted, "duplicated": duplicated,
            "unplanned": unplanned, "conflicted": []}


def report(planned, batches, result, out):
    total = len(planned)
    done = len(result["complete"])
    out.write("Corpus: %d sample(s) planned across %d batch(es).\n"
              % (total, batches))
    out.write("  read %d run record(s)%s\n"
              % (result.get("runs_read", 0),
                 "" if result.get("runs_read", 0) >= batches else
                 " -- FEWER THAN THE PLAN'S BATCHES. A batch that never ran "
                 "and a\n  transfer that dropped a file look the same from "
                 "here; check the archive first."))
    out.write("  complete      %d\n" % done)
    out.write("  incomplete    %d\n" % len(result["incomplete"]))
    out.write("  unaccounted   %d\n" % len(result["unaccounted"]))

    if result["incomplete"]:
        out.write("\nReached and not credited:\n")
        grouped = {}
        for sample, (status, reason) in sorted(result["incomplete"].items()):
            grouped.setdefault((status, reason), []).append(sample)
        for (status, reason), names in sorted(grouped.items(),
                                              key=lambda kv: -len(kv[1])):
            out.write("  %d %s: %s\n" % (len(names), status, reason or "(no reason given)"))
            out.write("    %s%s\n" % (", ".join(names[:6]),
                                      ", ..." if len(names) > 6 else ""))

    if result["unaccounted"]:
        out.write("\nNo run mentions these at all -- nothing failed, because "
                  "nothing ran:\n")
        by_batch = {}
        for sample in result["unaccounted"]:
            by_batch.setdefault(planned[sample], []).append(sample)
        for number, names in sorted(by_batch.items()):
            out.write("  batch %03d: %d sample(s): %s%s\n"
                      % (number, len(names), ", ".join(names[:6]),
                         ", ..." if len(names) > 6 else ""))
        out.write("  A whole batch here is a task that was never submitted or "
                  "died before it\n  wrote anything. Re-run those batches; the "
                  "finished ones are cache hits.\n")

    if result.get("conflicted"):
        out.write("\nOne run credited these and another did not -- an output "
                  "lost, or an\nenvironment that changed under them:\n  %s\n"
                  % ", ".join(result["conflicted"][:10]))
    if result.get("unlisted_by_plan"):
        out.write("\nIn plan.json but in no batch list -- planned and never "
                  "handed to anything:\n  %s\n"
                  % ", ".join(result["unlisted_by_plan"][:10]))
    if result["duplicated"]:
        out.write("\nCredited by more than one run (queue time spent twice, "
                  "usually a stale batch list):\n  %s\n"
                  % ", ".join(result["duplicated"][:10]))
    if result["unplanned"]:
        out.write("\nCredited but not in this plan (%d): %s\n"
                  % (len(result["unplanned"]),
                     ", ".join(result["unplanned"][:6])))

    if (result["duplicated"] or result.get("unlisted_by_plan")
            or result.get("conflicted")):
        out.write("\nThis corpus cannot be reported finished. See above.\n")
    elif done == total and total:
        out.write("\nEvery planned sample is accounted for and complete.\n")
    elif done:
        out.write("\n%d of %d complete. Do not report this corpus as finished.\n"
                  % (done, total))
    else:
        out.write("\nNothing in this plan completed.\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Reconcile a corpus plan against every run's outcome.")
    ap.add_argument("--plan", type=Path, required=True,
                    help="a plan_batches.py output directory")
    ap.add_argument("--results", type=Path, required=True,
                    help="the retrieved results tree")
    ap.add_argument("--run-id", required=True,
                    help="the bundle's run id (run_id.txt); records from other "
                         "runs in the same tree are not this corpus")
    ap.add_argument("--json", type=Path,
                    help="also write the reconciliation here")
    args = ap.parse_args(argv)

    if not args.results.is_dir():
        sys.stderr.write("no results tree at %s\n" % args.results)
        return EXIT_CONFIG
    planned, batches, plan_json, unlisted = planned_samples(args.plan)
    records = outcomes(args.results, args.run_id)
    result = reconcile(planned, records)
    result["unlisted_by_plan"] = unlisted
    result["runs_read"] = len(records)
    if args.json:
        args.json.write_text(json.dumps(
            {"kind": "genoar.corpus.reconciliation",
             "plan_id": plan_json.get("plan_id"), "run_id": args.run_id,
             "planned": len(planned), "batches": batches,
             "runs_read": len(records), **result},
            indent=2, sort_keys=True) + "\n")
    report(planned, batches, result, sys.stdout)

    if not records:
        return EXIT_NOTHING
    if (result["duplicated"] or result["unlisted_by_plan"]
            or result.get("conflicted")):
        # Exact-once is the claim. Work done twice, or planned and never
        # listed, is a corpus that cannot make it -- whatever the totals say.
        return EXIT_PARTIAL
    if len(result["complete"]) == len(planned):
        return EXIT_OK
    return EXIT_PARTIAL if result["complete"] else EXIT_NOTHING


if __name__ == "__main__":
    sys.exit(main())
