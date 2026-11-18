from __future__ import annotations

_scan_job: dict = {
    "phase":      "idle",   # idle | scanning | post | done | error
    "done":       0,
    "total":      0,
    "current":    "",
    "results":    None,
    "error":      None,
    "start_time": None,     # 전체 타임아웃 감지용 (time.time())
}
