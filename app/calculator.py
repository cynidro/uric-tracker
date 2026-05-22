"""
진도율 및 완강 예상일 계산
- 오늘 제외 (어제까지 기준)
- 전체 평균 / 최근 7일 / 최근 3일 페이스
"""

from collections import Counter
from datetime import date, timedelta
from math import ceil
from typing import Optional


def _window_pace(date_counts: Counter, today: date, window: int) -> Optional[float]:
    """오늘 제외, window일 전 ~ 어제까지 수강 강의수 / window"""
    cutoff    = (today - timedelta(days=window)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    total = sum(v for k, v in date_counts.items() if cutoff <= k <= yesterday)
    return total / window if window > 0 else None


def _finish(pace: Optional[float], remaining: int, today: date):
    """남은 강의 / 페이스 → (잔여일, 완강일)"""
    if not pace or pace <= 0:
        return None, None
    days = ceil(remaining / pace)
    return days, (today + timedelta(days=days)).isoformat()


def calculate_progress(courses: list) -> dict:
    today     = date.today()
    yesterday = today - timedelta(days=1)

    # ── 전체 강의수 / 완료수 ──────────────────────────────
    total_lectures   = sum(c["total_lectures"] for c in courses)
    total_completed  = sum(c["completed"]       for c in courses)
    remaining        = max(0, total_lectures - total_completed)

    # ── 날짜별 수강 강의수 (오늘 제외) ───────────────────
    # last_date가 있는 강의만 카운트
    date_counts: Counter = Counter()
    for course in courses:
        for lec in course.get("lectures", []):
            d = lec.get("last_date")
            if d and d < today.isoformat():      # 오늘 제외
                date_counts[d] += 1

    # ── 첫 수강일 ────────────────────────────────────────
    first_dates = [c["first_watched_date"] for c in courses if c.get("first_watched_date")]
    first_date: Optional[date] = (
        date.fromisoformat(min(first_dates)) if first_dates else None
    )

    # ── 전체 평균 (첫 수강일 ~ 어제) ─────────────────────
    overall_avg: Optional[float] = None
    days_elapsed = 0
    if first_date and first_date <= yesterday:
        days_elapsed = (yesterday - first_date).days + 1
        completed_excl_today = sum(date_counts.values())
        if days_elapsed > 0:
            overall_avg = completed_excl_today / days_elapsed

    # ── 최근 7일 / 3일 페이스 ────────────────────────────
    pace_7d = _window_pace(date_counts, today, 7)
    pace_3d = _window_pace(date_counts, today, 3)

    days_overall, finish_overall = _finish(overall_avg, remaining, today)
    days_7d,      finish_7d      = _finish(pace_7d,     remaining, today)
    days_3d,      finish_3d      = _finish(pace_3d,     remaining, today)

    # ── 과목별 진도율 ─────────────────────────────────────
    for c in courses:
        total = c["total_lectures"]
        done  = c["completed"]
        c["progress_pct"] = round(done / total * 100, 1) if total > 0 else 0.0

    overall_pct = round(total_completed / total_lectures * 100, 1) if total_lectures > 0 else 0.0

    return {
        "total_lectures":   total_lectures,
        "total_completed":  total_completed,
        "remaining":        remaining,
        "overall_pct":      overall_pct,
        "first_date":       first_date.isoformat() if first_date else None,
        "days_elapsed":     days_elapsed,
        # 전체 평균 (오늘 제외)
        "daily_avg":        round(overall_avg, 2) if overall_avg else None,
        "days_to_finish":   days_overall,
        "expected_finish":  finish_overall,
        # 최근 7일 페이스
        "pace_7d":          round(pace_7d, 2) if pace_7d else None,
        "days_to_finish_7d":  days_7d,
        "expected_finish_7d": finish_7d,
        # 최근 3일 페이스
        "pace_3d":          round(pace_3d, 2) if pace_3d else None,
        "days_to_finish_3d":  days_3d,
        "expected_finish_3d": finish_3d,
        "today":            today.isoformat(),
        "courses":          courses,
    }
