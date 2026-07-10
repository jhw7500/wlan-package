"""wifi_snmp_pp.py / wifi_snmp.py 의 logger.log(local0) 편입 로깅 검증.

가짜 sUTILS 모듈을 sys.modules 에 주입해 Logger.message 호출을 캡처한다(/dev/log 불필요).
로깅은 main()/에러경로 안의 lazy import 로만 동작하므로 순수 함수 테스트에는 영향 없음.
"""

import io
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wifi_snmp_pp as pp  # noqa: E402
import wifi_snmp as ext    # noqa: E402


def _fake_sUTILS(records, raise_on_init=False):
    m = types.ModuleType("sUTILS")

    class Logger:
        def __init__(self, app_name=None, facility=None, **kw):
            if raise_on_init:
                raise RuntimeError("no /dev/log")
            self.app_name = app_name

        def message(self, level, msg, extra=None):
            records.append((level, msg))

    def _EXTRA_():
        return ("test.py", 0)

    m.Logger = Logger
    m._EXTRA_ = _EXTRA_
    return m


# ---- wifi_snmp_pp.py (pass_persist 상주 데몬) ----

def test_pp_lifecycle_logs(monkeypatch):
    recs = []
    monkeypatch.setitem(sys.modules, "sUTILS", _fake_sUTILS(recs))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # 즉시 EOF → start+stop
    rc = pp.main()
    assert rc == 0
    msgs = [m for _, m in recs]
    assert any("start" in m for m in msgs), msgs
    assert any("stop" in m for m in msgs), msgs


def test_pp_exception_logs_err_and_survives(monkeypatch):
    recs = []
    monkeypatch.setitem(sys.modules, "sUTILS", _fake_sUTILS(recs))

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(pp, "collect_sources", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("get\n.1.3.6\n\n"))
    rc = pp.main()
    assert rc == 0
    assert any(lvl == "err" and "exception" in m for lvl, m in recs), recs
    # 예외 후에도 종료(stop) 로그까지 도달 = 프로세스 미중단
    assert any("stop" in m for _, m in recs), recs


def test_pp_best_effort_without_logger(monkeypatch):
    # Logger 생성 실패 시에도 데몬 정상 종료(로깅만 skip)
    monkeypatch.setitem(sys.modules, "sUTILS", _fake_sUTILS([], raise_on_init=True))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert pp.main() == 0


# ---- wifi_snmp.py (extend, 단명) ----

def test_extend_error_logs(monkeypatch):
    recs = []
    monkeypatch.setitem(sys.modules, "sUTILS", _fake_sUTILS(recs))
    rc = ext.main(["wifi_snmp.py", "bogus_metric"])
    assert rc == 1
    assert any(lvl == "err" for lvl, _ in recs), recs


def test_extend_success_no_log(monkeypatch):
    # 정상 값 경로는 로깅하지 않음(무소음)
    recs = []
    monkeypatch.setitem(sys.modules, "sUTILS", _fake_sUTILS(recs))
    monkeypatch.setattr(ext, "get_metric", lambda metric, data=None: "cantops")
    rc = ext.main(["wifi_snmp.py", "ssid"])
    assert rc == 0
    assert recs == [], recs
