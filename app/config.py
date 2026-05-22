"""
자격증명 및 앱 설정 관리
- app/data/config.json에 저장 (볼륨 마운트로 영속)
- .env / 환경변수 불필요
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent / "data" / "config.json"


def load_config() -> dict:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(data: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_credentials() -> tuple[str, str]:
    cfg = load_config()
    return cfg.get("uricpa_id", ""), cfg.get("uricpa_pw", "")


def has_credentials() -> bool:
    uid, pw = get_credentials()
    return bool(uid and pw)


def get_cost_total() -> int:
    cfg = load_config()
    return int(cfg.get("cost_total_lectures", 65))


def set_cost_total(n: int):
    cfg = load_config()
    cfg["cost_total_lectures"] = n
    save_config(cfg)


def get_refresh_interval() -> int:
    cfg = load_config()
    return int(cfg.get("refresh_interval_minutes", 60))
