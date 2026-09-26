"""Real photos through CsBot kernel (H5 直调) + SQLite + /cs/link export；模型可离线回放或真实。

C 端 TG 传输拆除（删C）后，本验收改为与 H5 路由相同的内核直调：
_prepare_photo/_on_photo/_on_text；清单导出走 /cs/link/{token}/export.xlsx。
不再有任何网络传输假件。
"""
import argparse
import hashlib
from pathlib import Path
import io
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import openpyxl
from fastapi import FastAPI
from fastapi.testclient import TestClient
from catalog import db, config, llm
from catalog.api import register_routes
from catalog.csbot import CsBot
from catalog.storage import LocalStorage


class ReplayModel:
    """Visually reviewed reference responses. Not OCR and not a quality measurement."""
    def __init__(self, cases, images):
        self.answers={hashlib.sha256(images[c['id']]).hexdigest():c['items'] for c in cases}

    def chat_vision(self, prompt, data):
        return json.dumps(self.answers[hashlib.sha256(data).hexdigest()],ensure_ascii=False)

    def chat_text(self, system, messages, **kwargs):
        if '草稿编辑判定器' in system:
            text=messages[-1]['content'].split('客户消息：')[-1]
            if '1 颜色改成黑色' in text:
                return json.dumps({'action':'edit','index':1,'field':'颜色','value':'黑色'})
            if '起订量一箱起' in text:
                return json.dumps({'action':'add','index':1,'field':'起订量','value':'一箱起'})
            return '{"action":"none"}'
        return '<<PASS>>'


class RecordedModel:
    def __init__(self, out):
        self.out=out
        self.calls=0

    def _record(self, kind, invoke, prompt):
        self.calls+=1
        start=time.monotonic()
        response=invoke()
        (self.out/f'model-{self.calls:02d}-{kind}.json').write_text(json.dumps({
            'kind':kind,'model':llm.VISION_MODEL if kind=='vision' else llm.TEXT_MODEL,
            'prompt':prompt,'elapsed_sec':round(time.monotonic()-start,2),'response':response},ensure_ascii=False,indent=2))
        return response

    def chat_vision(self,prompt,data):
        return self._record('vision',lambda:llm.chat_vision(prompt,data),prompt)

    def chat_text(self,system,messages,**kwargs):
        return self._record('text',lambda:llm.chat_text(system,messages,**kwargs),{'system':system,'messages':messages})


def run(out, real_model=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if (out/'catalog.db').exists():
        raise RuntimeError('输出目录已有测试数据库，请使用新的 --out 目录，避免混入上次数据')
    dataset=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/customer_photos.json').read_text())
    source=Path(__import__('os').environ.get('CS_SAMPLE_PHOTO_DIR',dataset['source_dir']))
    if real_model and not config.BAILIAN_API_KEY:
        raise RuntimeError('BLOCKED: 缺 BAILIAN_API_KEY；照片已齐，不能用参考数据回放冒充真实识图')
    images={c['id']:(source/c['file']).read_bytes() for c in dataset['cases']}
    conn=db.connect(str(out/'catalog.db'));db.init_db(conn)
    conn.execute("UPDATE shop_profile SET owner_tg_username='test_owner', owner_wechat='TEST-ONLY-WECHAT'");conn.commit()
    model=RecordedModel(out) if real_model else ReplayModel(dataset['cases'],images)
    notices=[]
    photo_dir=(out/'photos').resolve()
    bot=CsBot(conn,None,llm=model,img_dir=str(photo_dir))
    app=FastAPI();app.state.conn=conn;app.state.token='local-test-only';app.state.storage=LocalStorage(str(out/'images'));app.state.callback=None
    register_routes(app)
    results=[]
    transient_failures=[]
    replies=[]
    cust=None
    try:
        from catalog import cs_i18n
        for case in dataset['cases']:
            if cust is None:
                cust=bot._ensure_customer({'id':'9001','username':'fixture_buyer'})
                cs_i18n.set_language(conn,cust['id'],'中文');conn.commit()
            start=time.monotonic()
            for attempt in range(3):
                try:
                    prepared=bot._prepare_photo(cust,images[case['id']])
                    reply=bot._on_photo(cust,None,prepared=prepared)
                    break
                except ValueError as exc:
                    if '空内容' not in str(exc) or attempt==2:
                        raise
                    transient_failures.append({'photo':case['id'],'attempt':attempt+1,'error':'provider_empty_content'})
                    (out/'transient-failures.json').write_text(json.dumps(transient_failures,ensure_ascii=False,indent=2))
                    time.sleep(2*(attempt+1))
            replies.append(reply)
            elapsed=round(time.monotonic()-start,2)
            rows=conn.execute('SELECT fields_json,photo FROM cs_note WHERE customer_id=? ORDER BY id DESC',(cust['id'],)).fetchall()
            recent=[json.loads(r[0]) for r in rows if r['photo']==rows[0]['photo']]
            joined=json.dumps(recent,ensure_ascii=False).lower()
            checks={term:term.lower() in joined for term in case['required_terms']}
            count_ok=len(recent)==len(case['items'])
            if case['id']=='hair_oil':
                checks['unclear_price_not_invented']=all('模糊' in str(f.get('价格','')) for f in recent)
            if case['id']=='two_tubes':
                cropped=[f for f in recent if '260' in str(f.get('体积或尺寸',''))]
                checks['cropped_price_not_invented']=bool(cropped) and all(any(word in str(f.get('价格','')) for word in ('模糊','未拍到')) for f in cropped)
            (out/(case['id']+'-extraction.json')).write_text(json.dumps({'fields':recent,'checks':checks,'count_ok':count_ok},ensure_ascii=False,indent=2))
            assert all(checks.values()) and count_ok,(case['id'],recent)
            results.append({'photo':case['id'],'elapsed_sec':elapsed,'sha256':hashlib.sha256(images[case['id']]).hexdigest(),'term_checks':checks,'fields':recent})
        for text in ['1 颜色改成黑色','第1条起订量一箱起','你们在哪条街','确认','出表','这些样品有货吗？找老板询价']:
            replies.append(bot._on_text(cust,text))
        from catalog import notify as notify_mod
        notify_mod.deliver(conn,notifier=notices.append,file_sender=lambda body:None)   # 通知合一：内核只入队，投递统一走 notify.deliver
        fields=json.loads(conn.execute('SELECT fields_json FROM cs_note WHERE customer_id=? ORDER BY id LIMIT 1',(cust['id'],)).fetchone()[0])
        assert fields['颜色']=='黑色' and fields['起订量']=='一箱起'
        token=conn.execute('SELECT token FROM cs_link WHERE customer_id=?',(cust['id'],)).fetchone()[0]
        with TestClient(app) as client:
            data=client.get(f'/cs/link/{token}').json()
            response=client.get(f'/cs/link/{token}/export.xlsx');assert response.status_code==200
            (out/'customer_purchase_list.xlsx').write_bytes(response.content)
            wb=openpyxl.load_workbook(io.BytesIO(response.content));ws=wb.active
            note_count=conn.execute("SELECT COUNT(*) FROM cs_note WHERE customer_id=? AND status='confirmed'",(cust['id'],)).fetchone()[0]
            assert ws.max_row==note_count+1 and len(ws._images)==note_count
            assert client.get('/cs/link/not-a-real-link/export.xlsx').status_code in (403,404,410)
        assert notices and 'TEST-ONLY-WECHAT' in replies[-1]
        evidence={'mode':'real-model-kernel' if real_model else 'reference-fixture-replay-kernel',
                  'not_model_quality_proof':not real_model,'transient_failures':transient_failures,'photo_count':4,'confirmed_items':note_count,
                  'embedded_excel_images':len(ws._images),
                  'photos':results,'replies':replies,'merchant_notifications':notices}
        (out/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in evidence.items() if k not in ('photos','replies','merchant_notifications')},ensure_ascii=False))
        return evidence
    finally:
        conn.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='audit/round4/photo-flow');p.add_argument('--real-model',action='store_true');args=p.parse_args()
    try:run(args.out,args.real_model)
    except RuntimeError as exc:
        if str(exc).startswith('BLOCKED:'):
            print(exc);sys.exit(2)
        raise
