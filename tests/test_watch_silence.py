"""The watcher opens an issue for a real outage, not for a late upload.

Stations send 12-hour batches; one or two late batches make a station
"quiet" for a day or more before it catches up with nothing lost. On
2026-10-05/06 five did, and two issues were opened. These replay that and
a real outage through the watcher's own state and diff.
"""

from __future__ import annotations

from datetime import datetime, timezone

import secchi.sources.watch as W

AIR = "Air Temperature & Relative Humidity"


def inv(last, site="Glenbrook 2", stype=AIR, count=1000, sid=None):
    return {"locations": {"terrestrial": {stype: [
        {"site": site, "last_update": last, "data_count": count, "id": sid or site}]}}}


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def diff(a, b):
    return W.diff_state(a, b)


def notable(changes):
    return [c for c in changes if c["severity"] == "notable"]


def test_a_late_upload_is_logged_but_opens_no_issue():
    # Last reading 03:45 on the logger clock (11:45 UTC); quiet by the next
    # morning; back that afternoon with the gap filled, 35 h after it.
    live = W._snapshot_state(inv("2026-10-05T03:45:00", count=1000), set(), now=at("2026-10-05T20:00:00"))
    quiet = W._snapshot_state(inv("2026-10-05T03:45:00", count=1000), set(), now=at("2026-10-06T13:19:00"))
    back = W._snapshot_state(inv("2026-10-06T00:15:00", count=1082), set(), now=at("2026-10-06T22:38:00"))
    went = diff(live, quiet)
    assert [c["kind"] for c in went] == ["sensor went quiet"] and not notable(went)
    came = diff(quiet, back)
    kinds = {c["kind"] for c in came}
    assert kinds == {"sensor resumed", "dormant sensor received an upload"}
    assert not notable(came)                                  # no issue
    assert all("late upload" in c["note"] for c in came)
    # Still in the news log, which the page's own rules filter.
    assert {e["kind"] for e in W.news_events(came, back)} == kinds


def test_two_days_of_silence_opens_one_issue_and_the_return_is_news():
    s0 = W._snapshot_state(inv("2026-10-01T12:00:00"), set(), now=at("2026-10-03T18:00:00"))  # 46 h
    s1 = W._snapshot_state(inv("2026-10-01T12:00:00"), set(), now=at("2026-10-03T22:00:00"))  # 50 h
    s2 = W._snapshot_state(inv("2026-10-01T12:00:00"), set(), now=at("2026-10-04T22:00:00"))  # 74 h
    first = notable(diff(s0, s1))
    assert [c["kind"] for c in first] == ["sensor silent two days"]
    assert "2.1 days" in first[0]["note"]
    assert not notable(diff(s1, s2))                          # once, not every check
    s3 = W._snapshot_state(inv("2026-10-05T10:00:00", count=1400), set(), now=at("2026-10-05T20:00:00"))
    back = notable(diff(s2, s3))
    assert {c["kind"] for c in back} == {"sensor resumed", "dormant sensor received an upload"}


def test_a_hand_collected_logger_going_quiet_is_never_an_alert():
    s0 = W._snapshot_state(inv("2026-09-28T15:31:00", site="Camp Richardson", stype="Minidot"),
                           set(), now=at("2026-09-30T18:00:00"))
    s1 = W._snapshot_state(inv("2026-09-28T15:31:00", site="Camp Richardson", stype="Minidot"),
                           set(), now=at("2026-09-30T23:30:00"))
    assert not notable(diff(s0, s1))


def test_a_boat_trip_upload_is_still_news():
    # A dormant hand-collected logger receiving two months of data stays notable.
    s0 = W._snapshot_state(inv("2026-07-28T15:31:00", site="Camp Richardson", stype="Minidot",
                               count=100_000), set(), now=at("2026-09-30T18:00:00"))
    s1 = W._snapshot_state(inv("2026-09-28T15:31:00", site="Camp Richardson", stype="Minidot",
                               count=109_900), set(), now=at("2026-10-01T22:00:00"))
    assert [c["kind"] for c in notable(diff(s0, s1))] == ["dormant sensor received an upload"]
