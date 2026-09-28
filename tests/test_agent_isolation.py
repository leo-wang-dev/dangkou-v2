import json
from pathlib import Path
import pytest
from catalog import agent


def _template():
    return {'key': 'test_cat', 'name': '测试品类',
            'fields': [{'key': 'model', 'label': '型号', 'role': 'model'}]}


@pytest.mark.real_agent
def test_agent_only_mounts_input_and_output_and_filters_service_secrets(tmp_path,monkeypatch):
    source=tmp_path/'source.xlsx';source.write_bytes(b'fixture')
    work=tmp_path/'work';work.mkdir()
    monkeypatch.setattr(agent.shutil,'which',lambda name:'/usr/local/bin/docker' if name=='docker' else None)
    monkeypatch.setenv('CATALOG_AGENT_CONTAINER_IMAGE','approved-parser:fixture')
    monkeypatch.setenv('CATALOG_AGENT_NETWORK','dangkou-trial-parser')
    monkeypatch.setenv('TG_BOT_TOKEN','never-forward-this')
    monkeypatch.setenv('CATALOG_V2_SERVICE_TOKEN','never-forward-this-either')
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs));(work/'products.json').write_text(json.dumps({'products':[{'model':'A'}]}))
        return agent.subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(agent.subprocess,'run',run)
    assert agent.parse_dynamic(_template(),str(source),str(work))['products'][0]['model']=='A'
    args,kwargs=calls[0]
    assert args[args.index('--network')+1]=='dangkou-trial-parser'
    assert '--read-only' in args and '--cap-drop=ALL' in args
    assert sum(a=='--mount' for a in args)==2
    assert not any('never-forward' in str(v) for v in kwargs['env'].values())
    assert 'TG_BOT_TOKEN' not in kwargs['env'] and '--entrypoint' in args
    assert not any('/var/run/docker.sock' in a for a in args)


@pytest.mark.real_agent
def test_agent_fails_closed_without_isolation(tmp_path,monkeypatch):
    monkeypatch.delenv('CATALOG_AGENT_CONTAINER_IMAGE',raising=False)
    with pytest.raises(RuntimeError,match='禁止'):
        agent.parse_dynamic(_template(),str(tmp_path/'a.xlsx'),str(tmp_path/'work'))


@pytest.mark.real_agent
def test_nonzero_exit_rejects_leftover_result(tmp_path, monkeypatch):
    source = tmp_path / 'source.xlsx'; source.write_bytes(b'fixture')
    work = tmp_path / 'work'; work.mkdir()
    monkeypatch.setattr(agent.shutil, 'which', lambda name: '/fake/docker')
    monkeypatch.setenv('CATALOG_AGENT_CONTAINER_IMAGE', 'test:fixture')
    def run(args, **kwargs):
        (work / 'products.json').write_text('{"products": [{"model": "A"}]}')
        return agent.subprocess.CompletedProcess(args, 9)
    monkeypatch.setattr(agent.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='进程失败'):
        agent.parse_dynamic(_template(), str(source), str(work))
