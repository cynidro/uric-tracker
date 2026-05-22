"""
진도율 및 완강 예상일 계산 모듈
"""

import os
from datetime import date, timedelta
from math import ceil
from typing import Optional


def calculate_progress(courses: list) -> dict:
    """
    스크래핑된 과목 데이터를 바탕으로
    하루 평균 수강 강의수, 잔여 강의수, 예상 완료일 계산
    """
    total_completed = sum(c["completed"] for c in courses)
    total_lectures = sum(c["total_lectures"] for c in courses)
    remaining = max(0, total_lectures - total_completed)

    # 수강 시작일 = 모든 과목에서 가장 이른 최초 수강일
    first_dates = [
        c["first_watched_date"]
        for c in courses
        if c.get("first_watched_date")
    ]
    first_date: Optional[date] = None
    if first_dates:
        first_date = date.fromisoformat(min(first_dates))

    today = date.today()

    # 경과일 및 하루 평균
    daily_avg: Optional[float] = None
    days_elapsed: int = 0
    if first_date and first_date < today:
        days_elapsed = (today - first_date).days
        if days_elapsed > 0 and total_completed > 0:
            daily_avg = total_completed / days_elapsed

    # 예상 완료일
    expected_finish: Optional[date] = None
    days_to_finish: Optional[int] = None
    if daily_avg and daily_avg > 0:
        days_to_finish = ceil(remaining / daily_avg)
        expected_finish = today + timedelta(days=days_to_finish)

    # 과목별 진도율
    for c in courses:
        total = c["total_lectures"]
        done = c["completed"]
        c["progress_pct"] = round(done / total * 100, 1) if total > 0 else 0.0

    # 전체 진도율
    overall_pct = round(total_completed / total_lectures * 100, 1) if total_lectures > 0 else 0.0

    return {
        "total_lectures": total_lectures,
        "total_completed": total_completed,
        "remaining": remaining,
        "overall_pct": overall_pct,
        "first_date": first_date.isoformat() if first_date else None,
        "days_elapsed": days_elapsed,
        "daily_avg": round(daily_avg, 2) if daily_avg else None,
        "days_to_finish": days_to_finish,
        "expected_finish": expected_finish.isoformat() if expected_finish else None,
        "today": today.isoformat(),
        "courses": courses,
    }
