"""Read-only deployment prerequisites. Optional explicit dotenv input; no subprocesses, network or DB writes."""
import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit


def load_env(path):
    """Read a deliberately selected dotenv file; never shell-evaluate its values."""
    import re
    import shlex
    values={}
    for number,line in enumerate(Path(path).read_text().splitlines(),1):
        line=line.strip()
        if not line or line.startswith('#'):continue
        if line.startswith('export '):line=line[7:].lstrip()
        key,sep,value=line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key.strip()):
            raise ValueError(f'Invalid dotenv assignment at line {number}')
        try:parts=shlex.split(value,comments=True,posix=True)
        except ValueError:raise ValueError(f'Invalid dotenv quoting at line {number}') from None
        if len(parts)>1:raise ValueError(f'Quote dotenv whitespace at line {number}')
        values[key.strip()]=parts[0] if parts else ''
    os.environ.update(values)


def missing():
    required = ['CATALOG_V2_SERVICE_TOKEN','CATALOG_V2_PUBLIC_URL',
        'BAILIAN_API_KEY','BAILIAN_BASE_URL','CATALOG_AGENT_API_KEY',
        'CATALOG_AGENT_BASE_URL','CATALOG_AGENT_MODEL','CATALOG_NOTIFY_TOKEN',
        'CATALOG_AGENT_CONTAINER_IMAGE','CATALOG_AGENT_NETWORK','CATALOG_PARSER_STATE_DIR',
        'CATALOG_V2_DB','CATALOG_V2_IMG','CATALOG_CS_PHOTOS','USER_APP_DB','USER_APP_PHOTOS']
    development = os.environ.get('USER_APP_ENV') == 'development' and os.environ.get('USER_APP_DEV_EMAIL_LOG') == '1'
    if not development:
        required += ['RESEND_API_KEY','RESEND_FROM']
    errors = [name + ' 未配置' for name in required if not os.environ.get(name)]
    for binary in ('docker','node','soffice'):
        if not shutil.which(binary):
            errors.append('缺少运行依赖: '+binary)
    public = os.environ.get('CATALOG_V2_PUBLIC_URL','')
    if public and (urlsplit(public).scheme != 'https' or not urlsplit(public).hostname):
        errors.append('CATALOG_V2_PUBLIC_URL 必须为可访问的 HTTPS 地址')
    agent = urlsplit(os.environ.get('CATALOG_AGENT_BASE_URL',''))
    if agent.hostname in ('localhost','127.0.0.1','::1'):
        errors.append('CATALOG_AGENT_BASE_URL: parser container localhost is not the host; use the dedicated bridge DNS')
    if os.environ.get('CATALOG_AGENT_NETWORK') in ('host','bridge','none'):
        errors.append('CATALOG_AGENT_NETWORK must name a dedicated Docker network')
    if agent.hostname == 'litellm':
        for name in ('LITELLM_MASTER_KEY','LITELLM_UPSTREAM_MODEL','LITELLM_UPSTREAM_BASE_URL','LITELLM_UPSTREAM_API_KEY'):
            if not os.environ.get(name):errors.append(name+' 未配置')
        if os.environ.get('CATALOG_AGENT_API_KEY') != os.environ.get('LITELLM_MASTER_KEY'):
            errors.append('CATALOG_AGENT_API_KEY must match LITELLM_MASTER_KEY')
        if os.environ.get('CATALOG_AGENT_MODEL') != 'trial-parser':
            errors.append('CATALOG_AGENT_MODEL must match sample bridge alias trial-parser')
        if os.environ.get('CATALOG_AGENT_NETWORK') != 'dangkou-trial-parser':
            errors.append('CATALOG_AGENT_NETWORK must match sample compose network dangkou-trial-parser')
        if agent.path.rstrip('/'):
            errors.append('CATALOG_AGENT_BASE_URL must be gateway root without /v1')
    for name in ('CATALOG_PARSER_STATE_DIR','CATALOG_V2_DB','USER_APP_DB','CATALOG_V2_IMG','CATALOG_CS_PHOTOS','USER_APP_PHOTOS'):
        value=os.environ.get(name)
        if value and not Path(value).is_absolute():errors.append(name+' must be an absolute persistent path')
    if os.environ.get('CATALOG_V2_DB') and os.environ.get('CATALOG_V2_DB')==os.environ.get('USER_APP_DB'):
        errors.append('USER_APP_DB and CATALOG_V2_DB must be separate databases')
    if not (Path(__file__).resolve().parents[1]/'frontend/src/customer-languages.json').is_file():
        errors.append('Missing packaged canonical customer language resource')
    return errors


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run',action='store_true',help='Explicit read-only mode (also the default)')
    parser.add_argument('--env-file',type=Path,help='Explicit read-only dotenv input; values never executed or printed')
    args=parser.parse_args()
    if args.env_file:load_env(args.env_file)
    errors=missing()
    for error in errors:print('BLOCKED:',error)
    print('配置预检通过（未验证真实模型、邮件、SSH、容器及公网连通）' if not errors else '部署预检未通过（只读，未修改配置）')
    sys.exit(2 if errors else 0)
