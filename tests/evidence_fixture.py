"""시험용: 검증(verified) 시장 입력에 원 응답 근거를 붙인다 (5단계-2부터 근거 없는 verified는 거부됨).

고정 응답(KRX Open API 형식, 기준 세션의 시가총액 1행)을 실행에 저장하고, 같은 정의로 값을 계산해 입력을 만든다.
입력의 이름·조회 시각·기준 세션은 그대로 둔다. 실제 조회 없음.
"""
from dataclasses import replace
import json

from analyzer.pipeline.entry import MarketInput
from analyzer.pipeline.sources import derive, request_open_api, to_market_input

DEFINITION = "krxopen_field_v1"
PARAMS = {"field": "MKTCAP", "ticker": "000001"}


def attach(store, run_id, inputs):
    out = {}
    for key, item in inputs.items():
        if (isinstance(item, MarketInput) and item.source_status == "verified"
                and item.definition_id is None and item.source_session_date is not None):
            day = item.source_session_date
            body = json.dumps({"OutBlock_1": [{"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": "000001",
                                               "MKTCAP": "1,000,000"}]}).encode()
            resp = store.record_response(run_id, endpoint="krxopen.stk_bydd_trd", request=request_open_api(day),
                                         fetched_at=item.fetched_at, http_status=200, body=body)
            derived = derive(DEFINITION, [resp], PARAMS, day, None)
            item = replace(to_market_input(item.name, DEFINITION, PARAMS, day, derived, item.fetched_at),
                           source_as_of_time=item.source_as_of_time)
        out[key] = item
    return out
