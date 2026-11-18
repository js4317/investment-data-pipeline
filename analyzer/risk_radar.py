from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

_CACHE_DIR   = Path(__file__).parent.parent / "data" / "risk_radar"
_HISTORY_DIR = Path(__file__).parent.parent / "data" / "risk_radar_history"
_STATS_FILE  = Path(__file__).parent.parent / "data" / "risk_radar_stats.json"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

_MODEL        = "claude-sonnet-4-6"
_CACHE_DAYS   = 7
RADAR_VERSION = "1.1"   # 프롬프트·출력 구조 변경 시 올림 → 기존 캐시 자동 무효화

_VALID_LEVELS       = {"LOW", "MEDIUM", "HIGH"}
_VALID_SOURCE_TYPES = {"news", "공시", "unknown"}

_SYSTEM = """당신은 한국 상장 종목의 비재무적 리스크를 탐지하는 분석 보조 AI입니다.

규칙:
- 웹 검색으로 찾은 사실만 기술합니다. 추정이나 일반론은 사실처럼 서술하지 않습니다.
- 이슈가 없으면 없다고 명시합니다. 억측하지 않습니다.
- 매수/매도/보유 의견은 절대 제시하지 않습니다.
- 점수 재산정, 목표가, 수익률 예측을 하지 않습니다.
- 반드시 JSON 형식으로만 출력합니다. 다른 텍스트 없이 JSON 블록만 반환하세요."""

_PROMPT_TMPL = """\
{name}({ticker})에 대해 다음 4가지 비재무적 리스크를 최근 뉴스와 공시로 조사하세요.{sector_hint}

1. 지배구조(governance): 경영권 분쟁, 오너 리스크, 지배주주 관련 이슈, 내부 갈등
2. 경영진(management): 대표이사 교체/사임, 횡령·배임, 임원 관련 법적 이슈
3. 규제·소송(regulatory): 정부 규제, 공정거래법, 환경/안전, 주요 소송, 제재
4. 업종 리스크(industry): 이 종목 특유의 구조적 업종 리스크 또는 산업 환경 변화

다음 JSON 형식으로만 출력하세요. 마크다운이나 다른 텍스트는 포함하지 마세요.
- sources.type: "news"(뉴스 기사) / "공시"(DART·금감원 공시) / "unknown"
- confidence: "HIGH"=복수 독립 출처로 확인 / "MEDIUM"=1~2개 출처 / "LOW"=출처 없음·일반론

{{
  "overall": "LOW 또는 MEDIUM 또는 HIGH",
  "governance": "LOW 또는 MEDIUM 또는 HIGH",
  "management": "LOW 또는 MEDIUM 또는 HIGH",
  "regulatory": "LOW 또는 MEDIUM 또는 HIGH",
  "industry": "LOW 또는 MEDIUM 또는 HIGH",
  "confidence": "LOW 또는 MEDIUM 또는 HIGH",
  "summary": "발견된 이슈 핵심 요약 2~3줄. 이슈 없으면 없다고 명시.",
  "sources": [
    {{"title": "기사 또는 공시 제목 (또는 URL)", "type": "news 또는 공시 또는 unknown"}}
  ]
}}"""


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker}.json"


def _is_cache_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("radar_version") != RADAR_VERSION:
            return False
        cached = date.fromisoformat(data.get("cached_date", "2000-01-01"))
        return (date.today() - cached).days < _CACHE_DAYS
    except Exception:
        return False


def _read_cache(ticker: str) -> dict | None:
    path = _cache_path(ticker)
    if not _is_cache_valid(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**data["result"], "from_cache": True, "cached_at": data.get("cached_date", ""), "error": None}
    except Exception:
        return None


def _write_cache(ticker: str, result: dict) -> None:
    try:
        _cache_path(ticker).write_text(
            json.dumps(
                {"cached_date": date.today().isoformat(), "radar_version": RADAR_VERSION, "result": result},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def read_cache(ticker: str) -> dict | None:
    """캐시 결과 조회. 유효한 캐시 없으면 None."""
    return _read_cache(ticker)


def clear_cache(ticker: str) -> None:
    path = _cache_path(ticker)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# 히스토리 (주 1회 append — 연구용)
# ---------------------------------------------------------------------------

def _append_history(ticker: str, result: dict) -> None:
    """
    data/risk_radar_history/{ticker}.jsonl 에 결과 1줄 추가.
    scan() 호출 시 신규 API 응답인 경우에만 호출됨 (캐시 히트 시 호출 안 함).
    """
    try:
        entry = {
            "date":       date.today().isoformat(),
            "overall":    result.get("overall"),
            "governance": result.get("governance"),
            "management": result.get("management"),
            "regulatory": result.get("regulatory"),
            "industry":   result.get("industry"),
            "confidence": result.get("confidence"),
            "summary":    result.get("summary"),
        }
        path = _HISTORY_DIR / f"{ticker}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_history(ticker: str) -> list[dict]:
    """ticker의 전체 Risk Radar 이력 반환. 없으면 빈 리스트."""
    path = _HISTORY_DIR / f"{ticker}.jsonl"
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

def _update_stats(cache_hit: bool, success: bool = True, elapsed_sec: float | None = None) -> None:
    try:
        stats: dict = {}
        if _STATS_FILE.exists():
            stats = json.loads(_STATS_FILE.read_text(encoding="utf-8"))

        stats["total_requests"] = stats.get("total_requests", 0) + 1
        stats["cache_hits"]     = stats.get("cache_hits", 0) + (1 if cache_hit else 0)

        if not cache_hit:
            stats["api_calls"]    = stats.get("api_calls", 0) + 1
            stats["api_failures"] = stats.get("api_failures", 0) + (0 if success else 1)

            api_calls = stats["api_calls"]
            failures  = stats["api_failures"]
            stats["success_rate"] = f"{(api_calls - failures) / api_calls * 100:.1f}%" if api_calls else "—"

            if success and elapsed_sec is not None:
                api_success = api_calls - failures
                prev_avg    = stats.get("avg_response_sec", 0.0)
                stats["avg_response_sec"] = round(
                    (prev_avg * (api_success - 1) + elapsed_sec) / api_success, 2
                ) if api_success > 0 else elapsed_sec

        total = stats["total_requests"]
        hits  = stats["cache_hits"]
        stats["cache_hit_rate"] = f"{hits / total * 100:.1f}%" if total else "0%"
        stats["last_updated"]   = datetime.now().strftime("%Y-%m-%d %H:%M")

        _STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_stats() -> dict:
    if not _STATS_FILE.exists():
        return {}
    try:
        return json.loads(_STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 파싱 · 정규화
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return {}


def _normalize_source(src) -> dict:
    if isinstance(src, str):
        return {"title": src, "type": "unknown"}
    if isinstance(src, dict):
        return {
            "title": str(src.get("title", "")).strip() or "—",
            "type":  src.get("type") if src.get("type") in _VALID_SOURCE_TYPES else "unknown",
        }
    return {"title": str(src), "type": "unknown"}


def _normalize(result: dict) -> dict:
    for key in ("overall", "governance", "management", "regulatory", "industry", "confidence"):
        if result.get(key) not in _VALID_LEVELS:
            result[key] = "MEDIUM"
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        result["summary"] = "분석 결과 없음"
    raw = result.get("sources")
    result["sources"] = [_normalize_source(s) for s in raw] if isinstance(raw, list) else []
    return result


# ---------------------------------------------------------------------------
# API 키
# ---------------------------------------------------------------------------

def _load_api_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# 공개 인터페이스
# ---------------------------------------------------------------------------

def scan(ticker: str, name: str, sector: str | None = None) -> dict:
    """
    비재무 리스크 탐지. RADAR_VERSION이 같고 CACHE_DAYS 이내면 캐시 반환.

    Returns:
    {
        "overall":    "LOW|MEDIUM|HIGH",
        "governance": "LOW|MEDIUM|HIGH",
        "management": "LOW|MEDIUM|HIGH",
        "regulatory": "LOW|MEDIUM|HIGH",
        "industry":   "LOW|MEDIUM|HIGH",
        "confidence": "LOW|MEDIUM|HIGH",
        "summary":    str,
        "sources":    list[{"title": str, "type": "news|공시|unknown"}],
        "from_cache": bool,
        "cached_at":  str | None,
        "error":      str | None,
    }
    """
    cached = _read_cache(ticker)
    if cached:
        _update_stats(cache_hit=True)
        return cached

    api_key = _load_api_key()
    if not api_key:
        _update_stats(cache_hit=False, success=False)
        return _error_result("ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    sector_hint = f"\n업종: {sector}" if sector else ""
    prompt      = _PROMPT_TMPL.format(name=name, ticker=ticker, sector_hint=sector_hint)

    last_err = None
    t_start  = time.monotonic()

    for _ in range(2):
        try:
            import anthropic
            client   = anthropic.Anthropic(api_key=api_key)
            response = client.beta.messages.create(
                model      = _MODEL,
                max_tokens = 1024,
                system     = _SYSTEM,
                tools      = [{"type": "web_search_20250305", "name": "web_search"}],
                betas      = ["web-search-2025-03-05"],
                messages   = [{"role": "user", "content": prompt}],
            )
            text = next(
                (block.text for block in reversed(response.content) if hasattr(block, "text")),
                "",
            )
            parsed = _extract_json(text)
            if not parsed:
                _update_stats(cache_hit=False, success=False)
                return _error_result(f"JSON 파싱 실패: {text[:300]}")

            elapsed = time.monotonic() - t_start
            result  = _normalize(parsed)
            _write_cache(ticker, result)
            _append_history(ticker, result)
            _update_stats(cache_hit=False, success=True, elapsed_sec=elapsed)
            return {**result, "from_cache": False, "cached_at": date.today().isoformat(), "error": None}

        except Exception as e:
            last_err = e

    _update_stats(cache_hit=False, success=False)
    return _error_result(str(last_err))


def _error_result(msg: str) -> dict:
    return {
        "overall":    "MEDIUM",
        "governance": "MEDIUM",
        "management": "MEDIUM",
        "regulatory": "MEDIUM",
        "industry":   "MEDIUM",
        "confidence": "LOW",
        "summary":    f"분석 실패: {msg}",
        "sources":    [],
        "from_cache": False,
        "cached_at":  None,
        "error":      msg,
    }
