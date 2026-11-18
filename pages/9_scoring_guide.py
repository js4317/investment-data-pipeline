from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from constants import ENGINE_VERSION

_V  = config.VALUATION
_E  = config.EARNINGS
_R  = config.RISK
_S  = config.SUPPLY
_G  = config.GRADE
_P  = config.POSITION
_QR = config.QUICK_REJECT

st.title("📚 Scoring Guide")
st.caption(f"엔진 {ENGINE_VERSION}  ·  채점 규칙 {config.RULE_VERSION_STR}  ·  config.py 기반 자동 동기화")

# ---------------------------------------------------------------------------
# 100점 구조 요약
# ---------------------------------------------------------------------------
st.divider()
sc1, sc2, sc3, sc4, sc5 = st.columns(5)
sc1.metric("밸류에이션", "25점")
sc2.metric("실적",       "25점")
sc3.metric("리스크",     "25점")
sc4.metric("수급",       "25점")
sc5.metric("모멘텀",     "별도 오버레이", help="점수에 포함되지 않습니다. 참고용 정보 레이어입니다.")

# ---------------------------------------------------------------------------
# 등급 기준
# ---------------------------------------------------------------------------
st.divider()
st.subheader("등급 기준")
gc1, gc2, gc3, gc4 = st.columns(4)
gc1.metric("🟢 STRONG_BUY", f"{_G['strong_buy_min_score']}점 이상")
gc2.metric("🔵 BUY",        f"{_G['buy_min_score']}~{_G['strong_buy_min_score']-1}점")
gc3.metric("⚪ NEUTRAL",    f"{_G['neutral_min_score']}~{_G['buy_min_score']-1}점")
gc4.metric("🔴 AVOID",      f"{_G['neutral_min_score']-1}점 이하")

# ---------------------------------------------------------------------------
# Quick Reject
# ---------------------------------------------------------------------------
st.divider()
with st.expander("⚡ Quick Reject (즉시 탈락 조건)", expanded=False):
    st.markdown("**점수 계산 전에 즉시 AVOID 처리됩니다.**")
    st.write(f"• PBR > {_QR['pbr_max']} 배")
    st.write(f"• 신용잔고 > {_QR['margin_loan_max_pct']}%")
    st.write(f"• 연속 미스 분기 ≥ {_QR['consecutive_miss_quarters']}회")

# ---------------------------------------------------------------------------
# 밸류에이션 (25점)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("밸류에이션  25점")

st.markdown("**PER 점수**")
per_rows = []
table = _V["per_upper_bound_score_table"]
for i, row in enumerate(table):
    prev = table[i - 1]["max_per"] if i > 0 else 0
    if row["max_per"] >= 9000:
        label = f"PER > {prev}"
    else:
        label = f"PER ≤ {row['max_per']}" if i == 0 else f"{prev} < PER ≤ {row['max_per']}"
    per_rows.append({"구간": label, "점수": row["score"]})

st.dataframe(pd.DataFrame(per_rows), hide_index=True, use_container_width=False)
st.caption(f"• PBR > {_V['pbr_danger_threshold']} 이면 Quick Reject 검토")
st.caption(f"• PBR > {_V['pbr_caution_threshold']} 이면 confidence 하향 가능")

# ---------------------------------------------------------------------------
# 실적 (25점)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("실적  25점")

_base_max  = max(_E["base_score"].values())
_bonus_max = 25 - _base_max
st.caption(f"기본 점수 최대 {_base_max}점  +  추가 보너스 최대 {_bonus_max}점  =  최종 상한 25점")

st.markdown("**기본 점수 (최근 4분기 흑자 기준)**")
base_rows = [{"흑자 분기 수": k, "기본 점수": v} for k, v in sorted(_E["base_score"].items(), reverse=True)]
st.dataframe(pd.DataFrame(base_rows), hide_index=True, use_container_width=False)

st.markdown("**보너스 / 패널티**")
bonus_rows = [
    {"항목": "Revision 상향",                                             "조정": f"+{_E['revision_up_adj']}"},
    {"항목": "Revision 하향",                                             "조정": str(_E["revision_down_adj"])},
    {"항목": f"대형 미스 (≤ {abs(_E['large_miss_threshold_pct']):.0f}%)", "조정": str(_E["large_miss_penalty"])},
    {"항목": "구조적 개선 (Structural)",                                   "조정": f"+{_E['quality_structural_adj']}"},
    {"항목": "일회성 dominant",                                            "조정": str(_E["quality_one_off_adj"])},
    {"항목": f"ROE ≥ {_E['roe_tier3_pct']:.0f}%",                        "조정": f"+{_E['roe_tier3_bonus']}"},
    {"항목": f"ROE ≥ {_E['roe_tier2_pct']:.0f}%",                        "조정": f"+{_E['roe_tier2_bonus']}"},
    {"항목": f"ROE ≥ {_E['roe_tier1_pct']:.0f}%",                        "조정": f"+{_E['roe_tier1_bonus']}"},
    {"항목": f"ROE < {_E['roe_low_pct']:.0f}%",                          "조정": str(_E["roe_low_penalty"])},
]
st.dataframe(pd.DataFrame(bonus_rows), hide_index=True, use_container_width=False)

with st.expander("계산 예시", expanded=False):
    def _earnings_ex(q: int, roe: float | None = None, revision_up: bool = False) -> int:
        base = _E["base_score"].get(q, 0)
        adj  = _E["revision_up_adj"] if revision_up else 0
        bonus = 0
        if roe is not None:
            if   roe >= _E["roe_tier3_pct"]: bonus = _E["roe_tier3_bonus"]
            elif roe >= _E["roe_tier2_pct"]: bonus = _E["roe_tier2_bonus"]
            elif roe >= _E["roe_tier1_pct"]: bonus = _E["roe_tier1_bonus"]
        return min(base + adj + bonus, 25)

    ex_rows = [
        {
            "시나리오": f"4분기 흑자  ROE {_E['roe_tier2_pct']:.0f}%+",
            "계산": f"{_base_max} + {_E['roe_tier2_bonus']} (ROE)",
            "점수": _earnings_ex(4, _E["roe_tier2_pct"]),
        },
        {
            "시나리오": f"4분기 흑자  ROE {_E['roe_tier3_pct']:.0f}%+",
            "계산": f"{_base_max} + {_E['roe_tier3_bonus']} (ROE)",
            "점수": _earnings_ex(4, _E["roe_tier3_pct"]),
        },
        {
            "시나리오": f"4분기 흑자  Revision 상향",
            "계산": f"{_base_max} + {_E['revision_up_adj']} (Revision)  → 상한 적용",
            "점수": _earnings_ex(4, revision_up=True),
        },
        {
            "시나리오": f"3분기 흑자  ROE {_E['roe_tier3_pct']:.0f}%+",
            "계산": f"{_E['base_score'][3]} + {_E['roe_tier3_bonus']} (ROE)",
            "점수": _earnings_ex(3, _E["roe_tier3_pct"]),
        },
        {
            "시나리오": "4분기 흑자  보너스 없음",
            "계산": f"{_base_max}",
            "점수": _earnings_ex(4),
        },
    ]
    st.dataframe(pd.DataFrame(ex_rows), hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# 리스크 (25점)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("리스크  25점")
st.caption("기본 25점에서 패널티 합산")

risk_rows = [
    {"항목": f"부채비율 > {_R['debt_ratio_danger_pct']:.0f}%",                "패널티": str(_R["debt_penalty"])},
    {"항목": f"적자 분기 ≥ {_R['loss_quarters_warning']}회 (경고)",            "패널티": str(_R["loss_warning_penalty"])},
    {"항목": f"적자 분기 ≥ {_R['loss_quarters_danger']}회 (위험)",             "패널티": str(_R["loss_danger_penalty"])},
    {"항목": f"주식 희석 > {_R['dilution_danger_pct']:.0f}% (YoY)",           "패널티": str(_R["dilution_penalty"])},
    {"항목": f"주가 변동성 (연간 기준) > {_R['volatility_danger_pct']:.0f}%", "패널티": str(_R["volatility_penalty"])},
    {"항목": "지배구조 이슈",                                                   "패널티": str(_R["governance_penalty"])},
]
st.dataframe(pd.DataFrame(risk_rows), hide_index=True, use_container_width=False)

# ---------------------------------------------------------------------------
# 수급 (25점)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("수급  25점")

supply_rows = [
    {"항목": "외인 + 기관 동시 순매수 (쌍끌이)", "점수": str(_S["dual_buying_score"])},
    {"항목": "외인 단독 순매수",                  "점수": str(_S["foreign_single_score"])},
    {"항목": "기관 단독 순매수",                  "점수": str(_S["institution_single_score"])},
    {"항목": "외인 + 기관 동시 순매도",           "점수": str(_S["dual_selling_penalty"])},
]
st.dataframe(pd.DataFrame(supply_rows), hide_index=True, use_container_width=False)

st.markdown("**외인지분율 변화 보너스**")
own_rows = [
    {"변화량": f"+{_S['ownership_change_tier3_pp']}%p 이상", "보너스": f"+{_S['ownership_change_tier3_bonus']}"},
    {"변화량": f"+{_S['ownership_change_tier2_pp']}%p 이상", "보너스": f"+{_S['ownership_change_tier2_bonus']}"},
    {"변화량": f"+{_S['ownership_change_tier1_pp']}%p 이상", "보너스": f"+{_S['ownership_change_tier1_bonus']}"},
    {"변화량": "감소",                                         "보너스": str(_S["ownership_decrease_penalty"])},
]
st.dataframe(pd.DataFrame(own_rows), hide_index=True, use_container_width=False)
st.caption("최종 점수는 25점을 초과하지 않습니다.")

# ---------------------------------------------------------------------------
# 모멘텀 (오버레이)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("모멘텀  (점수 외 오버레이)")
st.caption("100점 점수에 포함되지 않습니다. 밸류 대비 시장 반응을 보조적으로 확인하는 레이어입니다.")

mom_rows = [
    {"등급": "▲▲",  "기준": "strength ≥ 100pt"},
    {"등급": "▲",   "기준": "strength 50~99pt"},
    {"등급": "·",   "기준": "strength 0~49pt"},
    {"등급": "▼",   "기준": "strength -1~-49pt"},
    {"등급": "▼▼",  "기준": "strength -50~-99pt"},
    {"등급": "▼▼▼", "기준": "strength ≤ -100pt"},
]
st.dataframe(pd.DataFrame(mom_rows), hide_index=True, use_container_width=False)
st.caption("rel = 종목 수익률 − KOSPI 수익률  (예: rel_3M = 최근 3개월 종목 수익률 − KOSPI 3개월 수익률)")
st.caption("strength = 0.5×rel_1M + 1.0×rel_3M + 1.5×rel_6M  (최근일수록 낮은 가중치)")

# ---------------------------------------------------------------------------
# 포지션 한도
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📐 포지션 한도 (참고)", expanded=False):
    pos_rows = [
        {"등급": "STRONG_BUY",  "최대 비중": f"{_P['strong_buy_max_weight_pct']}%"},
        {"등급": "BUY (HIGH)",   "최대 비중": f"{_P['buy_max_weight_pct']}%"},
        {"등급": "BUY (MEDIUM)", "최대 비중": f"{_P['buy_medium_confidence_pct']}%"},
        {"등급": "NEUTRAL",      "최대 비중": f"{_P['neutral_max_weight_pct']}%"},
    ]
    st.dataframe(pd.DataFrame(pos_rows), hide_index=True, use_container_width=False)
    st.caption(f"단일 종목 하드캡: {_P['single_stock_hard_cap_pct']}%  |  NEUTRAL 레짐 ×{_P['regime_neutral_multiplier']}  |  RISK_OFF ×{_P['regime_risk_off_multiplier']}")

# ---------------------------------------------------------------------------
# 설계 철학
# ---------------------------------------------------------------------------
st.divider()
with st.expander("💡 설계 철학", expanded=False):
    st.markdown("""
**이 시스템이 보는 것**

현재 밸류, 실적 안정성, 리스크 수준, 수급 방향 — 지금 이 순간의 상태를 정량화합니다.
예측이 아닌 현재 사실 기반 채점이 원칙입니다.

**모멘텀을 점수에 넣지 않는 이유**

모멘텀이 좋은 종목이 항상 좋은 종목은 아닙니다.
밸류에이션이 뒷받침되지 않은 모멘텀은 Value Trap 탐지를 방해합니다.
모멘텀은 "엔진이 고른 종목을 시장이 어떻게 보고 있는가"를 확인하는 용도입니다.

**이 시스템이 잘 맞지 않는 종목**

철강·조선·화학·정유처럼 이익 사이클이 큰 업종은 실적 바닥에서 PER이 왜곡됩니다.
적자 성장주·바이오는 EPS 기반 평가 자체가 불가합니다.
이 경우 스코어는 참고용으로만 활용하세요.

**금융주 리스크 예외**

은행·보험은 부채비율의 의미가 다릅니다. 향후 업종별 예외 처리를 검토 중입니다.
""")
