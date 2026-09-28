import importlib.util
from pathlib import Path
import os
import subprocess
import shutil

ROOT=Path(__file__).resolve().parents[1]

def load():
    spec=importlib.util.spec_from_file_location('preflight',ROOT/'scripts/preflight.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_production_mail_and_container_route_required(monkeypatch):
    module=load()
    for key in ('RESEND_API_KEY','RESEND_FROM','CATALOG_AGENT_NETWORK'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('CATALOG_AGENT_BASE_URL','http://127.0.0.1:4000')
    errors=module.missing()
    assert any('RESEND_API_KEY' in e for e in errors)
    assert any('RESEND_FROM' in e for e in errors)
    assert any('CATALOG_AGENT_NETWORK' in e for e in errors)
    assert any('localhost' in e for e in errors)

def test_preflight_dry_run_does_not_spawn_or_write(monkeypatch):
    module=load()
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(AssertionError('child not allowed')))
    assert isinstance(module.missing(),list)

def test_installer_can_render_without_host_mutation(tmp_path):
    fake=tmp_path/'bin'; fake.mkdir()
    for name in ('sudo','systemctl','install'):
        file=fake/name; file.write_text('#!/bin/sh\necho forbidden-host-mutation >&2\nexit 99\n'); file.chmod(0o755)
    for name in ('mkdir','cat','sed','dirname','mktemp'):
        (fake/name).symlink_to(shutil.which(name))
    result=subprocess.run(['/bin/bash',str(ROOT/'deploy/wechat-test-services.sh'),'--render',str(tmp_path)],capture_output=True,text=True,env={**os.environ,'PATH':str(fake)})
    assert result.returncode==0,result.stderr
    files={p.name:p.read_text() for p in tmp_path.iterdir() if p.is_file()}
    assert 'dangkou-wechat-test-index.service' in files
    assert '--kind central' in files['dangkou-wechat-test-guest-sweep.service']
    assert '--kind shop' in files['dangkou-wechat-test-guest-sweep.service']
    assert 'OnUnitActiveSec=15min' in files['dangkou-wechat-test-guest-sweep.timer']
    assert 'litellm' in files['dangkou-wechat-test-api.service']

def test_bridge_config_and_network_contract():
    import yaml
    config=yaml.safe_load((ROOT/'deploy/litellm.yaml').read_text())
    assert config['model_list'][0]['model_name']=='trial-parser'
    compose=yaml.safe_load((ROOT/'deploy/compose.litellm.yaml').read_text())
    assert compose['networks']['parser']['name']=='dangkou-trial-parser'
    assert compose['services']['litellm']['ports']==['127.0.0.1:4000:4000']
    assert 'ANTHROPIC_API_KEY' in (ROOT/'catalog/agent.py').read_text()
