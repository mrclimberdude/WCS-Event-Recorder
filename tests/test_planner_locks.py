from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from threading import Event

import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.storage import Database


@pytest.mark.parametrize("kind",["retry","missing"])
@pytest.mark.parametrize("change",["none","audio","database"])
def test_hashing_releases_database_and_stale_inputs_refuse_http(request,monkeypatch,kind,change):
    missing=kind=="missing"
    parent=request.getfixturevalue("missing_parent" if missing else "recovery_parent")
    module=importlib.import_module("wcs_event_recorder.missing_plan" if missing else "wcs_event_recorder.recovery_plan")
    verify=module.verify_audio
    ready,resume=Event(),Event()
    def paused(*args):
        ready.set()
        assert resume.wait(10),"hashing was not released"
        return verify(*args)
    monkeypatch.setattr(module,"verify_audio",paused)
    def plan():
        if missing:
            return module.plan_missing_recovery(parent.db.path,parent.run_id,11,AudDSettings(max_requests=1))
        return module.plan_recovery(parent.db.path,parent.run_id,[2],AudDSettings(max_requests=1))
    with ThreadPoolExecutor(max_workers=1) as workers:
        future=workers.submit(plan)
        try:
            assert ready.wait(10),"planner did not reach source hashing"
            # This commit must complete before hashing resumes, including rollback-journal mode.
            with Database(parent.db.path,timeout=.25) as writer:
                with writer.transaction():
                    writer.execute("UPDATE tracks SET title=title")
                    if change=="database":
                        configuration=dict(parent.run.configuration)
                        configuration["source_audio_sha256"]="0"*64
                        writer.execute("UPDATE analysis_runs SET configuration_json=? WHERE run_id=?",(json.dumps(configuration),parent.run_id))
            if change=="audio":
                content=bytearray(parent.source.audio_path.read_bytes())
                content[-1]^=1
                parent.source.audio_path.write_bytes(content)
        finally:
            resume.set()
        if change=="audio":
            with pytest.raises(ValueError,match="hash"):
                future.result(timeout=10)
            return
        frozen=future.result(timeout=10)
    if change=="database":
        calls=[]
        def forbidden(request):
            calls.append(request)
            raise AssertionError("stale plan reached HTTP")
        collect=importlib.import_module("wcs_event_recorder.missing_recovery" if missing else "wcs_event_recorder.recovery")
        with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
            with pytest.raises(ValueError):
                (collect.collect_missing if missing else collect.collect_recovery)(parent.db.path,frozen,"synthetic",client=client)
        assert calls==[]
    else:
        assert frozen["planned_requests"]==1
