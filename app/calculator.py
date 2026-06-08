"""
진도율 및 완강 예상일 계산

규칙:
- 오후 11시 이후 새로고침 → 오늘 포함 (당일분 고려)
- 그 외 → 어제까지 기준
- 완료 강의 시간 = duration_min (실제 강의 길이, watched_min 아님)
- 원가관리 미업로드분 = 업로드된 강의 평균 시간으로 추산
- 평일/주말 평균 수강량을 분리 계산하여, 미래 달력에 따른 시뮬레이션 기반 완강일 도출
- 사용자가 설정한 제외 일자(여행 등)는 통계 및 경과일 계산에서 배제
- 표준편차 기반 이상치(Outlier) 제거 및 최근 4주 차별 가중평균 수강 요율 적용
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


# ── 주말/평일 분석 (가중평균 및 이상치 제거) ─────────────────────

def analyze_weekday_weekend_weighted(date_counts: Counter, time_per_day: Counter, first_date: Optional[date], cutoff: date, excluded_dates: list[str]) -> dict:
    """
    1. first_date ~ cutoff 중 제외일이 아닌 날들을 평일/주말로 분리하여 전체 데이터 추출
    2. 평일/주말 데이터의 표준편차 기반 이상치(outlier) 필터링 진행 (평균에서 1.5 표준편차 이상 벗어난 날 제외)
    3. cutoff 기준 최근 1주, 2주, 3주, 4주차로 분류하여 각 주차별 일평균 수강량 산출
    4. 0.4, 0.3, 0.2, 0.1 가중치를 곱해 가중평균 요율 계산 (유효 데이터 주차만 정규화하여 반영)
    """
    ex_set = set(excluded_dates)
    
    if not first_date or first_date > cutoff:
        return {
            "weekday_avg_lecs": 0.0,
            "weekend_avg_lecs": 0.0,
            "weekday_avg_hours": 0.0,
            "weekend_avg_hours": 0.0,
        }

    # 전체 기간 유효 날짜 목록 구성
    wd_dates_all, we_dates_all = [], []
    curr = first_date
    while curr <= cutoff:
        curr_str = curr.isoformat()
        if curr_str not in ex_set:
            if curr.weekday() < 5:
                wd_dates_all.append(curr)
            else:
                we_dates_all.append(curr)
        curr += timedelta(days=1)

    # 전체 데이터 리스트 (이상치 필터링용)
    wd_lecs_raw = [date_counts.get(d.isoformat(), 0) for d in wd_dates_all]
    we_lecs_raw = [date_counts.get(d.isoformat(), 0) for d in we_dates_all]
    wd_hours_raw = [time_per_day.get(d.isoformat(), 0) / 60.0 for d in wd_dates_all]
    we_hours_raw = [time_per_day.get(d.isoformat(), 0) / 60.0 for d in we_dates_all]

    # 이상치 필터링 범위 계산
    def get_bounds(lst, m=1.5):
        if len(lst) < 5:
            return None, None
        n = len(lst)
        mean = sum(lst) / n
        var = sum((x - mean) ** 2 for x in lst) / n
        std = var ** 0.5
        return mean - m * std, mean + m * std

    wd_lec_min, wd_lec_max = get_bounds(wd_lecs_raw)
    we_lec_min, we_lec_max = get_bounds(we_lecs_raw)
    wd_hr_min, wd_hr_max = get_bounds(wd_hours_raw)
    we_hr_min, we_hr_max = get_bounds(we_hours_raw)

    def is_valid_wd_lec(d):
        val = date_counts.get(d.isoformat(), 0)
        if wd_lec_min is not None:
            return wd_lec_min <= val <= wd_lec_max
        return True

    def is_valid_we_lec(d):
        val = date_counts.get(d.isoformat(), 0)
        if we_lec_min is not None:
            return we_lec_min <= val <= we_lec_max
        return True

    def is_valid_wd_hr(d):
        val = time_per_day.get(d.isoformat(), 0) / 60.0
        if wd_hr_min is not None:
            return wd_hr_min <= val <= wd_hr_max
        return True

    def is_valid_we_hr(d):
        val = time_per_day.get(d.isoformat(), 0) / 60.0
        if we_hr_min is not None:
            return we_hr_min <= val <= we_hr_max
        return True

    # 4개 주차별 날짜 범위 (1주차: cutoff~cutoff-6, ...)
    weeks = [
        (cutoff - timedelta(days=6), cutoff),
        (cutoff - timedelta(days=13), cutoff - timedelta(days=8)),
        (cutoff - timedelta(days=20), cutoff - timedelta(days=14)),
        (cutoff - timedelta(days=27), cutoff - timedelta(days=21))
    ]
    weights = [0.4, 0.3, 0.2, 0.1]

    def get_week_avg(start_d, end_d, is_weekend, filter_func, value_func):
        valid_days = []
        c = start_d
        while c <= end_d:
            c_str = c.isoformat()
            if c_str not in ex_set and c >= first_date:
                match_day = (c.weekday() >= 5) if is_weekend else (c.weekday() < 5)
                if match_day and filter_func(c):
                    valid_days.append(c)
            c += timedelta(days=1)
        
        if not valid_days:
            return None
        return sum(value_func(d) for d in valid_days) / len(valid_days)

    wd_lec_vals = [get_week_avg(w[0], w[1], False, is_valid_wd_lec, lambda d: date_counts.get(d.isoformat(), 0)) for w in weeks]
    we_lec_vals = [get_week_avg(w[0], w[1], True, is_valid_we_lec, lambda d: date_counts.get(d.isoformat(), 0)) for w in weeks]
    wd_hr_vals = [get_week_avg(w[0], w[1], False, is_valid_wd_hr, lambda d: time_per_day.get(d.isoformat(), 0) / 60.0) for w in weeks]
    we_hr_vals = [get_week_avg(w[0], w[1], True, is_valid_we_hr, lambda d: time_per_day.get(d.isoformat(), 0) / 60.0) for w in weeks]

    def calc_weighted_sum(vals):
        valid_idx = [i for i, v in enumerate(vals) if v is not None]
        if not valid_idx:
            return 0.0
        sum_weights = sum(weights[i] for i in valid_idx)
        weighted_val = sum(vals[i] * weights[i] for i in valid_idx)
        return round(weighted_val / sum_weights, 2)

    return {
        "weekday_avg_lecs":  calc_weighted_sum(wd_lec_vals),
        "weekend_avg_lecs":  calc_weighted_sum(we_lec_vals),
        "weekday_avg_hours": calc_weighted_sum(wd_hr_vals),
        "weekend_avg_hours": calc_weighted_sum(we_hr_vals),
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

    # ── 평일/주말 평균 수강 패턴 추출 (이상치 필터링 + 가중평균) ──
    weighted_stats = analyze_weekday_weekend_weighted(date_counts, time_per_day, first_date, cutoff, ex_dates)

    wd_lec_rate = weighted_stats["weekday_avg_lecs"]
    we_lec_rate = weighted_stats["weekend_avg_lecs"]
    wd_hour_rate = weighted_stats["weekday_avg_hours"]
    we_hour_rate = weighted_stats["weekend_avg_hours"]

    # ── 달력 시뮬레이션 기반 완강일 ───────────────────────────
    sim_days_lec, sim_finish_lec = simulate_finish_date(remaining, wd_lec_rate, we_lec_rate, today)
    sim_days_hour, sim_finish_hour = simulate_finish_date(remaining_hours, wd_hour_rate, we_hour_rate, today)

    # ── 과목별 개별 완강 예상일 시뮬레이션 ────────────────────────
    for c in courses:
        c_rem = max(0, c["total_lectures"] - c["completed"])
        c_rem_h = 0.0
        lectures = c.get("lectures", [])
        avg_dur = c.get("avg_duration_min", 60.0)
        
        if not lectures:
            c_rem_h = (avg_dur * c_rem) / 60.0
        else:
            max_ep = max((l["episode"] for l in lectures if l["last_date"]), default=0)
            done_eps = {l["episode"] for l in lectures if l["last_date"] or l["episode"] <= max_ep}
            c_rem_h += sum(l["duration_min"] for l in lectures if l["episode"] not in done_eps)
            not_uploaded = max(0, c["total_lectures"] - len(lectures))
            c_rem_h += avg_dur * not_uploaded
            c_rem_h /= 60.0

        # 개별 완강일 계산
        sim_days_lec, c_finish_lec = simulate_finish_date(c_rem, wd_lec_rate, we_lec_rate, today)
        sim_days_hour, c_finish_hour = simulate_finish_date(c_rem_h, wd_hour_rate, we_hour_rate, today)
        
        c["simulated_finish_lecs"] = c_finish_lec if c_rem > 0 else today.isoformat()
        c["simulated_finish_hours"] = c_finish_hour if c_rem_h > 0 else today.isoformat()
        c["simulated_days_lecs"] = sim_days_lec or 0
        c["simulated_days_hours"] = sim_days_hour or 0

        # 최근 14일 동안 공부한 이력이 있는지 체크하여 현재 적극 수강 중인지 판별
        is_active = False
        if c["completed"] < c["total_lectures"]:
            limit_date = (cutoff - timedelta(days=14)).isoformat()
            for lec in lectures:
                ld = lec.get("last_date")
                if ld and ld >= limit_date:
                    is_active = True
                    break
        c["is_active"] = is_active

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
        "daily_avg":         round((total_completed / days_elapsed) if days_elapsed > 0 else 0.0, 2),
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
        # 과목 정보 및 코스 목록
        "courses":            courses,
    }
