"""
FastAPI 메인 앱
- 자격증명 없으면 설정 화면 표시
- /api/setup : 자격증명 저장
- /api/refresh : 수동 갱신
- /api/cost-total : 원가관리 총 강의수 수정
"""

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.calculator import calculate_progress
from app.config import (
    get_cost_total, get_refresh_interval, has_credentials,
    load_config, save_config, set_cost_total,
)
from app.scraper import UriCpaScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "data" / "progress.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


# ── 스크래핑 ───────────────────────────────────────────────────

def run_scrape():
    if not has_credentials():
        logger.warning("자격증명 없음 — 스크래핑 건너뜀")
        return
    logger.info("스크래핑 시작...")
    scraper = UriCpaScraper()
    try:
        raw    = scraper.fetch_all()
        result = calculate_progress(raw["courses"])
        result["errors"]     = raw.get("errors", [])
        result["scraped_at"] = raw["scraped_at"]
        DATA_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"완료: {result['total_completed']}/{result['total_lectures']}강")
    except Exception as e:
        logger.error(f"스크래핑 실패: {e}")
    finally:
        scraper.close()


def load_cached() -> dict | None:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ── 앱 생명주기 ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if has_credentials() and not DATA_FILE.exists():
        threading.Thread(target=run_scrape, daemon=True).start()

    interval = get_refresh_interval()
    scheduler.add_job(run_scrape, "interval", minutes=interval, id="scrape_job")
    scheduler.start()
    logger.info(f"스케줄러: {interval}분마다 자동 갱신")

    yield
    scheduler.shutdown()


# ── 앱 ────────────────────────────────────────────────────────

app = FastAPI(title="우리경영아카데미 강의 트래커", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    data      = load_cached()
    cfg       = load_config()
    logged_in = has_credentials()
    cost_total = get_cost_total()
    return templates.TemplateResponse("index.html", {
        "request":    request,
        "data":       data,
        "logged_in":  logged_in,
        "cost_total": cost_total,
        "saved_id":   cfg.get("uricpa_id", ""),
    })


@app.post("/api/setup")
async def setup(body: dict):
    """자격증명 저장 후 즉시 스크래핑"""
    uid = str(body.get("id", "")).strip()
    pw  = str(body.get("pw", "")).strip()
    if not uid or not pw:
        return JSONResponse({"error": "아이디와 비밀번호를 입력하세요."}, status_code=400)

    cfg = load_config()
    cfg["uricpa_id"] = uid
    cfg["uricpa_pw"] = pw
    save_config(cfg)

    threading.Thread(target=run_scrape, daemon=True).start()
    return JSONResponse({"status": "ok", "message": "저장됨. 스크래핑 시작 (약 30초)..."})


@app.post("/api/refresh")
async def refresh():
    if not has_credentials():
        return JSONResponse({"error": "먼저 로그인 정보를 입력하세요."}, status_code=400)
    threading.Thread(target=run_scrape, daemon=True).start()
    return JSONResponse({"status": "refreshing"})


@app.get("/api/data")
async def get_data():
    data = load_cached()
    if data is None:
        return JSONResponse({"error": "데이터 없음"}, status_code=404)
    return JSONResponse(data)


@app.patch("/api/cost-total")
async def update_cost_total(body: dict):
    n = int(body.get("total", 65))
    if n < 1:
        return JSONResponse({"error": "유효하지 않은 값"}, status_code=400)
    set_cost_total(n)

    # 캐시 재계산
    data = load_cached()
    if data:
        for c in data["courses"]:
            if c["id"] == "cost":
                c["total_lectures"] = n
                c["progress_pct"]   = round(c["completed"] / n * 100, 1) if n > 0 else 0.0
        recalc = calculate_progress(data["courses"])
        recalc["errors"]     = data.get("errors", [])
        recalc["scraped_at"] = data.get("scraped_at", "")
        DATA_FILE.write_text(
            json.dumps(recalc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return JSONResponse({"status": "ok", "cost_total": n})


@app.get("/health")
async def health():
    return {"status": "ok"}
