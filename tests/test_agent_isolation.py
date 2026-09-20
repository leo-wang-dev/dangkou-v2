import json
from pathlib import Path
import pytest
from catalog import agent


def test_agent_only_mounts_input_and_output_and_filters_service_secrets(tmp_path,monkeypatch):
    source=tmp_path/'source.xlsx';source.write_bytes(b'fixture')
    work=tmp_path/'work';work.mkdir()
    monkeypatch.setattr(agent.shutil,'which',lambda name:'/usr/local/bin/docker' if name=='docker' else None)
    monkeypatch.setenv('CATALOG_AGENT_CONTAINER_IMAGE','approved-parser:fixture')
    monkeypatch.setenv('TG_BOT_TOKEN','never-forward-this')
    monkeypatch.setenv('CATALOG_V2_SERVICE_TOKEN','never-forward-this-either')
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs));(work/'products.json').write_text(json.dumps({'products':[{'model_no':'A'}]}))
    monkeypatch.setattr(agent.subprocess,'run',run)
    assert agent.parse('razor',str(source),str(work))['products'][0]['model_no']=='A'
    args,kwargs=calls[0]
    assert '--read-only' in args and '--cap-drop=ALL' in args
    assert sum(a=='--mount' for a in args)==2
    assert not any('never-forward' in str(v) for v in kwargs['env'].values())
    assert 'TG_BOT_TOKEN' not in kwargs['env'] and '--entrypoint' in args
    assert not any('/var/run/docker.sock' in a for a in args)


def test_agent_fails_closed_without_isolation(tmp_path,monkeypatch):
    monkeypatch.delenv('CATALOG_AGENT_CONTAINER_IMAGE',raising=False)
    with pytest.raises(RuntimeError,match='禁止'):
        agent.parse('razor',str(tmp_path/'a.xlsx'),str(tmp_path/'work'))
