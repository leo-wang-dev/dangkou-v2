import subprocess,json,base64,shlex
from pathlib import Path
import requests
remote='''from pathlib import Path
import urllib.request,json
key=Path('/home/ubuntu/dsh-wechat-test/data/wechat/.admin-token').read_text().strip()
req=urllib.request.Request('http://127.0.0.1:17615/v1/login/start',data=b'{}',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=25) as r:print(r.read().decode())
'''
result=json.loads(subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ControlPath=/tmp/dangkou-test-ssh.sock','ubuntu@134.175.135.102','python3 -c '+shlex.quote(remote)],text=True))
s=result['session']
Path('audit/round18/wechat-login-session.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
if s.get('qrPngBase64'):
    Path('audit/round18/微信测试登录二维码.png').write_bytes(base64.b64decode(s['qrPngBase64']))
elif s.get('qrUrl'):
    r=requests.get(s['qrUrl'],timeout=20);r.raise_for_status()
    if r.content.startswith(b'\x89PNG') or r.content.startswith(b'\xff\xd8'):
        Path('audit/round18/微信测试登录二维码.png').write_bytes(r.content)
    else:
        import qrcode
        qrcode.make(s['qrUrl']).save('audit/round18/微信测试登录二维码.png')
print(json.dumps({'state':s['state'],'expiresAt':s['expiresAt'],'qr_saved':Path('audit/round18/微信测试登录二维码.png').exists()},ensure_ascii=False))
