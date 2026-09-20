"""Read-only deployment prerequisites. Never print credential values."""
import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import config, quote


def missing():
    required = ['CATALOG_V2_SERVICE_TOKEN', 'CATALOG_V2_PUBLIC_URL',
                'TG_BOT_TOKEN', 'BAILIAN_API_KEY', 'CATALOG_AGENT_API_KEY',
                'CATALOG_NOTIFY_TOKEN']
    errors = [name + ' 未配置' for name in required if not os.environ.get(name)]
    if not shutil.which('docker'):
        errors.append('缺少 Excel 隔离解析所需的 Docker')
    if not os.environ.get('CATALOG_AGENT_CONTAINER_IMAGE'):
        errors.append('未配置 CATALOG_AGENT_CONTAINER_IMAGE 解析容器镜像')
    public = os.environ.get('CATALOG_V2_PUBLIC_URL', '')
    if public and not public.startswith('https://'):
        errors.append('CATALOG_V2_PUBLIC_URL 必须为可访问的 HTTPS 地址')
    if not Path(quote.TEMPLATE_V2_PATH).is_file():
        errors.append('缺少商家真实报价模板 CATALOG_QUOTE_TEMPLATE')
    return errors


if __name__ == '__main__':
    errors = missing()
    for error in errors:
        print('BLOCKED:', error)
    print('配置预检通过（仍需真实收发验收）' if not errors else '部署预检未通过')
    sys.exit(2 if errors else 0)
