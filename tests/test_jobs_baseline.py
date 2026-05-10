"""
Tests for jobs._gap_baseline_for: which session-aware gap denominator the
scan should use given a scan time.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.jobs import _gap_baseline_for
from src.scanner import GAP_VS_PREV_CLOSE, GAP_VS_TODAY_OPEN

NYC = ZoneInfo("America/New_York")
NICOSIA = ZoneInfo("Europe/Nicosia")


def test_regular_session_uses_today_open_baseline():
    # 11:00 ET → squarely in 09:30–16:00 regular session
    when = datetime(2026, 5, 7, 11, 0, tzinfo=NYC)
    assert _gap_baseline_for(when) == GAP_VS_TODAY_OPEN


def test_pre_market_uses_prev_close_baseline():
    # 06:00 ET → pre-market (04:00–09:30)
    when = datetime(2026, 5, 7, 6, 0, tzinfo=NYC)
    assert _gap_baseline_for(when) == GAP_VS_PREV_CLOSE


def test_post_market_uses_prev_close_baseline():
    # 17:00 ET → post-market (16:00–20:00)
    when = datetime(2026, 5, 7, 17, 0, tzinfo=NYC)
    assert _gap_baseline_for(when) == GAP_VS_PREV_CLOSE


def test_dead_zone_uses_prev_close_baseline():
    # 23:30 ET → dead zone
    when = datetime(2026, 5, 7, 23, 30, tzinfo=NYC)
    assert _gap_baseline_for(when) == GAP_VS_PREV_CLOSE


def test_open_boundary_uses_today_open():
    # 09:30:00 ET — exact open. Per inclusive lower bound, regular session.
    when = datetime(2026, 5, 7, 9, 30, tzinfo=NYC)
    assert _gap_baseline_for(when) == GAP_VS_TODAY_OPEN


def test_close_boundary_uses_prev_close():
    # 16:00:00 ET — exact close. Per exclusive upper bound, post-market.
    when = datetime(2026, 5, 7, 16, 0, tzinfo=NYC)
    assert _gap_baseline_for(when) == GAP_VS_PREV_CLOSE


def test_baseline_resolves_through_nicosia_input():
    # 17:00 EEST = 10:00 EDT in May → regular session → today's open
    when = datetime(2026, 5, 7, 17, 0, tzinfo=NICOSIA)
    assert _gap_baseline_for(when) == GAP_VS_TODAY_OPEN
    # 06:30 EEST = 23:30 EDT prev day → dead zone → prev close
    when2 = datetime(2026, 5, 8, 6, 30, tzinfo=NICOSIA)
    assert _gap_baseline_for(when2) == GAP_VS_PREV_CLOSE
