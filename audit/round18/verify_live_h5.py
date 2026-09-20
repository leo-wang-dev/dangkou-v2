import subprocess,json
from pathlib import Path
from playwright.sync_api import sync_playwright
script="from pathlib import Path;print(dict(x.split('=',1) for x in Path('/home/ubuntu/dangkou-wechat-test/.env').read_text().splitlines() if '=' in x)['CATALOG_V2_SERVICE_TOKEN'])"
token=subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ControlPath=/tmp/dangkou-test-ssh.sock','ubuntu@134.175.135.102','python3 -c '+__import__('shlex').quote(script)],text=True).strip()
base='https://134.175.135.102:80/merchant/manage/eeeeeeeeeeeeeeeeeeeeeeee'
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True)
 page=browser.new_page(viewport={'width':390,'height':844})
 page.goto(base+'/?t='+token,wait_until='networkidle')
 page.locator('#v-products').click()
 page.locator('summary').filter(has_text='开通客户 Telegram Bot').click()
 page.wait_for_function("document.querySelector('#tg-bot-status').textContent.includes('未绑定')")
 text=page.locator('#tg-bot-status').inner_text()
 assert '未绑定' in text,text
 assert page.locator('input[type=password]').count()==0
 page.screenshot(path='audit/round18/live-h5.png',full_page=True)
 result={'status':text,'guide_visible':page.get_by_text('① 打开官方 BotFather').is_visible(),'url_prefix':base,'viewport_width':390}
 Path('audit/round18/live-h5.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result,ensure_ascii=False))
 browser.close()
