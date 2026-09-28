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


def test_buyer_identity_must_match_private_user_app_port(monkeypatch):
    module=load()
    monkeypatch.delenv('CUSTOMER_IDENTITY_BASE_URL',raising=False)
    monkeypatch.setenv('USER_APP_PORT','19211')
    assert any('CUSTOMER_IDENTITY_BASE_URL' in e for e in module.missing())
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL','http://127.0.0.1:19100')
    assert any('CUSTOMER_IDENTITY_BASE_URL' in e and 'USER_APP_PORT' in e for e in module.missing())
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL','http://127.0.0.1:19211')
    assert not any('CUSTOMER_IDENTITY_BASE_URL' in e for e in module.missing())
    monkeypatch.setenv('CUSTOMER_IDENTITY_BASE_URL','http://127.0.0.1:not-a-port')
    assert any('CUSTOMER_IDENTITY_BASE_URL' in e for e in module.missing())


def test_sample_public_tool_targets_same_private_user_app():
    sample=(ROOT/'deploy/nginx-merchant.conf').read_text()
    import re
    port=re.search(r'^USER_APP_PORT=(\d+)$',(ROOT/'.env.example').read_text(),re.M).group(1)
    assert f'proxy_pass http://127.0.0.1:{port}/;' in sample


def test_preflight_rejects_wrong_public_tool_upstream(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setenv('USER_APP_PORT', '19211')
    config = tmp_path / 'site.conf'
    config.write_text('location /tool/ {\n proxy_pass http://127.0.0.1:19100/;\n}\n')
    assert any('/tool/' in error for error in module.missing(nginx_config=config))
    config.write_text('location /tool/ {\n proxy_pass http://127.0.0.1:19211/;\n}\n')
    assert not any('/tool/' in error for error in module.missing(nginx_config=config))

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


def test_safe_dotenv_loader_and_deploy_caller(tmp_path,monkeypatch):
    module=load()
    sample=tmp_path/'sample.env';sentinel=tmp_path/'should-not-exist'
    sample.write_text('RESEND_FROM="Trial Sender <trial@example.test>"\nRESEND_API_KEY=placeholder\nLITERAL="$(touch '+str(sentinel)+')"\n')
    for name in ('RESEND_FROM','RESEND_API_KEY','LITERAL'):monkeypatch.setenv(name,'before-test')
    module.load_env(sample)
    assert os.environ['RESEND_FROM']=='Trial Sender <trial@example.test>'
    assert not sentinel.exists()
    # Execute only local orchestration against hermetic SSH/rsync stubs. Remote
    # payloads are captured, never evaluated or sent to a host.
    fake=tmp_path/'commands';fake.mkdir();capture=tmp_path/'remote.txt'
    for name in ('ssh','rsync','sudo','systemctl','install'):
        content='#!/bin/sh\n'
        content+=('/bin/cat >> "$CAPTURE"\n' if name=='ssh' else ('exit 0\n' if name=='rsync' else 'exit 99\n'))
        p=fake/name;p.write_text(content);p.chmod(0o755)
    (fake/'dirname').symlink_to(shutil.which('dirname'))
    result=subprocess.run(['/bin/bash',str(ROOT/'deploy/deploy.sh')],env={**os.environ,'PATH':str(fake),'DEPLOY_HOST':'invalid.test','CAPTURE':str(capture)},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'scripts/preflight.py --env-file .env' in capture.read_text()
