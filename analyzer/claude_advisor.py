from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path

from constants import ENGINE_VERSION

_CACHE_DIR  = Path(__file__).parent.parent / "data" / "claude_analysis"
_STATS_FILE = Path(__file__).parent.parent / "data" / "claude_stats.json"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_MODEL = "claude-haiku-4-5-20251001"

_GRADE_REASON = {
    "strong_buy": "총점 85점 이상 — 스코어링 기준 최고 등급",
    "buy":        "총점 70~84점 — 매수 기준 충족",
    "neutral":    "총점 55~69점 — 뚜렷한 저평가 신호 없음",
    "avoid":      "총점 54점 이하 또는 Quick Reject 조건 해당",
}

_SYSTEM = """당신은 한국 주식 정량 분석 결과를 설명하는 보조 AI입니다.

규칙:
- 점수와 등급은 이미 결정되어 있습니다. 당신은 설명만 합니다.
- 매수/매도 추천, 목표가, 수익률 예측, 기간 전망은 절대 하지 않습니다.
- 근거 없는 추정은 하지 않습니다.
- 업종 특성상 주의해야 할 해석 함정이 있으면 명시합니다.
- 한국어로 간결하게 답변합니다."""

_PROMPT_TMPL = """\
다음은 정량 스코어링 엔진(v{engine_version})이 산출한 종목 데이터입니다.

종목명: {name} ({ticker}){sector_line}
총점: {score}점 / 100점
등급: {grade} ({grade_reason})
세부 점수: 밸류에이션 {valuation}/25  실적 {earnings}/25  리스크 {risk}/25  수급 {supply}/25
모멘텀: {momentum_display}  (1M {rel_1m}  3M {rel_3m}  6M {rel_6m} vs KOSPI)
신뢰도: {confidence}{conf_note}

주요 지표:
{metrics}
{notes_section}
이 데이터를 바탕으로 아래 형식으로 작성하세요.
예측이나 추천 표현은 절대 사용하지 마세요.

**추천 근거**
점수가 높게 나온 핵심 이유 2~3줄

**강점**
• 점수를 끌어올린 주요 항목

**주의사항**
• 점수가 낮은 영역 또는 업종 해석 시 유의점"""


def _fmt_rel(val) -> str:
    if val is None:
        return "—"
    return f"{val:+.1f}%p"


def _build_metrics(data: dict) -> str:
    lines = []
    if data.get("per")              is not None: lines.append(f"PER: {data['per']:.1f}배")
    if data.get("pbr")              is not None: lines.append(f"PBR: {data['pbr']:.2f}배")
    if data.get("roe")              is not None: lines.append(f"ROE: {data['roe']:.1f}%")
    if data.get("div_yield")        is not None: lines.append(f"배당수익률: {data['div_yield']:.2f}%")
    if data.get("volatility")       is not None: lines.append(f"연환산 변동성: {data['volatility']:.0f}%")
    if data.get("debt_ratio")       is not None: lines.append(f"부채비율: {data['debt_ratio']:.0f}%")
    if data.get("foreign_ownership") is not None: lines.append(f"외인지분율: {data['foreign_ownership']:.1f}%")
    return "\n".join(lines) if lines else "지표 없음"


def _build_prompt(data: dict) -> str:
    notes         = data.get("notes", [])
    notes_section = ("\n엔진 경고:\n" + "\n".join(f"- {n}" for n in notes) + "\n") if notes else ""

    confidence = data.get("confidence", "MEDIUM").upper()
    conf_note  = (
        "\n⚠ 일부 데이터 누락 가능성 — 주의사항에 데이터 한계를 반드시 명시하세요"
        if confidence == "LOW" else ""
    )

    grade_str   = data.get("grade", "").lower()
    sector      = data.get("sector")
    sector_line = f"\n업종: {sector}" if sector else ""

    return _PROMPT_TMPL.format(
        engine_version   = ENGINE_VERSION,
        name             = data.get("name", data.get("ticker", "")),
        ticker           = data.get("ticker", ""),
        sector_line      = sector_line,
        score            = data.get("score", "—"),
        grade            = data.get("grade", "—").upper(),
        grade_reason     = _GRADE_REASON.get(grade_str, ""),
        valuation        = data.get("valuation_score", "—"),
        earnings         = data.get("earnings_score", "—"),
        risk             = data.get("risk_score", "—"),
        supply           = data.get("supply_score", "—"),
        momentum_display = data.get("momentum", "—"),
        rel_1m           = _fmt_rel(data.get("rel_1m")),
        rel_3m           = _fmt_rel(data.get("rel_3m")),
        rel_6m           = _fmt_rel(data.get("rel_6m")),
        confidence       = confidence,
        conf_note        = conf_note,
        metrics          = _build_metrics(data),
        notes_section    = notes_section,
    )


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker}.json"


def _cache_key_matches(cached: dict, score: int, grade: str, confidence: str) -> bool:
    return (
        cached.get("date")           == date.today().isoformat()
        and cached.get("score")      == score
        and cached.get("grade")      == grade
        and cached.get("confidence") == confidence.upper()
        and cached.get("engine_version") == ENGINE_VERSION
    )


def _load_cache(ticker: str, score: int, grade: str, confidence: str) -> dict | None:
    """캐시 히트 시 {"analysis": str, "cached_at": str, "engine_version": str} 반환."""
    path = _cache_path(ticker)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not _cache_key_matches(cached, score, grade, confidence):
            return None
        return {
            "analysis":       cached["analysis"],
            "cached_at":      cached.get("cached_at", cached.get("date", "")),
            "engine_version": cached.get("engine_version", ENGINE_VERSION),
        }
    except Exception:
        return None


def _save_cache(ticker: str, score: int, grade: str, confidence: str, analysis: str) -> None:
    path = _cache_path(ticker)
    try:
        path.write_text(
            json.dumps(
                {
                    "date":           date.today().isoformat(),
                    "cached_at":      datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "score":          score,
                    "grade":          grade,
                    "confidence":     confidence.upper(),
                    "engine_version": ENGINE_VERSION,
                    "analysis":       analysis,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 통계 로그
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

        payload = json.dumps(stats, ensure_ascii=False, indent=2)
        _STATS_FILE.write_text(payload, encoding="utf-8")
        _STATS_FILE.with_suffix(".json.bak").write_text(payload, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 공개 인터페이스
# ---------------------------------------------------------------------------

def analyze_ticker(data: dict) -> dict:
    """
    Returns:
        {
            "analysis":       str,
            "from_cache":     bool,
            "cached_at":      str | None,
            "engine_version": str,
        }
    """
    ticker     = data.get("ticker", "")
    score      = data.get("score", 0)
    grade      = data.get("grade", "").lower()
    confidence = data.get("confidence", "MEDIUM")

    cached = _load_cache(ticker, score, grade, confidence)
    if cached:
        _update_stats(cache_hit=True)
        return {"from_cache": True, **cached}

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key:
        return {
            "analysis":       "ANTHROPIC_API_KEY가 설정되지 않았습니다.",
            "from_cache":     False,
            "cached_at":      None,
            "engine_version": ENGINE_VERSION,
        }

    prompt   = _build_prompt(data)
    last_err = None
    t_start  = time.monotonic()

    for _ in range(2):
        try:
            import anthropic
            client   = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model      = _MODEL,
                max_tokens = 600,
                system     = _SYSTEM,
                messages   = [{"role": "user", "content": prompt}],
            )
            analysis  = response.content[0].text.strip()
            elapsed   = time.monotonic() - t_start
            cached_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            _save_cache(ticker, score, grade, confidence, analysis)
            _update_stats(cache_hit=False, success=True, elapsed_sec=elapsed)
            return {
                "analysis":       analysis,
                "from_cache":     False,
                "cached_at":      cached_at,
                "engine_version": ENGINE_VERSION,
            }
        except Exception as e:
            last_err = e

    _update_stats(cache_hit=False, success=False)
    return {
        "analysis":       f"분석 생성 실패: {last_err}",
        "from_cache":     False,
        "cached_at":      None,
        "engine_version": ENGINE_VERSION,
    }
