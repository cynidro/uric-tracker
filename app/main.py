"""
FastAPI 메인 앱
- 대시보드 렌더링
- 수동 갱신 API
- 1시간 자동 갱신 스케줄러
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.calculator import calculate_progress
from app.scraper import UriCpaScraper

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "data" / "progress.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


# ── 스크래핑 작업 ──────────────────────────────────────────────

def run_scrape():
    """스크래핑 실행 후 JSON 캐시 저장"""
    logger.info("스크래핑 시작...")
    scraper = UriCpaScraper()
    try:
        raw = scraper.fetch_all()
        result = calculate_progress(raw["courses"])
        result["errors"] = raw.get("errors", [])
        result["scraped_at"] = raw["scraped_at"]

        DATA_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            f"스크래핑 완료: {result['total_completed']}/{result['total_lectures']}강"
        )
    except Exception as e:
        logger.error(f"스크래핑 실패: {e}")
    finally:
        scraper.close()


def load_cached() -> dict | None:
    """캐시된 JSON 로드"""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ── 앱 생명주기 ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 스크래핑 (캐시 없으면)
    if not DATA_FILE.exists():
        import threading
        t = threading.Thread(target=run_scrape, daemon=True)
        t.start()

    # 자동 갱신 스케줄러
    interval = int(os.getenv("REFRESH_INTERVAL_MINUTES", "60"))
    scheduler.add_job(run_scrape, "interval", minutes=interval, id="scrape_job")
    scheduler.start()
    logger.info(f"스케줄러 시작: {interval}분마다 자동 갱신")

    yield

    scheduler.shutdown()


# ── FastAPI 앱 ─────────────────────────────────────────────────

app = FastAPI(title="우리경영아카데미 강의 트래커", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    data = load_cached()
    cost_total = int(os.getenv("COST_TOTAL_LECTURES", "65"))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "data": data,
            "cost_total": cost_total,
        },
    )


@app.post("/api/refresh")
async def refresh():
    """수동 갱신 엔드포인트"""
    import threading
    t = threading.Thread(target=run_scrape, daemon=True)
    t.start()
    return JSONResponse({"status": "refreshing", "message": "스크래핑 시작됨 (약 30초 소요)"})


@app.get("/api/data")
async def get_data():
    """현재 캐시된 데이터 JSON 반환"""
    data = load_cached()
    if data is None:
        return JSONResponse({"error": "데이터 없음. 갱신을 눌러주세요."}, status_code=404)
    return JSONResponse(data)


@app.patch("/api/cost-total")
async def update_cost_total(body: dict):
    """원가관리 총 강의수 업데이트 (ENV 파일 직접 수정 + 재계산)"""
    new_total = int(body.get("total", 65))
    if new_total < 1:
        return JSONResponse({"error": "유효하지 않은 값"}, status_code=400)

    # .env 파일 업데이트 (프로젝트 루트 또는 /app)
    for env_path in [Path("/app/.env"), Path(__file__).parent.parent / ".env"]:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            import re
            content = re.sub(
                r"COST_TOTAL_LECTURES=\d+",
                f"COST_TOTAL_LECTURES={new_total}",
                content,
            )
            env_path.write_text(content, encoding="utf-8")
            break

    os.environ["COST_TOTAL_LECTURES"] = str(new_total)

    # 캐시 데이터 재계산
    data = load_cached()
    if data:
        for c in data["courses"]:
            if c["id"] == "cost":
                c["total_lectures"] = new_total
                c["progress_pct"] = round(c["completed"] / new_total * 100, 1) if new_total > 0 else 0.0
        # 전체 재계산
        recalc = calculate_progress(data["courses"])
        recalc["errors"] = data.get("errors", [])
        recalc["scraped_at"] = data.get("scraped_at", "")
        DATA_FILE.write_text(
            json.dumps(recalc, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return JSONResponse({"status": "ok", "cost_total": new_total})


@app.get("/health")
async def health():
    return {"status": "ok"}
