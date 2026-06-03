"""
진도율 및 완강 예상일 계산

규칙:
- 오후 11시 이후 새로고침 → 오늘 포함 (당일분 고려)
- 그 외 → 어제까지 기준
- 완료 강의 시간 = duration_min (실제 강의 길이, watched_min 아님)
- 원가관리 미업로드분 = 업로드된 강의 평균 시간으로 추산
- 평일/주말 평균 수강량을 분리 계산하여, 미래 달력에 따른 시뮬레이션 기반 완강일 도출
- 사용자가 설정한 제외 일자(여행 등)는 통계 및 경과일 계산에서 배제
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Optional


# ── 유틸 ─────────────────────────────────────────────────────

def _get_cutoff() -> tuple[date, date]:
    """(today, cutoff) — 23시 이후면 cutoff=today, 그 외 cutoff=yesterday"""
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    today = now.date()
    return today, today if now.hour >= 23 else today - timedelta(days=1)


def simulate_finish_date(remaining: float, wd_rate: float, we_rate: float, today_date: date) -> tuple[Optional[int], Optional[str]]:
    """
    오늘 이후 날짜로 하루씩 시뮬레이션하며 남은 수량이 0 이하가 되는 날을 구합니다.
    """
    if remaining <= 0:
        return 0, today_date.isoformat()
    
    # 주당 평균이 0 이하인 경우 무한루프 방지
    weekly_sum = 5 * wd_rate + 2 * we_rate
    if weekly_sum <= 0:
        return None, None
    
    curr = today_date
    val = remaining
    days = 0
    while val > 0:
        curr += timedelta(days=1)
        days += 1
        if curr.weekday() < 5:  # 평일
            val -= wd_rate
        else:                  # 주말
            val -= we_rate
            
        if days > 3650:  # 최대 10년 안전장치
            return None, None
            
    return days, curr.isoformat()


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

def analyze_weekday_weekend(date_counts: Counter, time_per_day: Counter, first_date: Optional[date], cutoff: date, excluded_dates: list[str]) -> dict:
    """
    first_date부터 cutoff까지의 날짜 중 제외 일자를 빼고
    평일과 주말의 하루 평균 강의수 및 평균 수강시간을 집계합니다.
    """
    wd_lec, we_lec = [], []
    wd_hrs, we_hrs = [], []
    
    ex_set = set(excluded_dates)

    if not first_date or first_date > cutoff:
        return {
            "weekday_avg_lecs": 0.0,
            "weekend_avg_lecs": 0.0,
            "weekday_avg_hours": 0.0,
            "weekend_avg_hours": 0.0,
            "weekday_days": 0,
            "weekend_days": 0,
        }

    curr = first_date
    while curr <= cutoff:
        curr_str = curr.isoformat()
        if curr_str in ex_set:
            curr += timedelta(days=1)
            continue
            
        cnt = date_counts.get(curr_str, 0)
        hrs = time_per_day.get(curr_str, 0) / 60.0
        
        if curr.weekday() < 5:  # 월~금
            wd_lec.append(cnt)
            wd_hrs.append(hrs)
        else:                  # 토~일
            we_lec.append(cnt)
            we_hrs.append(hrs)
            
        curr += timedelta(days=1)

    def avg(lst): return round(sum(lst) / len(lst), 2) if lst else 0.0

    return {
        "weekday_avg_lecs":  avg(wd_lec),
        "weekend_avg_lecs":  avg(we_lec),
        "weekday_avg_hours": avg(wd_hrs),
        "weekend_avg_hours": avg(we_hrs),
        "weekday_days":      len(wd_lec),
        "weekend_days":      len(we_lec),
    }


# ── 메인 계산 ─────────────────────────────────────────────────

def calculate_progress(courses: list, excluded_dates: Optional[list[str]] = None) -> dict:
    today, cutoff = _get_cutoff()
    ex_dates = excluded_dates or []
    ex_set = set(ex_dates)

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

    # 제외일을 제외한 누적 경과일수 계산
    days_elapsed = 0
    if first_date and first_date <= cutoff:
        curr = first_date
        while curr <= cutoff:
            if curr.isoformat() not in ex_set:
                days_elapsed += 1
            curr += timedelta(days=1)

    # ── 전체 평균 (제외일 제외) ─────────────────────────────────
    total_lec_to_cutoff = sum(cnt for d, cnt in date_counts.items() if d not in ex_set)
    total_min_to_cutoff = sum(mins for d, mins in time_per_day.items() if d not in ex_set)

    overall_avg   = (total_lec_to_cutoff / days_elapsed) if days_elapsed > 0 else 0.0
    overall_avg_h = (total_min_to_cutoff / 60.0 / days_elapsed) if days_elapsed > 0 else 0.0

    # ── 평일/주말 평균 수강 패턴 추출 ─────────────────────────
    weekday_stats = analyze_weekday_weekend(date_counts, time_per_day, first_date, cutoff, ex_dates)

    wd_lec_rate = weekday_stats["weekday_avg_lecs"]
    we_lec_rate = weekday_stats["weekend_avg_lecs"]
    wd_hour_rate = weekday_stats["weekday_avg_hours"]
    we_hour_rate = weekday_stats["weekend_avg_hours"]

    # ── 달력 시뮬레이션 기반 완강일 ───────────────────────────
    sim_days_lec, sim_finish_lec = simulate_finish_date(remaining, wd_lec_rate, we_lec_rate, today)
    sim_days_hour, sim_finish_hour = simulate_finish_date(remaining_hours, wd_hour_rate, we_hour_rate, today)

    # ── 이번 주 vs 지난 주 비교 (WoW) (제외일 반영) ────────────
    this_monday = cutoff - timedelta(days=cutoff.weekday())
    last_monday = this_monday - timedelta(days=7)

    last_week_wd_days = [last_monday + timedelta(days=i) for i in range(5) if (last_monday + timedelta(days=i)).isoformat() not in ex_set]
    last_week_we_days = [last_monday + timedelta(days=i) for i in [5, 6] if (last_monday + timedelta(days=i)).isoformat() not in ex_set]

    this_week_wd_days = [this_monday + timedelta(days=i) for i in range(5) if this_monday + timedelta(days=i) <= cutoff and (this_monday + timedelta(days=i)).isoformat() not in ex_set]
    this_week_we_days = [this_monday + timedelta(days=i) for i in [5, 6] if this_monday + timedelta(days=i) <= cutoff and (this_monday + timedelta(days=i)).isoformat() not in ex_set]

    def _get_days_sum(days_list):
        lecs = sum(date_counts.get(d.isoformat(), 0) for d in days_list)
        mins = sum(time_per_day.get(d.isoformat(), 0) for d in days_list)
        return lecs, mins

    last_wd_lecs, last_wd_mins = _get_days_sum(last_week_wd_days)
    last_we_lecs, last_we_mins = _get_days_sum(last_week_we_days)

    n_l_wd = len(last_week_wd_days)
    last_wd_avg_lecs = round(last_wd_lecs / n_l_wd, 2) if n_l_wd > 0 else 0.0
    last_wd_avg_hours = round(last_wd_mins / n_l_wd / 60.0, 2) if n_l_wd > 0 else 0.0

    n_l_we = len(last_week_we_days)
    last_we_avg_lecs = round(last_we_lecs / n_l_we, 2) if n_l_we > 0 else 0.0
    last_we_avg_hours = round(last_we_mins / n_l_we / 60.0, 2) if n_l_we > 0 else 0.0

    this_wd_avg_lecs, this_wd_avg_hours = None, None
    if this_week_wd_days:
        this_wd_lecs, this_wd_mins = _get_days_sum(this_week_wd_days)
        n_wd = len(this_week_wd_days)
        this_wd_avg_lecs = round(this_wd_lecs / n_wd, 2)
        this_wd_avg_hours = round(this_wd_mins / n_wd / 60.0, 2)

    this_we_avg_lecs, this_we_avg_hours = None, None
    if this_week_we_days:
        this_we_lecs, this_we_mins = _get_days_sum(this_week_we_days)
        n_we = len(this_week_we_days)
        this_we_avg_lecs = round(this_we_lecs / n_we, 2)
        this_we_avg_hours = round(this_we_mins / n_we / 60.0, 2)

    wd_lec_delta = round(this_wd_avg_lecs - last_wd_avg_lecs, 2) if this_wd_avg_lecs is not None else None
    wd_hour_delta = round(this_wd_avg_hours - last_wd_avg_hours, 2) if this_wd_avg_hours is not None else None

    we_lec_delta = round(this_we_avg_lecs - last_we_avg_lecs, 2) if this_we_avg_lecs is not None else None
    we_hour_delta = round(this_we_avg_hours - last_we_avg_hours, 2) if this_we_avg_hours is not None else None

    weekly_comparison = {
        "last_week_wd": {
            "avg_lecs": last_wd_avg_lecs,
            "avg_hours": last_wd_avg_hours,
        },
        "this_week_wd": {
            "avg_lecs": this_wd_avg_lecs,
            "avg_hours": this_wd_avg_hours,
            "days_count": len(this_week_wd_days),
        },
        "last_week_we": {
            "avg_lecs": last_we_avg_lecs,
            "avg_hours": last_we_avg_hours,
        },
        "this_week_we": {
            "avg_lecs": this_we_avg_lecs,
            "avg_hours": this_we_avg_hours,
            "days_count": len(this_week_we_days),
        },
        "wd_lec_delta": wd_lec_delta,
        "wd_hour_delta": wd_hour_delta,
        "we_lec_delta": we_lec_delta,
        "we_hour_delta": we_hour_delta,
    }

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
        # 평일/주말 평균 요율
        "daily_avg":         round(overall_avg, 2)   if overall_avg else 0.0,
        "weekday_avg_lecs":  wd_lec_rate,
        "weekend_avg_lecs":  we_lec_rate,
        "weekday_avg_hours": wd_hour_rate,
        "weekend_avg_hours": we_hour_rate,
        # 시뮬레이션 결과 완강 예정일
        "simulated_finish_lecs":  sim_finish_lec,
        "simulated_finish_hours": sim_finish_hour,
        "simulated_days_lecs":     sim_days_lec,
        "simulated_days_hours":    sim_days_hour,
        # (구버전 호환용 기본 예상일 매핑)
        "expected_finish":   sim_finish_lec,
        "expected_finish_7d": sim_finish_lec,
        "expected_finish_3d": sim_finish_lec,
        "expected_finish_7d_h": sim_finish_hour,
        "expected_finish_3d_h": sim_finish_hour,
        # 주말/평일 상세 통계
        "weekday_stats":      weekday_stats,
        "weekly_comparison":  weekly_comparison,
        "courses":            courses,
    }


