"""첨부정정 사례별 승인 목록 (격리 구현 — 운영 적용 보류). docs/design/signal_pipeline.md §13.25.

최신 공시가 [첨부정정]이고 재무 API가 원본 접수번호를 돌려주는 기간 중, 원문 대조로 확인·승인된 조합만
"응답 접수번호 = 원본 번호"를 허용한다. 꼬리표 규칙으로 일반화하지 않는다.

자동 차단 조건 (하나라도 어긋나면 예외 미적용 → 기존 rcept_no_mismatch 차단):
 - 공시 목록에서 이번 실행에 고른 최신 버전 접수번호 ≠ 목록 항목의 첨부정정 번호(이후 정정 추가 등)
 - 재무 응답의 회사·사업연도·보고서 코드·연결/별도·접수번호 ≠ 항목
 - 재무 응답의 검증 계정 금액 ≠ 항목에 기록한 금액(근거가 달라짐) → exception_evidence_changed
"""
from __future__ import annotations

from dataclasses import dataclass

EXCEPTIONS_VERSION = "attach_exceptions_v4_2026-10-22"   # v2: 입력별 검증 계정 범위, v3: 215200·009450, v4: 023160


@dataclass(frozen=True)
class AttachmentException:
    id: str
    corp_code: str
    ticker: str
    bsns_year: int
    reprt_code: str
    fs_div: str
    latest_rcept_no: str            # 목록의 최신 버전([첨부정정])
    response_rcept_no: str          # 재무 API가 돌려주는 원본 번호
    accounts: tuple                 # ((이름, 재무제표 구분들, 계정 ID들, 원 금액 문자열), ...) — 검증한 계정
    inputs: tuple                   # 예외를 허용하는 입력 정의 ID. 범위 밖 입력에는 적용하지 않는다
    period_no: int                  # 대조한 회계기간(제 N 기)
    unit: str                       # 대조 문서의 표시 단위
    checks: tuple                   # 이미지·다른 계정명·누락 첨부 확인 기록
    evidence: tuple                 # ((파일, SHA-256), ...) — 보존본(검증 폴더)


APPROVED: tuple[AttachmentException, ...] = (
    AttachmentException(
        id="033500-2025-11011-CFS",
        corp_code="00231105", ticker="033500", bsns_year=2025, reprt_code="11011", fs_div="CFS",
        latest_rcept_no="20260319000704", response_rcept_no="20260318001705",
        accounts=(
            ("op", ("IS", "CIS"), ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"), "72558706089"),
            ("ni", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), "55883227093"),
            ("ni_ctrl", ("IS", "CIS"), ("ifrs-full_ProfitLossAttributableToOwnersOfParent",), "55883227093"),
            ("equity", ("BS",), ("ifrs-full_Equity",), "258895742264"),
            ("equity_ctrl", ("BS",), ("ifrs-full_EquityAttributableToOwnersOfParent",), "258895742264"),
            ("liabilities", ("BS",), ("ifrs-full_Liabilities",), "242665185023"),
        ),
        inputs=("dart_earnings_proxy_v1", "dart_loss_quarters_v1", "dart_debt_ratio_v1", "dart_eq_ratio_v1"),
        period_no=41, unit="원",
        checks=(
            "3자 대조: 원 사업보고서 본문(20260318001705.xml) = 재무 API = 첨부정정 연결감사보고서(_00761) 제41기, 6계정 일치",
            "옛 첨부(원본 _00761)는 제40·39기 열만 존재 — 표지 '제40기 → 제41기 감사보고서 교체'와 일치",
            "표지 정정 대상: 감사보고서·연결감사보고서(첨부파일 변경) — 첨부정정 zip 구성원 2개 모두 확보(누락 첨부 없음)",
            "이미지: 연결감사보고서(_00761) 이미지 0개. 별도 감사보고서(_00760) 이미지 9개는 연결(CFS) 입력에 쓰지 않음",
            "다른 계정명: 대조 문서에서 재무제표 표를 직접 읽어 6계정을 찾음(계정명 부재 추정 아님)",
        ),
        evidence=(
            ("_verification_2026-10-15/stage5/runs16/20261022_142934/raw/20260319000704.bin",
             "57cfbc7b40d66e097fd52b4c7e914babf35c88497c42939e76804a8b0f96d6ee"),
            ("_verification_2026-10-15/stage5/runs16/20261022_142934/raw/20260318001705.bin",
             "87b5ed832528ddaae764633d8ae3656f4048b12908039798e982f41a39d6dd5c"),
            ("_verification_2026-10-15/stage5/runs14/20261022_132400/raw/0187_dart.fnltt.body.gz(본문 압축 해제 SHA-256)",
             "d475ac9dabd98d1f4b42a69dfa4eb3915c1f600ab8fad6b1e460fe5d4dee4e9a"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/attach_compare_all.json",
             "8000855d69468bd9b6d2270d9498a42214fca5162548960c67b1699dfb910f90"),
        ),
    ),
    AttachmentException(
        id="215200-2025-11011-CFS",
        corp_code="01074862", ticker="215200", bsns_year=2025, reprt_code="11011", fs_div="CFS",
        latest_rcept_no="20260326000491", response_rcept_no="20260318000849",
        accounts=(       # 네 입력에 필요한 계정만 — ROE(지배 계정)에는 적용하지 않는다
            ("op", ("IS", "CIS"), ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"), "121019655273"),
            ("ni", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), "92935243694"),
            ("equity", ("BS",), ("ifrs-full_Equity",), "520580075368"),
            ("liabilities", ("BS",), ("ifrs-full_Liabilities",), "375632585824"),
        ),
        inputs=("dart_earnings_proxy_v1", "dart_loss_quarters_v1", "dart_debt_ratio_v1", "dart_eq_ratio_v1"),
        period_no=11, unit="원",
        checks=(
            "3자 대조: 원 사업보고서 본문 = 재무 API = 연결감사보고서(원본, 첨부정정본에 감사보고서 없음) 제11기, 4계정 일치 (v2 대조)",
            "표지 정정 대상: 내부회계관리제도운영보고서 첨부 추가 — 재무제표 아님",
            "웹 뷰어 확보: 문서 1·구간 6, 예상 구간 = 수신 구간, 구간 원문에 채점 계정명 없음",
            "이미지: 고유 8개 원본 보존·직접 확인 — 내부회계 감사·운영실태·평가 보고서, 재무 계정 금액·재무제표 표 없음, 판독 불가 부분 없음",
            "누락 첨부: 선택 목록의 같은 접수번호 문서 = 수신 문서",
        ),
        evidence=(
            ("_verification_2026-10-15/stage5/runs16/20261022_142934/raw/20260318000849.bin",
             "b02918d9383ce31a32cf41967a8e9c35fcf17e39addb3eb205c7c74619f905c7"),
            ("_verification_2026-10-15/stage5/runs14/20261022_132400/raw/0333_dart.fnltt.body.gz(본문 압축 해제 SHA-256)",
             "6dfd7a6214a02e680163ab11980babdcaf48b4465e878052b7e9c1f384355627"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/viewer_results.json",
             "3cf780e16ec0bb2b8b90b9f3cc28d7ebbde517ebc925565f104b94a022655784"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/attach_compare_all_v2.json",
             "00f749f1db85f2590ff0ca2607c71a4c388a02ae054249f14443cb8027813974"),
            ("_verification_2026-10-15/stage5/runs18/20261022_154127/images.json",
             "13de2a060d30034f5a0acd7288d514195ea4392d7578d0474f34b66318a490e1"),
        ),
    ),
    AttachmentException(
        id="009450-2025-11011-CFS",
        corp_code="00101488", ticker="009450", bsns_year=2025, reprt_code="11011", fs_div="CFS",
        latest_rcept_no="20260403000835", response_rcept_no="20260318001570",
        accounts=(       # 지배주주 순이익은 재무 API가 다른 XBRL 요소로 제공해 미검증 → ROE 미적용
            ("op", ("IS", "CIS"), ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"), "143376893533"),
            ("ni", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), "89698872406"),
            ("equity", ("BS",), ("ifrs-full_Equity",), "770495485591"),
            ("liabilities", ("BS",), ("ifrs-full_Liabilities",), "751671610276"),
        ),
        inputs=("dart_earnings_proxy_v1", "dart_loss_quarters_v1", "dart_debt_ratio_v1", "dart_eq_ratio_v1"),
        period_no=53, unit="원",
        checks=(
            "3자 대조: 원 사업보고서 본문 = 재무 API = 연결감사보고서(원본, 첨부정정본에 감사보고서 없음) 제53기, 4계정 일치 (v2 대조)",
            "표지 정정 대상: 내부회계관리제도 보고서 붙임 추가 — 재무제표 아님",
            "웹 뷰어 확보: 문서 1·구간 6, 예상 구간 = 수신 구간, 구간 원문에 채점 계정명 없음",
            "이미지: 고유 8개 원본 보존·직접 확인 — 내부회계 감사·운영실태·평가 보고서·붙임(통제 활동), 재무 계정 금액·재무제표 표 없음, 판독 불가 부분 없음",
            "누락 첨부: 선택 목록의 같은 접수번호 문서 = 수신 문서",
        ),
        evidence=(
            ("_verification_2026-10-15/stage5/runs16/20261022_142934/raw/20260318001570.bin",
             "d63d30c1748085c7f6c5769eb26c7ee2ec3440682d0fd43e06a74061640d346c"),
            ("_verification_2026-10-15/stage5/runs14/20261022_132400/raw/0109_dart.fnltt.body.gz(본문 압축 해제 SHA-256)",
             "f5df2f9b8433ad0b8a5df887961371440f40eef939377e211027e30d3c72390d"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/viewer_results.json",
             "3cf780e16ec0bb2b8b90b9f3cc28d7ebbde517ebc925565f104b94a022655784"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/attach_compare_all_v2.json",
             "00f749f1db85f2590ff0ca2607c71a4c388a02ae054249f14443cb8027813974"),
            ("_verification_2026-10-15/stage5/runs18/20261022_154127/images.json",
             "13de2a060d30034f5a0acd7288d514195ea4392d7578d0474f34b66318a490e1"),
        ),
    ),
    AttachmentException(
        id="023160-2025-11011-CFS",
        corp_code="00153375", ticker="023160", bsns_year=2025, reprt_code="11011", fs_div="CFS",
        latest_rcept_no="20260326001150", response_rcept_no="20260320000407",
        accounts=(       # 네 입력 계정만 — 연결감사보고서에 당기순이익 귀속 행이 없어 지배주주 순이익 미검증 → ROE 미적용
            ("op", ("IS", "CIS"), ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"), "38734846446"),
            ("ni", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), "65514427414"),
            ("equity", ("BS",), ("ifrs-full_Equity",), "669616725985"),
            ("liabilities", ("BS",), ("ifrs-full_Liabilities",), "71198893994"),
        ),
        inputs=("dart_earnings_proxy_v1", "dart_loss_quarters_v1", "dart_debt_ratio_v1", "dart_eq_ratio_v1"),
        period_no=44, unit="원",
        checks=(
            "3자 대조: 원 사업보고서 본문 = 재무 API = 연결감사보고서(원본, 첨부정정본에 감사보고서 없음) 제44기, 4계정 일치 (v2 대조)",
            "표지 정정 대상: 정관(이사 의무 조항 누락 보완) — 재무제표 아님",
            "웹 뷰어 확보: 문서 1·구간 2(정정신고·정관), 예상 구간 = 수신 구간, 구간 원문에 채점 계정명 없음",
            "이미지: 구간 원문에 이미지 0개",
            "누락 첨부: 선택 목록의 같은 접수번호 문서 = 수신 문서",
        ),
        evidence=(
            ("_verification_2026-10-15/stage5/runs16/20261022_142934/raw/20260320000407.bin",
             "92c8d22ed0acfcf576e701638a431eafa4dd32ab78e63dd538dcfcd3075b59c0"),
            ("_verification_2026-10-15/stage5/runs14/20261022_132400/raw/0156_dart.fnltt.body.gz(본문 압축 해제 SHA-256)",
             "fc86dfe5b00ff415f608557f17c9d694f78503ba64f19d96c3e9585f8dd211bb"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/viewer_results.json",
             "3cf780e16ec0bb2b8b90b9f3cc28d7ebbde517ebc925565f104b94a022655784"),
            ("_verification_2026-10-15/stage5/runs17/20261022_150357/attach_compare_all_v2.json",
             "00f749f1db85f2590ff0ca2607c71a4c388a02ae054249f14443cb8027813974"),
        ),
    ),
)


def match_exception(approved, *, corp_code: str, filing, fs_div: str, response,
                    definition_id: str | None = None, required_accounts: tuple = ()):
    """→ (항목, None) 허용 / (None, 'exception_evidence_changed') 항목은 맞으나 근거 금액이 달라짐 / (None, None) 해당 없음.
    입력 범위: definition_id가 항목의 inputs에 있고, 입력이 쓰는 계정(required_accounts)이 모두 항목의 검증 계정이어야 한다."""
    for e in approved:
        if definition_id not in e.inputs or not set(required_accounts) <= {a[0] for a in e.accounts}:
            continue
        if (e.corp_code, e.bsns_year, e.reprt_code, e.fs_div, e.latest_rcept_no) != (
                corp_code, filing.year, filing.reprt_code, fs_div, filing.rcept_no):
            continue
        rows = response.rows or ()
        if not rows or any((r.get("rcept_no"), r.get("corp_code"), r.get("bsns_year"), r.get("reprt_code")) !=
                           (e.response_rcept_no, corp_code, str(e.bsns_year), e.reprt_code) for r in rows):
            continue
        for _name, sj_divs, ids, amount in e.accounts:
            hits = [r for r in rows if r.get("sj_div") in sj_divs and r.get("account_id") in ids]
            # IS·CIS에 같은 계정이 둘 다 있으면 모두 같은 금액이어야 한다
            if not hits or any(r.get("thstrm_amount") != amount for r in hits):
                return None, "exception_evidence_changed"
        return e, None
    return None, None
