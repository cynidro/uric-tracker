"""
진도율 및 완강 예상일 계산

규칙:
- 오후 11시 이후 새로고침 → 오늘 포함 (당일분 고려)
- 그 외 → 어제까지 기준
- 완료 강의 시간 = duration_min (실제 강의 길이, watched_min 아님)
- 원가관리 미업로드분 = 업로드된 강의 평균 시간으로 추산
"""

from collections import Counter
from datetime import date, datetime, timedelta
from math import ceil
from typing import Optional


# ── 유틸 ─────────────────────────────────────────────────────

def _get_cutoff() -> tuple[date, date]:
    """(today, cutoff) — 23시 이후면 cutoff=today, 그 외 cutoff=yesterday"""
    now = datetime.now()
    today = now.date()
    return today, today if now.hour >= 23 else today - timedelta(days=1)


def _window_pace(counts: Counter, window: int, cutoff: date) -> Optional[float]:
    """cutoff 기준 최근 window일 평균"""
    start = (cutoff - timedelta(days=window - 1)).isoformat()
    end   = cutoff.isoformat()
    total = sum(v for k, v in counts.items() if start <= k <= end)
    return total / window


def _finish(pace: Optional[float], remaining: float, today: date):
    """잔여 / 페이스 → (잔여일, 완강일 str)"""
    if not pace or pace <= 0 or remaining <= 0:
        return None, None
    days = ceil(remaining / pace)
    return days, (today + timedelta(days=days)).isoformat()


# ── 잔여 시간 계산 ────────────────────────────────────────────

def calc_remaining_hours(courses: list) -> float:
    """
    잔여 강의 총 시간 (시간 단위)
    - 업로드된 잔여 강의: duration_min 합산
    - 원가관리 미업로드: avg_duration_min * 미업로드 수
    """
    total_min = 0.0
    for c in courses:
        lectures  = c.get("lectures", [])
        avg_dur   = c.get("avg_duration_min", 60.0)
        total_cnt = c["total_lectures"]

        if not lectures:
            total_min += avg_dur * max(0, total_cnt - c["completed"])
            continue

        max_ep    = max((l["episode"] for l in lectures if l["last_date"]), default=0)
        done_eps  = {l["episode"] for l in lectures
                     if l["last_date"] or l["episode"] <= max_ep}

        # 업로드된 잔여 강의
        total_min += sum(l["duration_min"] for l in lectures
                         if l["episode"] not in done_eps)
        # 미업로드 잔여 (원가관리)
        not_uploaded = max(0, total_cnt - len(lectures))
        total_min += avg_dur * not_uploaded

    return total_min / 60.0


# ── 주말/평일 분석 ────────────────────────────────────────────

def analyze_weekday_weekend(date_counts: Counter, time_per_day: Counter) -> dict:
    wd_lec, we_lec = [], []
    wd_hrs, we_hrs = [], []

    for d_str, cnt in date_counts.items():
        d   = date.fromisoformat(d_str)
        hrs = time_per_day.get(d_str, 0) / 60.0
        if d.weekday() < 5:          # 월~금
            wd_lec.append(cnt); wd_hrs.append(hrs)
        else:                         # 토~일
            we_lec.append(cnt); we_hrs.append(hrs)

    def avg(lst): return round(sum(lst) / len(lst), 2) if lst else None

    return {
        "weekday_avg_lecs":  avg(wd_lec),
        "weekend_avg_lecs":  avg(we_lec),
        "weekday_avg_hours": avg(wd_hrs),
        "weekend_avg_hours": avg(we_hrs),
        "weekday_days":      len(wd_lec),
        "weekend_days":      len(we_lec),
    }


# ── 메인 계산 ─────────────────────────────────────────────────

def calculate_progress(courses: list) -> dict:
    today, cutoff = _get_cutoff()

    # ── 날짜별 강의수 / 시간 집계 (cutoff까지) ──────────────
    date_counts: Counter = Counter()   # 날짜 → 완료 강의수
    time_per_day: Counter = Counter()  # 날짜 → duration_min 합

    for c in courses:
        for lec in c.get("lectures", []):
            d = lec.get("last_date")
            if d and d <= cutoff.isoformat():
                date_counts[d]  += 1
                time_per_day[d] += lec.get("duration_min", 0)

    # ── 기본 통계 ────────────────────────────────────────────
    total_completed = sum(c["completed"] for c in courses)
    total_lectures  = sum(c["total_lectures"] for c in courses)
    remaining       = max(0, total_lectures - total_completed)
    remaining_hours = round(calc_remaining_hours(courses), 1)

    first_dates = [c["first_watched_date"] for c in courses
                   if c.get("first_watched_date")]
    first_date  = date.fromisoformat(min(first_dates)) if first_dates else None

    days_elapsed = 0
    if first_date and first_date <= cutoff:
        days_elapsed = (cutoff - first_date).days + 1

    # ── 전체 평균 ─────────────────────────────────────────────
    total_lec_to_cutoff = sum(date_counts.values())
    total_min_to_cutoff = sum(time_per_day.values())

    overall_avg   = (total_lec_to_cutoff / days_elapsed) if days_elapsed > 0 else None
    overall_avg_h = (total_min_to_cutoff / 60.0 / days_elapsed) if days_elapsed > 0 else None

    # ── 윈도우 페이스 ─────────────────────────────────────────
    pace_7d   = _window_pace(date_counts,  7, cutoff)
    pace_3d   = _window_pace(date_counts,  3, cutoff)
    pace_7d_m = _window_pace(time_per_day, 7, cutoff)   # 분 단위
    pace_3d_m = _window_pace(time_per_day, 3, cutoff)
    pace_7d_h = pace_7d_m / 60.0 if pace_7d_m is not None else None
    pace_3d_h = pace_3d_m / 60.0 if pace_3d_m is not None else None

    # ── 완강 예상 (강의 기준) ─────────────────────────────────
    d_all, f_all = _finish(overall_avg, remaining, today)
    d_7d,  f_7d  = _finish(pace_7d,    remaining, today)
    d_3d,  f_3d  = _finish(pace_3d,    remaining, today)

    # ── 완강 예상 (시간 기준) ─────────────────────────────────
    dh_7d, fh_7d = _finish(pace_7d_h, remaining_hours, today)
    dh_3d, fh_3d = _finish(pace_3d_h, remaining_hours, today)

    # ── 주말/평일 분석 ────────────────────────────────────────
    weekday_stats = analyze_weekday_weekend(date_counts, time_per_day)

    # ── 과목별 진도율 ─────────────────────────────────────────
    for c in courses:
        t = c["total_lectures"]
        c["progress_pct"] = round(c["completed"] / t * 100, 1) if t > 0 else 0.0

    overall_pct = round(total_completed / total_lectures * 100, 1) if total_lectures > 0 else 0.0

    return {
        # 기본
        "total_lectures":    total_lectures,
        "total_completed":   total_completed,
        "remaining":         remaining,
        "remaining_hours":   remaining_hours,
        "overall_pct":       overall_pct,
        "first_date":        first_date.isoformat() if first_date else None,
        "days_elapsed":      days_elapsed,
        "today":             today.isoformat(),
        "cutoff":            cutoff.isoformat(),
        # 강의 기준 페이스
        "daily_avg":         round(overall_avg, 2)   if overall_avg else None,
        "pace_7d":           round(pace_7d, 2)       if pace_7d    else None,
        "pace_3d":           round(pace_3d, 2)       if pace_3d    else None,
        "days_to_finish":    d_all,
        "days_to_finish_7d": d_7d,
        "days_to_finish_3d": d_3d,
        "expected_finish":   f_all,
        "expected_finish_7d": f_7d,
        "expected_finish_3d": f_3d,
        # 시간 기준 페이스 (7일/3일)
        "pace_7d_hours":          round(pace_7d_h, 2) if pace_7d_h else None,
        "pace_3d_hours":          round(pace_3d_h, 2) if pace_3d_h else None,
        "days_to_finish_7d_h":    dh_7d,
        "days_to_finish_3d_h":    dh_3d,
        "expected_finish_7d_h":   fh_7d,
        "expected_finish_3d_h":   fh_3d,
        # 주말/평일
        "weekday_stats": weekday_stats,
        "courses":       courses,
    }
