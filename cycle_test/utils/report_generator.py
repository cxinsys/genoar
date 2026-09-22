#!/usr/bin/env python3
"""
Report Generator - Generate cycle summaries and final test reports
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def _substage_succeeded(sub: Dict[str, Any]) -> bool:
    """Whether a Stage 3 sub-stage really did all of its work.

    The success flag means the step ran without erroring out, not that every item
    came through: the pipeline reports success when Cell Ranger ran even if it
    failed on individual samples, and a download reports the same with failures
    counted separately. Reading only the flag hid those. Shared by the rate tally
    and the console print, so a 0% rate cannot show a check mark in the console.
    """
    if not sub.get("success"):
        return False
    if sub.get("failed_samples", 0) > 0:
        return False
    if sub.get("failed", 0) > 0:
        return False
    return True


# Stage 3's real work is download / pipeline / handoff. prep runs on every cycle
# regardless of whether Stage 3 was requested, so it never counts as Stage 3 on
# its own — otherwise a Stage-1+2 run would report Stage 3 at 100%.
STAGE3_KEYS = ("stage3_download", "stage3_pipeline", "stage3_handoff")


def _stage3_verdict(result: Dict[str, Any], stages: Dict[str, Any]) -> tuple:
    """Decide, from the whole cycle, whether Stage 3 was attempted and passed.

    One place owns this so the report and the orchestrator's exit status share a
    single state model — the mismatch between the two is what let earlier reports
    disagree with the exit code. Three mutually exclusive things make Stage 3 an
    attempt:

      - an execution sub-stage (download / pipeline / handoff) actually ran;
      - a run that requested Stage 3 had its prep fail, so nothing downstream ran;
      - a run that requested Stage 3 found no data (status no_data), which the
        orchestrator already treats as a failure (console ✗, exit 1).

    Returns (attempted, ok). A run that never requested Stage 3 leaves it N/A.
    """
    requested = result.get("stage3_requested", False)
    attempted = False
    ok = True

    for key in STAGE3_KEYS:
        sub = stages.get(key)
        if sub is None or sub.get("skipped"):
            continue
        attempted = True
        if not _substage_succeeded(sub):
            ok = False

    prep = stages.get("stage3_prep")
    if requested and prep is not None and not prep.get("skipped") \
            and not prep.get("success"):
        attempted = True
        ok = False

    # no_data is now reachable without Stage 3 ever being in the picture: Stage 1
    # ends a cycle that way when no page falls in the requested range. Counting
    # that as a failed Stage 3 would report a stage that was never asked for, so
    # the request flag gates it.
    if requested and result.get("status") == "no_data":
        attempted = True
        ok = False

    return attempted, ok


def generate_cycle_summary(
    all_results: List[Dict[str, Any]],
    output_dir: Path
) -> Path:
    """
    Generate comprehensive summary of all cycles.

    Args:
        all_results: List of cycle results
        output_dir: Output directory

    Returns:
        Path to the final report file
    """
    timestamp = datetime.now()

    summary = {
        "generated_at": timestamp.isoformat(),
        "total_cycles": len(all_results),
        "cycles": [],
        "totals": {
            "meta_files": 0,
            "smtx_files": 0,
            "srr_files": 0,
            "total_srr_ids": 0,
            "cell_type_samples": 0,
            "tissue_samples": 0,
            "disease_samples": 0
        },
        "success_rate": {
            "stage1": 0.0,
            "stage2": 0.0,
            "stage3": 0.0,
            "overall": 0.0
        }
    }

    # Count attempts and successes per stage separately, so a stage that was
    # skipped (never attempted) neither helps nor hurts its rate. Counting a
    # skipped stage as a success is what made --stage3-only runs report 100%.
    tally = {s: {"attempted": 0, "succeeded": 0} for s in ("stage1", "stage2", "stage3")}

    def _record(stage_key, stage_result):
        """Fold one stage's {success, skipped} into the tally."""
        if stage_result is None or stage_result.get("skipped"):
            return
        tally[stage_key]["attempted"] += 1
        if stage_result.get("success"):
            tally[stage_key]["succeeded"] += 1

    for result in all_results:
        cycle_summary = {
            "cycle_num": result.get("cycle_num"),
            "pages": f"{result.get('start_page')}-{result.get('end_page')}",
            "status": result.get("status", "unknown"),
            "start_time": result.get("start_time"),
            "end_time": result.get("end_time"),
            "duration_seconds": None
        }

        # Why a cycle correctly did nothing (no page in range / no SRR ids / no
        # sample eligible for Cell Ranger). Present only on no_data cycles, so a
        # reader can tell "did nothing" apart from "failed" without the log.
        if result.get("no_data_reason"):
            cycle_summary["no_data_reason"] = result["no_data_reason"]

        # Calculate duration
        if result.get("start_time") and result.get("end_time"):
            try:
                start = datetime.fromisoformat(result["start_time"])
                end = datetime.fromisoformat(result["end_time"])
                cycle_summary["duration_seconds"] = (end - start).total_seconds()
            except (ValueError, TypeError):
                pass

        stages = result.get("stages", {})

        # Stage 1 stats
        if "stage1" in stages:
            s1 = stages["stage1"]
            _record("stage1", s1)

            if "statistics" in s1:
                stats = s1["statistics"]
                summary["totals"]["meta_files"] += stats.get("meta_files", 0)
                summary["totals"]["smtx_files"] += stats.get("smtx_files", 0)
                summary["totals"]["srr_files"] += stats.get("srr_files", 0)
                summary["totals"]["total_srr_ids"] += stats.get("total_srr_ids", 0)

            cycle_summary["stage1"] = {
                "success": s1.get("success", False),
                "skipped": s1.get("skipped", False),
                # "success" | "capped" | "no_pages_in_range" | "interrupted" |
                # "failed". A capped crawl succeeded over every page that exists,
                # so success alone would hide that the range moved.
                "status": s1.get("status"),
                "capped": s1.get("capped", False),
                "capped_pages": s1.get("capped_pages"),
                "meta_files": s1.get("statistics", {}).get("meta_files", 0),
                # The integration pass is not fatal to Stage 1, but a failure
                # means its reports are missing, which should not be invisible.
                "integration_ok": s1.get("integration", {}).get("success"),
                "error": s1.get("error")
            }

        # Stage 2 stats
        if "stage2" in stages:
            s2 = stages["stage2"]
            _record("stage2", s2)

            if "tables" in s2:
                for field, info in s2["tables"].items():
                    if "rows" in info and info["rows"] > 0:
                        key = f"{field}_samples"
                        if key in summary["totals"]:
                            summary["totals"][key] += info["rows"]

            cycle_summary["stage2"] = {
                "success": s2.get("success", False),
                "skipped": s2.get("skipped", False),
                "tables": {k: v.get("rows", 0) for k, v in s2.get("tables", {}).items()},
                "error": s2.get("error")
            }

        # Stage 3 prep stats
        if "stage3_prep" in stages:
            s3 = stages["stage3_prep"]
            cycle_summary["stage3_prep"] = {
                "success": s3.get("success", False),
                "unique_srr_ids": s3.get("unique_srr_ids", 0),
                "gse_count": s3.get("gse_count", 0)
            }

        # Surface every execution sub-stage that is present (the old report
        # dropped all of these). The Stage 3 pass/fail verdict for the rate is
        # decided in one place, _stage3_verdict, so it stays in step with the
        # orchestrator's exit status.
        for key in STAGE3_KEYS:
            if key in stages:
                cycle_summary[key] = {
                    k: v for k, v in stages[key].items() if k != "statistics"
                }

        stage3_attempted, stage3_ok = _stage3_verdict(result, stages)
        if stage3_attempted:
            tally["stage3"]["attempted"] += 1
            if stage3_ok:
                tally["stage3"]["succeeded"] += 1

        summary["cycles"].append(cycle_summary)

    # Success rate per stage over the cycles that actually attempted it. A stage
    # nothing attempted is None (N/A) rather than 0 or 100, so a skipped stage is
    # never mistaken for a perfect one.
    def _rate(stage_key):
        attempted = tally[stage_key]["attempted"]
        if attempted == 0:
            return None
        return round(tally[stage_key]["succeeded"] / attempted * 100, 1)

    for stage_key in ("stage1", "stage2", "stage3"):
        summary["success_rate"][stage_key] = _rate(stage_key)

    # Overall is over every attempt across the attempted stages, so a failed
    # Stage 3 pulls it below 100 and an all-skipped stage does not inflate it.
    total_attempted = sum(t["attempted"] for t in tally.values())
    total_succeeded = sum(t["succeeded"] for t in tally.values())
    summary["success_rate"]["overall"] = (
        round(total_succeeded / total_attempted * 100, 1) if total_attempted else None
    )

    # Calculate averages
    if all_results:
        durations = [
            c.get("duration_seconds") for c in summary["cycles"]
            if c.get("duration_seconds") is not None
        ]
        if durations:
            summary["average_cycle_duration_seconds"] = round(sum(durations) / len(durations), 1)

    # Save report
    report_path = output_dir / f"final_report_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, 'w') as f:
        json.dump(summary, f, indent=2)

    # Print summary to console
    print_summary(summary)

    logger.info(f"Final report saved to {report_path}")

    return report_path


def print_summary(summary: Dict[str, Any]) -> None:
    """Print formatted summary to console."""
    print("\n" + "=" * 60)
    print("CYCLE TEST FINAL REPORT")
    print("=" * 60)
    print(f"Generated: {summary['generated_at']}")
    print(f"Total Cycles: {summary['total_cycles']}")
    print()

    # Success rates. None means the stage was never attempted (e.g. skipped by
    # --stage3-only), shown as N/A rather than a misleading 0 or 100.
    def _fmt(rate):
        return "N/A" if rate is None else f"{rate}%"

    print("Success Rates:")
    print(f"  Stage 1: {_fmt(summary['success_rate']['stage1'])}")
    print(f"  Stage 2: {_fmt(summary['success_rate']['stage2'])}")
    print(f"  Stage 3: {_fmt(summary['success_rate'].get('stage3'))}")
    print(f"  Overall: {_fmt(summary['success_rate']['overall'])}")
    print()

    # Totals
    print("Totals:")
    print(f"  META files:         {summary['totals']['meta_files']}")
    print(f"  SMTX files:         {summary['totals']['smtx_files']}")
    print(f"  SRR files:          {summary['totals']['srr_files']}")
    print(f"  Total SRR IDs:      {summary['totals']['total_srr_ids']}")
    print(f"  Cell Type samples:  {summary['totals']['cell_type_samples']}")
    print(f"  Tissue samples:     {summary['totals']['tissue_samples']}")
    print(f"  Disease samples:    {summary['totals']['disease_samples']}")
    print()

    # Average duration
    if summary.get("average_cycle_duration_seconds"):
        avg_min = summary["average_cycle_duration_seconds"] / 60
        print(f"Average Cycle Duration: {avg_min:.1f} minutes")
        print()

    # Per-cycle summary
    print("Per-Cycle Results:")
    print("-" * 60)
    for cycle in summary["cycles"]:
        status_icon = "✓" if cycle["status"] == "success" else "✗"
        # The reason turns a bare "✗ no_data" into something a reader can act on:
        # a cycle that correctly did nothing reads differently from one that broke.
        reason = f" — {cycle['no_data_reason']}" if cycle.get("no_data_reason") else ""
        print(
            f"  Cycle {cycle['cycle_num']:03d} ({cycle['pages']}): "
            f"{status_icon} {cycle['status']}{reason}"
        )

        if cycle.get("stage1"):
            s1 = cycle["stage1"]
            if s1.get("skipped"):
                print("    Stage 1: - skipped")
            else:
                s1_icon = "✓" if s1["success"] else "✗"
                note = "" if s1.get("integration_ok") is not False else " (integration failed)"
                if s1.get("capped"):
                    pages = s1.get("capped_pages") or {}
                    note += (
                        f" (capped at page {pages.get('completed_end', '?')} of the "
                        f"{pages.get('requested_end', '?')} requested)"
                    )
                elif s1.get("status") == "no_pages_in_range":
                    # Zero META files because there was nothing to crawl, not
                    # because the crawl went wrong.
                    note += " (no page fell in the requested range)"
                print(f"    Stage 1: {s1_icon} {s1.get('meta_files', 0)} META files{note}")

        if cycle.get("stage2"):
            s2 = cycle["stage2"]
            if s2.get("skipped"):
                print("    Stage 2: - skipped")
            else:
                s2_icon = "✓" if s2["success"] else "✗"
                tables = s2.get("tables", {})
                print(f"    Stage 2: {s2_icon} cell_type={tables.get('cell_type', 0)}, "
                      f"tissue={tables.get('tissue', 0)}, disease={tables.get('disease', 0)}")

        dl = cycle.get("stage3_download")
        if dl and not dl.get("skipped"):
            # Same verdict as the rate: a download with failures is not a check mark,
            # so the icon cannot say ok while the JSON rate says 0%.
            dl_icon = "✓" if _substage_succeeded(dl) else "✗"
            print(f"    Stage 3 download: {dl_icon} "
                  f"{dl.get('downloaded', 0)}/{dl.get('total', 0)} ok, {dl.get('failed', 0)} failed")

        pl = cycle.get("stage3_pipeline")
        if pl and not pl.get("skipped"):
            # Cell Ranger output is the point of Stage 3b, so the console says how
            # many samples have it — the number a zero-sample run would otherwise
            # hide behind a success line.
            pl_icon = "✓" if _substage_succeeded(pl) else "✗"
            print(f"    Stage 3 pipeline: {pl_icon} "
                  f"{pl.get('cellranger_completed', 0)} sample(s) with Cell Ranger output")
            if pl.get("nothing_processed"):
                print(f"      nothing analysed: {pl.get('nothing_processed_reason')}")
            elif pl.get("error"):
                print(f"      error: {pl.get('error')}")

    print("=" * 60)


if __name__ == "__main__":
    # Test with sample data
    logging.basicConfig(level=logging.INFO)

    sample_results = [
        {
            "cycle_num": 1,
            "start_page": 1,
            "end_page": 100,
            "status": "success",
            "start_time": "2024-01-15T10:00:00",
            "end_time": "2024-01-15T10:30:00",
            "stages": {
                "stage1": {
                    "success": True,
                    "statistics": {
                        "meta_files": 15,
                        "smtx_files": 20,
                        "srr_files": 10,
                        "total_srr_ids": 150
                    }
                },
                "stage2": {
                    "success": True,
                    "tables": {
                        "cell_type": {"rows": 100},
                        "tissue": {"rows": 50},
                        "disease": {"rows": 30}
                    }
                },
                "stage3_prep": {
                    "success": True,
                    "unique_srr_ids": 150,
                    "gse_count": 10
                }
            }
        }
    ]

    output_dir = Path("/tmp/report_test")
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = generate_cycle_summary(sample_results, output_dir)
    print(f"\nReport saved to: {report_path}")
