from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Generic, Optional, TypeVar

from constants import DataSource, EarningsQuality, RevisionDirection


# ---------------------------------------------------------------------------
# 에러 계층
# ---------------------------------------------------------------------------

class FetchErrorReason(str, Enum):
    API_TIMEOUT            = "api_timeout"           # 일시적: 재시도 가능
    TICKER_NOT_FOUND       = "ticker_not_found"      # 영구적: 거래정지·상폐 의심
    INSUFFICIENT_DATA      = "insufficient_data"     # 일시적: 거래일 부족
    PARSE_ERROR            = "parse_error"           # 영구적: 컬럼 없음·타입 오류
    INVALID_ZERO_VALUE     = "invalid_zero_value"    # 영구적: 시총 0 등 명백한 이상값
    # RATE_LIMITED 후보 — 네이버 429·captcha·block 대응 (현재 API_TIMEOUT으로 처리, see TODO.md)



# ---------------------------------------------------------------------------
# FetchResult — 모든 krx fetch 함수의 반환 타입
# ---------------------------------------------------------------------------

T = TypeVar("T")


@dataclass(frozen=True)
class FetchResult(Generic[T]):
    """
    data is not None  → 성공 (error는 None)
    data is None      → 실패 (error에 원인)
    두 필드가 동시에 None이거나 동시에 값이 있으면 invariant 위반.
    """
    data:  Optional[T]
    error: Optional[FetchErrorReason] = None

    def __post_init__(self) -> None:
        if self.data is None and self.error is None:
            raise ValueError("FetchResult: data와 error 중 하나는 반드시 있어야 함")
        if self.data is not None and self.error is not None:
            raise ValueError("FetchResult: data와 error가 동시에 존재할 수 없음")

    @classmethod
    def success(cls, data: T) -> FetchResult[T]:
        return cls(data=data)

    @classmethod
    def failure(cls, reason: FetchErrorReason) -> FetchResult[T]:
        return cls(data=None, error=reason)

    @property
    def ok(self) -> bool:
        return self.data is not None


# ---------------------------------------------------------------------------
# 공통 베이스 — source / as_of_date 반복 제거
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaseMarketData:
    source:     DataSource
    as_of_date: date


# ---------------------------------------------------------------------------
# 데이터 모델 — frozen=True: 읽기 전용 snapshot, 실수 수정 방지
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SupplyFlow(BaseMarketData):
    foreign_net_buy:          Optional[int]   # 순매수 금액(원) — pykrx get_market_trading_VALUE
    institution_net_buy:      Optional[int]   # 순매수 금액(원) — 주수(volume) 아님 주의
    lookback_days:            int
    pension_net:              Optional[int] = None   # 연기금등
    investment_trust_net:     Optional[int] = None   # 투신
    financial_investment_net: Optional[int] = None   # 금융투자 (증권사 자기매매·프로그램)
    private_fund_net:         Optional[int] = None   # 사모
    insurance_net:            Optional[int] = None   # 보험


@dataclass(frozen=True)
class ForeignOwnership(BaseMarketData):
    ownership_pct:        float
    prev_pct:             Optional[float]
    change_lookback_pct:  Optional[float]  # lookback 기간 동안의 지분율 변화
    lookback_days:        int


@dataclass(frozen=True)
class MarketCap(BaseMarketData):
    market_cap:    int
    trading_value: int


@dataclass(frozen=True)
class Fundamentals(BaseMarketData):
    """
    source         : 주 데이터 출처 (단일 소스면 이것만 사용)
    *_source fields: 개별 필드 출처 — 혼합 소스(pykrx + naver 병합)일 때만 채움.
                     None이면 base source 상속.
    """
    per:             Optional[float]
    pbr:             Optional[float]
    div_yield:       Optional[float]
    roe:             Optional[float]       = None
    per_source:      Optional[DataSource] = None
    pbr_source:      Optional[DataSource] = None
    div_yield_source: Optional[DataSource] = None
    roe_source:      Optional[DataSource] = None


@dataclass(frozen=True)
class KospiDisparity(BaseMarketData):
    current:       float
    ma_n:          float
    disparity_pct: float
    window:        int


@dataclass(frozen=True)
class MarginLoan(BaseMarketData):
    """
    신용잔고율 — HEAT 과열 경보 및 QUICK_REJECT 기준.
    ratio_pct: percent unit (e.g. 7.5 means 7.5%)
    """
    ratio_pct: float


@dataclass(frozen=True)
class RiskData(BaseMarketData):
    """
    리스크 채점 입력 — raw fact만 저장. 판단은 scoring 레이어 소관.

    debt_ratio_pct  : 부채비율 (D/E, percent unit). 금융주는 None으로 두면 감점 없음.
    quarters_with_loss: 최근 4분기 중 적자 분기 수 (0~4). 연속 여부 무관, 총합.
    dilution_pct    : YoY 주식수 증가율 (percent unit). None이면 평가 제외.
    volatility_pct  : 연환산 변동성 (percent unit). None이면 평가 제외.
    governance_flag : 지배구조 위험 — 판단 영역이라 수동 입력.
    """
    quarters_with_loss: int   = 0
    debt_ratio_pct:         Optional[float] = None
    dilution_pct:           Optional[float] = None
    volatility_pct:         Optional[float] = None
    governance_flag:        bool            = False
    earnings_quality_ratio: Optional[float] = None  # 영업이익 / 당기순이익 (trailing 4Q). None = 데이터 부족
    interest_coverage:      Optional[float] = None  # EBIT / 이자비용. 데이터 소스 미확보 — 항상 None (see TODO.md)
    halt_days:              Optional[int]   = None  # 변동성 창(최근 252거래일) 중 거래량 0인 날 수(거래정지). None = 확인 불가
    halted_on_session:      Optional[bool]  = None  # 기준일(창의 마지막 거래일) 거래정지 여부. None = 확인 불가

    def __post_init__(self) -> None:
        if not (0 <= self.quarters_with_loss <= 4):
            raise ValueError(
                f"RiskData: quarters_with_loss must be 0~4, got {self.quarters_with_loss}"
            )


@dataclass(frozen=True)
class EarningsData(BaseMarketData):
    """
    실적 채점 입력 — 사실(fact)만 저장. 판단(good/bad)은 scoring 레이어 소관.

    quarters_met_expectations : 최근 4분기 중 충족 횟수 (0~4).
                                 충족 = surprise_pct >= 0 (inline -3%~+3% 포함).
    large_miss_pct            : 최근 대형 미스 크기. None이면 대형 미스 없음.
                                 판단 기준(threshold)은 config.EARNINGS 소관.
    revision                  : 컨센서스 추정치 방향. coverage 없으면 UNKNOWN.
    quality                   : 구조적/일회성 판단. 미입력이면 UNKNOWN.
    """
    quarters_met_expectations: int
    large_miss_pct:            Optional[float]   = None
    revision:                  RevisionDirection = RevisionDirection.UNKNOWN
    quality:                   EarningsQuality   = EarningsQuality.UNKNOWN

    def __post_init__(self) -> None:
        if not (0 <= self.quarters_met_expectations <= 4):
            raise ValueError(
                f"EarningsData: quarters_met_expectations must be 0~4, "
                f"got {self.quarters_met_expectations}"
            )
