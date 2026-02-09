"""Pull recent WHOOP data. Run from project root with venv active.

Usage:
    python pull.py                    # all data, last 5 records each
    python pull.py sleep              # just sleep
    python pull.py workout --limit 10 # 10 most recent workouts
    python pull.py cycle recovery     # multiple types
    python pull.py all --json         # raw JSON output
"""

import asyncio
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.whoop_client import (
    get_sleep, get_workouts, get_recovery, get_cycles, get_profile, get_body,
)

USER_ID = "32850214"

FETCHERS = {
    "sleep": get_sleep,
    "workout": get_workouts,
    "recovery": get_recovery,
    "cycle": get_cycles,
}


def print_cycles(records):
    for r in records:
        s = r.get("score") or {}
        end = r.get("end") or "ongoing"
        print(f"  {r['start'][:16]} -> {end[:16] if end != 'ongoing' else end}")
        print(f"    strain: {s.get('strain', '?'):.1f}  cals: {s.get('kilojoule', 0):.0f}kJ  "
              f"avg_hr: {s.get('average_heart_rate', '?')}  max_hr: {s.get('max_heart_rate', '?')}")


def print_sleep(records):
    for r in records:
        s = r.get("score") or {}
        print(f"  {r.get('start', '?')[:16]} -> {r.get('end', '?')[:16]}")
        stage = s.get("stage_summary") or {}
        total_min = stage.get("total_in_bed_time_milli", 0) / 60000
        rem_min = stage.get("total_rem_sleep_time_milli", 0) / 60000
        deep_min = stage.get("total_slow_wave_sleep_time_milli", 0) / 60000
        light_min = stage.get("total_light_sleep_time_milli", 0) / 60000
        print(f"    in_bed: {total_min:.0f}min  rem: {rem_min:.0f}min  deep: {deep_min:.0f}min  light: {light_min:.0f}min")
        perf = s.get("sleep_performance_percentage")
        eff = s.get("sleep_efficiency_percentage")
        if perf is not None:
            print(f"    performance: {perf:.0f}%  efficiency: {eff:.0f}%")


def print_workouts(records):
    for r in records:
        s = r.get("score") or {}
        sport = r.get("sport_id", "?")
        print(f"  {r.get('start', '?')[:16]} -> {r.get('end', '?')[:16]}  sport_id: {sport}")
        print(f"    strain: {s.get('strain', '?'):.1f}  avg_hr: {s.get('average_heart_rate', '?')}  "
              f"max_hr: {s.get('max_heart_rate', '?')}  cals: {s.get('kilojoule', 0):.0f}kJ")


def print_recovery(records):
    for r in records:
        s = r.get("score") or {}
        print(f"  cycle_id: {r.get('cycle_id', '?')}  created: {r.get('created_at', '?')[:16]}")
        print(f"    recovery: {s.get('recovery_score', '?'):.0f}%  hrv: {s.get('hrv_rmssd_milli', '?'):.1f}ms  "
              f"rhr: {s.get('resting_heart_rate', '?')}bpm  spo2: {s.get('spo2_percentage', '?')}%")


PRINTERS = {
    "cycle": print_cycles,
    "sleep": print_sleep,
    "workout": print_workouts,
    "recovery": print_recovery,
}


async def main():
    parser = argparse.ArgumentParser(description="Pull recent WHOOP data")
    parser.add_argument("types", nargs="*", default=["all"], help="Data types: sleep, workout, recovery, cycle, profile, body, all")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true", dest="raw_json", help="Output raw JSON")
    args = parser.parse_args()

    types = args.types
    if "all" in types:
        types = ["profile", "body", "cycle", "sleep", "workout", "recovery"]

    for kind in types:
        if kind == "profile":
            data = await get_profile(USER_ID)
            print(f"\n=== PROFILE ===")
            if args.raw_json:
                print(json.dumps(data, indent=2))
            else:
                print(f"  {data.get('first_name', '')} {data.get('last_name', '')} (user_id: {data.get('user_id')})")
                print(f"  email: {data.get('email', '?')}")
            continue

        if kind == "body":
            data = await get_body(USER_ID)
            print(f"\n=== BODY ===")
            if args.raw_json:
                print(json.dumps(data, indent=2))
            else:
                print(f"  height: {data.get('height_meter', '?')}m  weight: {data.get('weight_kilogram', '?')}kg  max_hr: {data.get('max_heart_rate', '?')}")
            continue

        fetcher = FETCHERS.get(kind)
        if not fetcher:
            print(f"Unknown type: {kind}", file=sys.stderr)
            continue

        records = await fetcher(USER_ID, limit=args.limit)
        print(f"\n=== {kind.upper()} ({len(records)} records) ===")
        if args.raw_json:
            print(json.dumps(records, indent=2))
        elif records:
            PRINTERS[kind](records)
        else:
            print("  (none)")


if __name__ == "__main__":
    asyncio.run(main())
