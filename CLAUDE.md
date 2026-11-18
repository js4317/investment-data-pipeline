# 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing, state assumptions explicitly. If multiple interpretations exist, present them — don't pick silently. If a simpler approach exists, say so. Ask only when core requirements are genuinely ambiguous — not for trivial details.

# 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked. No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested. No defensive error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- "Would a senior engineer say this is overcomplicated?" If yes, simplify.

# 3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing: don't improve adjacent code, don't refactor things that aren't broken, match existing style.
When your changes create orphans: remove imports/variables/functions YOUR changes made unused. Don't remove pre-existing dead code unless asked.
The test: every changed line should trace directly to the user's request.

# 4. Goal-Driven Execution
Define success criteria. Loop until verified.

For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

# 5. Design Scope
Quality/value 중심의 안정형 투자 시스템. 현재 기업 질(Quality)을 평가하며 미래 회복을 예측하지 않는다.

Applicable: 안정적 수익 + 저PBR/PER, 통신·유틸리티·소비재, 저PBR 금융, 수급 동반 기술주.

Out of scope: Cyclical trough (철강·조선·화학·정유·해운), 턴어라운드, 적자 성장주, 테마 모멘텀.
- Cyclical 업종이 스크리너 상위에 뜨면 버그가 아님. 시스템 적용 범위 밖임.
- PER N/A 또는 50+는 단순 고평가가 아닐 수 있다 — 실적 사이클 바닥일 수 있음. 별도 프레임워크 필요.

Production scoring structure (v8.6.0):
- STRONG_BUY threshold: Earnings ≥ 22pt (gate). Valuation + Risk = ranking inputs.
- Supply_score 제거: 5개 연구(2026-09-23) 전 방향 기각 → earn+val+risk 3-factor 구조.

Empirical component roles (실제 alpha 기여 연구 결과) → docs/RESEARCH.md.
Research findings는 production architecture를 자동으로 바꾸지 않는다.

# 6. Alpha Measurement — PIT Rules
Look-ahead bias 절대 금지. "추천 결정에 사용된 당일 종가"를 entry price로 쓰지 않는다.

스냅샷 헤더 메타데이터 (2026-07-01~):
- `entry_basis`: "same_day_open" | "next_day_open" — 09:00 이전 실행 = 당일 시가, 16:00 이후 = 익일 시가.
- `market_data_date`: pykrx가 실제 반환한 KRX 데이터 날짜.
- `analysis_timestamp`: 실행 시각 (ISO 8601).

판단은 저장 시점에 1회만. 같은 날 2회 실행해도 최초 실행의 entry_basis 보존.
KOSPI benchmark도 동일 entry_basis 적용. Alpha windows: [20, 60, 120] 영업일.

# 7. Financial Quality Principles
숫자를 믿기 전에 그 숫자가 믿을 만한지 검증한다.

- op/ni ratio < 0.2: Valuation -5 / < 0.5: -2 / < 0.8: 경고 (일회성 이익·파생상품 PER 왜곡 감지)
- EBIT/이자비용 < 1 + 부채비율 > 120% 복합 조건: Risk -3 (데이터 소스 확보 시 활성화)

# 8. Validation Principles
검증은 raw n 단독이 아니라 sample coverage, temporal consistency, cluster-aware uncertainty를 함께 고려한다.

- 단일 기간 성과로 결론 내리지 않는다. 극단 이벤트 구간(서킷브레이커 등)은 별도 분리.
- Factor 추가: 긍정적 방향성 확인 후 검토. Factor 삭제: 복수 윈도우 지속적 음수 + 충분한 sample.
- 검증 기준 세부사항, 현재 연구 결과 → docs/RESEARCH.md.

# 9. Engine Evolution — Evidence-Gated Change

**원칙: 변경은 언제든 가능하다. 단, 모든 점수 관련 변경은 연구 근거가 선행되어야 한다.**

Engine Freeze는 폐기됐다. 대신 Evidence-Gated Change 정책을 따른다.

### 변경 요건 (점수 로직·가중치·팩터·등급 기준)

| 단계 | 내용 |
|---|---|
| Research | 복수 관점에서 반복 검증 (IC, counterfactual, role attribution 등) |
| Replay | 과거 동일 데이터에서 기존 vs 새 구조 비교 |
| Regression | 코드 정상 작동 확인 (테스트 통과) |
| Calibration | 새 엔진 버전을 새 baseline으로 설정 |
| Forward validation | 새 구조가 실제로 개선됐는지 OOS에서 확인 |

연구 없이 "합리적으로 보여서" 바꾸는 것 금지. 연구가 충분하면 버전과 무관하게 바꾼다.

### 변경 불필요 (승인 없이 가능)
- 버그 수정, UI 개선, 비점수 기능 추가

### 명시적 승인 필요
- HOLDING_TICKERS 변경 (추가/삭제)

---

Production Engine (v8.6.0):
- Supply_score 제거 완료 (2026-09-23). 5개 연구 전 방향 기각 근거.
- Retrospective Research System (RRS): 새 공식(earn+val+risk)으로 재실행 필요.

Roadmap:
- v8.6 Rebuild — 3-factor 구조 OOS 재시작 (현재)
- v9  Operate  — Signal Quality → Decision Quality
- v10 Optimize — 비중 최적화

검증 전에는 포지션 사이징·최적화·리스크 버짓 구현 금지.

Factor Lifecycle: Experimental → Calibrating → Validated → Deprecated
각 상태의 통계 조건 → docs/RESEARCH.md.

# 10. Console Architecture Principles
"콘솔은 정보를 제공하고, 판단은 투자자가 한다."

패널 역할 (각각 다른 질문에 답한다):
- Score          → "이 기업의 질이 충분한가?" — 펀더멘털 전용. v8.x에서 변경 안 함.
- Risk Radar     → "점수에 안 보이는 비재무 리스크가 있는가?" — 온디맨드, 7일 캐시.
- Market Context → "지금 확인해야 할 사실이 있는가?" — 공시·이벤트 기반. 해석 없음.
- Portfolio      → "내가 정한 규칙에 따르면 오늘 뭘 해야 하나?"

Market Context 불변 원칙:
- 점수에 영향 없음. Score = 펀더멘털 전용.
- 사실만 표시. 해석·매수매도 권고 금지. LLM 요약도 "요약"만.

Structural Trend (v8.5 Candidate, 미구현):
- Score와 완전히 독립된 Layer. 점수에 직접 반영하지 않는다.
- 선행 조건: DART 8분기 재무 timeseries 안정적 자동 추출 검증 완료 후 시작.
- Validated 후에도 Score 편입 여부는 별도 논의. Stage 4(UI 표시)가 종착점.

의존성 방향: pages → analyzer / pages → backtests / backtests → analyzer (역방향 없음).
코드 구조 세부사항, migration triggers, known violations → docs/ARCHITECTURE.md.

# 11. Security & Broker Rules
- API 키는 환경변수로만 관리. 코드·설정 파일 하드코딩 금지.
- 체결 데이터는 로컬 DB에만 저장. 외부 서버·클라우드·LLM에 전송 금지.
- 주문은 항상 사용자 명시적 승인 이후에만 가능. 자동 주문 불가.
- v9 Broker Integration 상세 → docs/ARCHITECTURE.md.

# 12. Versioning & Changelog
모든 사용자 기능 변경은 CHANGELOG.md에 기록한다.
ENGINE_VERSION 변경 시 반드시 CHANGELOG.md에 해당 버전 섹션을 추가한다.
점수 계산·필터링·등급 기준 변경은 반드시 기록한다 — 코드만 바꾸고 CHANGELOG 빠뜨리는 것 금지.
단순 주석·리팩토링·스타일 변경은 기록하지 않는다.

Version Bump: 점수 로직 변경 → PATCH / 새 기능 → MINOR / 철학 변경·호환 깨짐 → MAJOR
날짜 포맷: v7.0.0+ 는 연-월-일 (예: 2026-06-08)
CHANGELOG 섹션 (해당하는 것만): Philosophy / Breaking Changes / Added / Changed / Fixed / Calibration Result

# 13. Document Structure
- CLAUDE.md            → "Claude는 어떻게 작업해야 하는가?" (엔진 설계 원칙·규칙 전용)
- docs/RESEARCH.md     → "지금까지 무엇을 발견했는가?" (연구 결과, factor 검증 기준 세부사항)
- docs/ARCHITECTURE.md → "코드 구조와 기술 부채" (의존성, migration triggers, known violations)
- CHANGELOG.md         → "무엇이 언제 바뀌었는가?"
