"""
우리경영아카데미 강의 스크래퍼
- config.py에서 자격증명 로드 (앱 UI에서 입력)
- 세션 쿠키 기반 로그인
- 4개 과목 강의 목록 파싱
"""

import os
import re
import logging
from datetime import date, datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.config import get_credentials, get_cost_total

logger = logging.getLogger(__name__)

BASE_URL = "https://www.uricpa.com"
LOGIN_URL = f"{BASE_URL}/GContents/Include/Member/LogIn_Proc.asp"
LOGIN_PAGE = f"{BASE_URL}/"

COURSES = [
    {
        "id": "financial",
        "name": "재무회계",
        "lecture_idx": "10718",
        "color": "#38bdf8",
        "ongoing": False,
    },
    {
        "id": "cost",
        "name": "원가관리",
        "lecture_idx": "10714",
        "color": "#fb923c",
        "ongoing": True,
    },
    {
        "id": "tax_accounting",
        "name": "세무회계",
        "lecture_idx": "10719",
        "color": "#4ade80",
        "ongoing": False,
    },
    {
        "id": "tax_law",
        "name": "세법학",
        "lecture_idx": "10760",
        "color": "#c084fc",
        "ongoing": False,
    },
]

DETAIL_URL = f"{BASE_URL}/GContents/MyClass/MyCourse/TogetherClass/OnLecture/Index3.asp"
COMMON_PARAMS = {
    "BoardMode": "Detail",
    "Order_Idx": "1594289",
    "Goods_Idx": "53750",
    "Item_Kind": "0",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


def parse_minutes(text: str) -> int:
    text = text.strip()
    if not text or text == "-":
        return 0
    total = 0
    h = re.search(r"(\d+)\s*시간", text)
    m = re.search(r"(\d+)\s*분", text)
    if h:
        total += int(h.group(1)) * 60
    if m:
        total += int(m.group(1))
    return total


def parse_date(text: str) -> Optional[date]:
    text = text.strip()
    if not text or text == "-":
        return None
    try:
        return datetime.strptime(text, "%Y.%m.%d").date()
    except ValueError:
        return None


class UriCpaScraper:
    def __init__(self):
        self.client = httpx.Client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        self._logged_in = False

    def login(self) -> bool:
        uid, pwd = get_credentials()
        if not uid or not pwd:
            logger.error("자격증명이 설정되지 않았습니다. 앱에서 로그인 정보를 입력하세요.")
            return False
        try:
            self.client.get(LOGIN_PAGE)
            resp = self.client.post(
                LOGIN_URL,
                data={
                    "Member_ID": uid,
                    "Member_PWD": pwd,
                    "RtnPage": "/Index.asp?",
                    "KeepLogin": "",
                    "SSLYN": "",
                },
            )
            resp.raise_for_status()

            test = self.client.get(
                f"{BASE_URL}/GContents/MyClass/MyCourse/TogetherClass/OnLecture/Index.asp"
            )
            if "로그인이 필요합니다" in test.text:
                logger.error("로그인 실패: 아이디/비밀번호를 확인하세요.")
                return False

            self._logged_in = True
            logger.info("로그인 성공")
            return True
        except Exception as e:
            logger.error(f"로그인 중 오류: {e}")
            return False

    def _ensure_logged_in(self) -> bool:
        if not self._logged_in:
            return self.login()
        return True

    def fetch_course(self, course: dict) -> dict:
        if not self._ensure_logged_in():
            raise RuntimeError("로그인 실패")

        params = {**COMMON_PARAMS, "Lecture_Idx": course["lecture_idx"]}
        url = DETAIL_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())

        try:
            resp = self.client.get(url)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"[{course['name']}] 페이지 요청 실패: {e}")
            raise

        if "로그인이 필요합니다" in resp.text:
            self._logged_in = False
            if not self.login():
                raise RuntimeError("재로그인 실패")
            resp = self.client.get(url)

        soup = BeautifulSoup(resp.text, "lxml")
        return self._parse_course_page(soup, course)

    def _parse_course_page(self, soup: BeautifulSoup, course: dict) -> dict:
        lectures = []
        total_count_from_page = 0
        header_text = soup.get_text()

        m = re.search(r"강의수\s*[\s\S]{0,30}?(\d+)\s*강", header_text)
        if m:
            total_count_from_page = int(m.group(1))

        start_date = None
        m2 = re.search(r"(\d{4}\.\d{2}\.\d{2})\s*~", header_text)
        if m2:
            start_date = parse_date(m2.group(1))

        lecture_table = None
        for t in soup.find_all("table"):
            th_texts = [th.get_text(strip=True) for th in t.find_all("th")]
            if "회차" in th_texts and "강의제목" in th_texts:
                lecture_table = t
                break

        if lecture_table is None:
            logger.warning(f"[{course['name']}] 강의 테이블을 찾지 못했습니다.")
            return self._make_result(course, [], total_count_from_page, start_date)

        rows = lecture_table.find_all("tr")
        header_row = rows[0] if rows else None
        if not header_row:
            return self._make_result(course, [], total_count_from_page, start_date)

        headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
        try:
            idx_ep    = headers.index("회차")
            idx_title = headers.index("강의제목")
            idx_dur   = headers.index("강의시간")
            idx_watch = headers.index("수강한시간")
            idx_date  = headers.index("최종수강일")
        except ValueError:
            logger.warning(f"[{course['name']}] 헤더 컬럼 매핑 실패: {headers}")
            return self._make_result(course, [], total_count_from_page, start_date)

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(idx_ep, idx_title, idx_dur, idx_watch, idx_date):
                continue

            episode_text = cells[idx_ep].get_text(strip=True)
            ep_match = re.search(r"(\d+)\s*강", episode_text)
            if not ep_match:
                continue
            episode_num = int(ep_match.group(1))

            title        = cells[idx_title].get_text(strip=True)
            duration_min = parse_minutes(cells[idx_dur].get_text(strip=True))
            watched_min  = parse_minutes(cells[idx_watch].get_text(strip=True))
            last_date    = parse_date(cells[idx_date].get_text(strip=True))

            lectures.append({
                "episode":      episode_num,
                "title":        title,
                "duration_min": duration_min,
                "watched_min":  watched_min,
                "last_date":    last_date.isoformat() if last_date else None,
            })

        return self._make_result(course, lectures, total_count_from_page, start_date)

    def _make_result(self, course, lectures, total_from_page, start_date):
        if course["id"] == "cost":
            total_lectures = get_cost_total()
        else:
            total_lectures = total_from_page if total_from_page > 0 else len(lectures)

        if lectures:
            max_completed_ep = max(
                (l["episode"] for l in lectures if l["last_date"]),
                default=0,
            )
        else:
            max_completed_ep = 0

        completed_count = 0
        dates_seen = []

        for lec in lectures:
            is_done = lec["last_date"] is not None or lec["episode"] <= max_completed_ep
            if is_done:
                completed_count += 1
            if lec["last_date"]:
                dates_seen.append(lec["last_date"])

        first_date = min(dates_seen) if dates_seen else None

        return {
            "id":               course["id"],
            "name":             course["name"],
            "color":            course["color"],
            "ongoing":          course["ongoing"],
            "total_lectures":   total_lectures,
            "total_from_page":  total_from_page,
            "completed":        completed_count,
            "lectures":         lectures,
            "start_date":       start_date.isoformat() if start_date else None,
            "first_watched_date": first_date,
        }

    def fetch_all(self) -> dict:
        results = []
        errors = []
        for course in COURSES:
            try:
                logger.info(f"[{course['name']}] 스크래핑 시작")
                data = self.fetch_course(course)
                results.append(data)
                logger.info(f"[{course['name']}] 완료: {data['completed']}/{data['total_lectures']}강")
            except Exception as e:
                logger.error(f"[{course['name']}] 스크래핑 실패: {e}")
                errors.append({"course": course["name"], "error": str(e)})

        return {
            "courses":    results,
            "errors":     errors,
            "scraped_at": datetime.now().isoformat(),
        }

    def close(self):
        self.client.close()
