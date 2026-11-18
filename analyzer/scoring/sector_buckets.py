from __future__ import annotations

# KRX 업종명 → 논리적 버킷 매핑
# 소표본 방지: 해상/항공/육상운수 → "운송" 통합 (대한해운 단독 버킷 문제 해결)
# n < hybrid_min_sector_n 이면 절대 fallback — 이 파일은 이름만 관리, 표본 수 판단은 scorer 레이어

_KRX_TO_BUCKET: dict[str, str] = {
    # 금융 — PER/PBR 구조가 일반 제조업과 완전히 다름, 각 업종 별도 버킷
    "은행":             "은행",
    "보험":             "보험",
    "증권":             "증권",
    "기타금융":          "기타금융",

    # 지주사 — 자회사 배당 기반 수익구조, 연결 PER 왜곡
    "지주회사":          "지주사",

    # 운송 통합 — 해상/항공/육상 각각 표본이 적어 합산
    "운수창고":          "운송",
    "운수창고업":        "운송",
    "해상운수":          "운송",
    "항공운수":          "운송",
    "육상운수":          "운송",
    "창고및운송관련서비스": "운송",

    # 나머지는 KRX 업종명 그대로 사용
    # (전기전자, 화학, 의약품, 건설업 등 표본 충분한 대형 섹터)
}

_DEFAULT_BUCKET = "기타"


def krx_to_bucket(krx_sector: str | None) -> str:
    if not krx_sector:
        return _DEFAULT_BUCKET
    return _KRX_TO_BUCKET.get(krx_sector, krx_sector)
