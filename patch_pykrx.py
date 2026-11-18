"""
pykrx auth.py 내 KRX 서버 불안정 대응 패치.

문제: KRX 로그인 서버가 invalid JSON 반환 시 pykrx import 자체가 crash.
      세션 만료 후 재인증 실패 시 모든 pykrx 호출이 None 반환 → "거래 데이터 없음".

패치 내용:
  1. login_krx         : resp.json() JSONDecodeError → return False (crash 방지)
  2. build_krx_session : refresh 예외 → success=False (import crash 방지)
  3. get_auth_session  : 세션 갱신 실패 시 기존 세션 유지 + 60초 cooldown (KRX 재시도 폭격 방지)
  4. get_auth_session  : 최초 auth 실패(None) 시에도 60초 cooldown 적용
"""

import site
from pathlib import Path


def find_pykrx_auth() -> Path | None:
    candidates = [
        Path(d) / "pykrx" / "website" / "comm" / "auth.py"
        for d in site.getsitepackages()
    ]
    return next((p for p in candidates if p.exists()), None)


def apply_patch(content: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if old in content:
        return content.replace(old, new, 1), True
    print(f"  ⚠️  [{label}] 패턴 미발견 — 이미 패치됐거나 버전 차이. 건너뜀.")
    return content, False


def main():
    auth_path = find_pykrx_auth()
    if not auth_path:
        print("ERROR: pykrx 설치 경로를 찾을 수 없음.")
        return

    print(f"대상 파일: {auth_path}")
    content = auth_path.read_text(encoding="utf-8")

    bak = auth_path.with_suffix(".py.bak")
    bak.write_text(content, encoding="utf-8")
    print(f"백업 완료: {bak}")

    applied = 0

    # ── Patch 1: login_krx — resp.json() 예외 처리 ──────────────────────────
    content, ok = apply_patch(
        content,
        old=(
            "    resp = session.post(LOGIN_URL, data=payload, headers=headers, timeout=15)\n"
            "    data = resp.json()\n"
            "    error_code = data.get(\"_error_code\", \"\")\n"
            "    error_message = data.get(\"_error_message\", \"\")"
        ),
        new=(
            "    resp = session.post(LOGIN_URL, data=payload, headers=headers, timeout=15)\n"
            "    try:\n"
            "        data = resp.json()\n"
            "    except Exception as _json_err:\n"
            "        print(f\"KRX 로그인 응답 파싱 실패: {_json_err}\")\n"
            "        return False\n"
            "    error_code = data.get(\"_error_code\", \"\")\n"
            "    error_message = data.get(\"_error_message\", \"\")"
        ),
        label="login_krx resp.json()",
    )
    if ok:
        applied += 1

    # ── Patch 2: build_krx_session — refresh 예외 처리 ──────────────────────
    content, ok = apply_patch(
        content,
        old=(
            "    krxs = KRXSession()\n"
            "    success = krxs.refresh(login_id, login_pw)"
        ),
        new=(
            "    krxs = KRXSession()\n"
            "    try:\n"
            "        success = krxs.refresh(login_id, login_pw)\n"
            "    except Exception as _ref_err:\n"
            "        print(f\"KRX 로그인 중 오류: {_ref_err}\")\n"
            "        success = False"
        ),
        label="build_krx_session refresh",
    )
    if ok:
        applied += 1

    # ── Patch 3: get_auth_session — 세션 갱신 실패 시 기존 세션 유지 + cooldown ─
    content, ok = apply_patch(
        content,
        old=(
            "            if _auth_session.refresh(login_id, login_pw):\n"
            "                print(\"KRX 세션 갱신 완료.\")\n"
            "            else:\n"
            "                print(\"KRX 세션 갱신 실패.\")\n"
            "                return None"
        ),
        new=(
            "            try:\n"
            "                _refreshed = _auth_session.refresh(login_id, login_pw)\n"
            "            except Exception as _ref_err:\n"
            "                print(f\"KRX 세션 갱신 오류: {_ref_err}. 기존 세션 유지.\")\n"
            "                _refreshed = False\n"
            "            if _refreshed:\n"
            "                print(\"KRX 세션 갱신 완료.\")\n"
            "            else:\n"
            "                print(\"KRX 세션 갱신 실패. 기존 세션 유지.\")\n"
            "                # 60초 cooldown: KRX 서버 폭격 방지\n"
            "                _auth_session.is_authenticated = True\n"
            "                _auth_session.expiry_time = time.time() + 60"
        ),
        label="get_auth_session refresh fallback + cooldown",
    )
    if ok:
        applied += 1

    # ── Patch 4: get_auth_session — 최초 auth 실패 시 cooldown 적용 ────────────
    content, ok = apply_patch(
        content,
        old=(
            "    if _auth_session is None:\n"
            "        # 환경 변수에서 다시 시도\n"
            "        login_id = os.getenv(\"KRX_ID\")\n"
            "        login_pw = os.getenv(\"KRX_PW\")\n"
            "        if login_id and login_pw:\n"
            "            _auth_session = build_krx_session(login_id, login_pw)\n"
            "        return _auth_session"
        ),
        new=(
            "    if _auth_session is None:\n"
            "        # 환경 변수에서 다시 시도\n"
            "        login_id = os.getenv(\"KRX_ID\")\n"
            "        login_pw = os.getenv(\"KRX_PW\")\n"
            "        if login_id and login_pw:\n"
            "            result = build_krx_session(login_id, login_pw)\n"
            "            if result is None:\n"
            "                # 로그인 실패: 60초 cooldown용 dummy 세션 생성 (폭격 방지)\n"
            "                _auth_session = KRXSession()\n"
            "                _auth_session.is_authenticated = False\n"
            "                _auth_session.expiry_time = time.time() + 60\n"
            "            else:\n"
            "                _auth_session = result\n"
            "        return _auth_session"
        ),
        label="get_auth_session first-auth cooldown",
    )
    if ok:
        applied += 1

    auth_path.write_text(content, encoding="utf-8")
    print(f"\n패치 완료: {applied}/4 항목 적용됨.")
    if applied == 4:
        print("✅ 모든 패치 적용 완료.")
    else:
        print("⚠️  일부 패치 미적용. 로그 확인 필요.")


if __name__ == "__main__":
    main()
