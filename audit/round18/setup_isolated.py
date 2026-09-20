"""Run on test server as ubuntu. Never print credentials."""
from pathlib import Path
import os,secrets,shutil,sys,json
root=Path('/home/ubuntu/dangkou-wechat-test');engine=Path('/home/ubuntu/dsh-wechat-test')

def envread(path):
    return dict(line.split('=',1) for line in Path(path).read_text().splitlines() if line and not line.startswith('#') and '=' in line)
def envwrite(path,values):
    path.write_text('\n'.join(k+'='+str(v) for k,v in values.items())+'\n');path.chmod(0o600)
side=envread('/home/ubuntu/dangkou-v2/.env')
service=secrets.token_urlsafe(32); notify=secrets.token_urlsafe(32)
reserved=side.get('TG_BOT_TOKEN','').split(':')[0]
for key in list(side):
    if key.startswith(('TG_BOT_TOKEN','MERCHANT_')):side.pop(key)
side.update(CATALOG_V2_DB=str(root/'data/catalog.db'),CATALOG_V2_IMG=str(root/'data/images'),
 CATALOG_CS_PHOTOS=str(root/'data/cs_photos'),CATALOG_V2_SERVICE_TOKEN=service,CATALOG_CS_SERVICE_TOKEN=service,
 CATALOG_V2_PUBLIC_URL='https://134.175.135.102:80/merchant/manage/eeeeeeeeeeeeeeeeeeeeeeee',
 CATALOG_CS_API_URL='http://127.0.0.1:19010',MERCHANT_HUB_ENABLED='0',WECHAT_CUSTOMER_BOT_ENABLED='1',
 WECHAT_MERCHANT_OWNER_IDS='PENDING_MANUAL_LOGIN',WECHAT_RESERVED_TG_BOT_IDS=reserved,
 WECHAT_CUSTOMER_STATE=str(root/'data/customer-runtime'),CATALOG_NOTIFY_URL='http://127.0.0.1:17616/notify',
 CATALOG_NOTIFY_TOKEN=notify,CATALOG_NOTIFY_WORKER='1',TG_PROXY_URL='http://127.0.0.1:17891')
(root/'data').mkdir(exist_ok=True)
if (root/'.env').exists():raise SystemExit('already configured; preserve existing test state')
envwrite(root/'.env',side)
if not (root/'.venv').exists():(root/'.venv').symlink_to('/home/ubuntu/dangkou-v2/.venv-unified',target_is_directory=True)
eng=envread('/home/ubuntu/dsh-engine/.env')
for key in list(eng):
    if key.startswith(('FEISHU_','WECOM_','DINGTALK_','CATALOG_','DSH_WECHAT_','WECHAT_','DSH_FEISHU_')):eng.pop(key)
eng.update(DSH_WECHAT_ENABLED='true',DSH_WECHAT_DM_ALLOWLIST='PENDING_MANUAL_LOGIN',
 DSH_WECHAT_DATA_DIR=str(engine/'data/wechat'),DSH_WECHAT_ADMIN_PORT='17615',
 DSH_FEISHU_STATE=str(engine/'state'),DSH_FEISHU_WORKSPACE=str(engine/'workspace'),
 DSH_WEB_ENABLED='false',DSH_KBADMIN_ENABLED='false',WECHAT_CUSTOMER_BOT_ENABLED='1',
 CATALOG_V2_URL='http://127.0.0.1:19010',CATALOG_V2_SERVICE_TOKEN=service,CATALOG_V2_PUBLIC_URL=side['CATALOG_V2_PUBLIC_URL'],
 CATALOG_V2_PLUGIN_PATH=str(root/'engine-plugin/catalog-v2.mjs'),CATALOG_NOTIFY_PORT='17616',CATALOG_NOTIFY_TOKEN=notify,
 CATALOG_V2_EXPORT_ROOT=str(root/'data'),DSH_BOT_NAME='档口微信手测助手')
envwrite(engine/'.env',eng)
if not (engine/'node_modules').exists():(engine/'node_modules').symlink_to('/home/ubuntu/dsh-engine/node_modules',target_is_directory=True)
(engine/'workspace').mkdir(exist_ok=True)
persona=engine/'config/persona/dangkou.md'
if not persona.exists():
 persona.parent.mkdir(parents=True,exist_ok=True);shutil.copy2('/home/ubuntu/dsh-engine/config/persona/dangkou.md',persona)
with persona.open('a') as f:f.write('\n商家在微信管理本档口。TG客户Bot通过商家发送BotFather Token的专用程序开通，不要索取或复述Token。通过cs_redline_get/set帮助商家自愿设置红线，未填写不生效；必须商家确认审批。商品维护沿用catalog工具。\n')
sys.path.insert(0,str(root));os.environ.update(side)
from catalog import db,wechat_customer
from catalog.templates import TEMPLATES
c=db.connect();db.init_db(c)
c.execute("UPDATE shop_profile SET shop_name='微信贯通测试档口',tg_bot_id='',tg_bot_username='',owner_wechat='',owner_tg_username='' WHERE id=1")
wechat_customer.activate(c)
source=db.connect('/home/ubuntu/dangkou-v2/data/merchants/caa0d647d631a356e7f5be33/catalog.db')
for t in TEMPLATES.values():
 row=source.execute(f"SELECT * FROM {t.table} WHERE status='approved' AND cs_visible=1 LIMIT 1").fetchone()
 if row:
  values=dict(row);values.pop('shop_id',None)
  cols=list(values)
  c.execute(f"INSERT INTO {t.table} ("+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',list(values.values()))
  image=values.get('image_main')
  if image:
   src=Path('/home/ubuntu/dangkou-v2/data/merchants/caa0d647d631a356e7f5be33/images')/image
   if not src.exists():src=Path('/home/ubuntu/dangkou-v2/data/images')/image
   dest=root/'data/images'/image;dest.parent.mkdir(parents=True,exist_ok=True)
   if src.exists():shutil.copy2(src,dest)
c.commit();c.close();source.close()
print('isolated configuration and sample products prepared; credentials not printed')
