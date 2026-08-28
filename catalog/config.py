"""配置：密钥只从 .env（gitignored）或进程 env 读取，绝不入库。"""
import os

_ENV = os.path.join(os.path.dirname(__file__), '..', '.env')
try:
    for line in open(_ENV, encoding='utf-8'):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

DB_PATH = os.environ.get('CATALOG_V2_DB',
                         os.path.join(os.path.dirname(__file__), '..', 'data', 'catalog.db'))
IMG_DIR = os.environ.get('CATALOG_V2_IMG',
                         os.path.join(os.path.dirname(__file__), '..', 'data', 'images'))
SERVICE_TOKEN = os.environ.get('CATALOG_V2_SERVICE_TOKEN', '')
AGENT_BASE_URL = os.environ.get('CATALOG_AGENT_BASE_URL', 'https://ccdox.0755.click')
AGENT_API_KEY = os.environ.get('CATALOG_AGENT_API_KEY', '')
AGENT_MODEL = os.environ.get('CATALOG_AGENT_MODEL', 'claude-sonnet-5')
AGENT_TIMEOUT = int(os.environ.get('CATALOG_AGENT_TIMEOUT', '2700'))
BAILIAN_API_KEY = os.environ.get('BAILIAN_API_KEY', '')
BAILIAN_BASE_URL = os.environ.get(
    'BAILIAN_BASE_URL',
    'https://ws-gox0m0606t27jojm.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
