"""Timing for the self-scheduling collector (stdlib only, runs on bare runners).

GitHub's own cron never fired for this repo, so each collector run starts
the next one: collect, wait for the next 20-minute slot, dispatch again.
Outside ski hours a run just waits (up to the 6-hour job limit) and hands
off, so the chain survives overnight and resumes at 7am.

    python -m skicrowd.schedule window   -> prints "true"/"false"
    python -m skicrowd.schedule wait     -> prints seconds to sleep before
                                            dispatching the next run, or
                                            "stop" outside the ski season
"""
from __future__ import annotations

import datetime as dt
import sys
from zoneinfo import ZoneInfo

MT = ZoneInfo("America/Denver")
SEASON_MONTHS = {11, 12, 1, 2, 3, 4}
START_HOUR, END_HOUR = 7, 17          # collect 7:00-16:59 Mountain time
STEP_MIN = 20
MAX_WAIT_S = 5 * 3600 + 45 * 60       # stay under GitHub's 6-hour job limit


def in_window(now: dt.datetime) -> bool:
    t = now.astimezone(MT)
    return t.month in SEASON_MONTHS and START_HOUR <= t.hour < END_HOUR


def next_collection(now: dt.datetime) -> dt.datetime | None:
    """Next time a run should collect, or None when the season is over."""
    t = now.astimezone(MT).replace(second=0, microsecond=0)
    if t.month not in SEASON_MONTHS:
        return None
    slot = t + dt.timedelta(minutes=STEP_MIN - t.minute % STEP_MIN)
    if START_HOUR <= slot.hour < END_HOUR:
        return slot
    day = t.date() if t.hour < START_HOUR else t.date() + dt.timedelta(days=1)
    morning = dt.datetime.combine(day, dt.time(START_HOUR), tzinfo=MT)
    return morning if morning.month in SEASON_MONTHS else None


def wait_seconds(now: dt.datetime) -> int | None:
    nxt = next_collection(now)
    if nxt is None:
        return None
    return int(min(max((nxt - now).total_seconds(), 60), MAX_WAIT_S))


def main(argv: list[str]) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    cmd = argv[1] if len(argv) > 1 else "window"
    if cmd == "window":
        print("true" if in_window(now) else "false")
    elif cmd == "wait":
        w = wait_seconds(now)
        print("stop" if w is None else w)
    else:
        sys.exit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main(sys.argv)
