"""Real model, approved WeChat rules, isolated notes and local TG delivery."""
import sys,json,tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import db,cs,tickets,llm
from catalog.wechat_customer import activate
from catalog.csbot import CsBot


def run(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    results=[]
    with tempfile.TemporaryDirectory() as tmp:
        c=db.connect(str(Path(tmp)/'shop.db'));db.init_db(c)
        c.execute("UPDATE shop_profile SET shop_name='微信规则验收',tg_bot_id='123',owner_wechat='TEST-OWNER' WHERE id=1")
        activate(c);c.commit()
        api=SimpleNamespace(sent=[],documents=[])
        api.send_message=lambda *a:api.sent.append(a)
        api.send_document=lambda *a:api.documents.append(a)
        bot=CsBot(c,api,llm=llm,img_dir=str(Path(tmp)/'photos'))
        def message(text,uid):
            bot.handle_update({'update_id':uid,'message':{'from':{'id':100},'chat':{'id':100,'type':'private'},'text':text}})
            return '\n'.join(x[1] for x in api.sent[-1:])
        first=message('可以加急赶货吗？',1)
        old=cs.get_redline(c)['text_raw']
        ticket=tickets.create(c,'redline',None,{'kind':'redline','product_id':None,'text_raw':'只有要求加急赶货才转人工','text_summary':'只有要求加急赶货才转人工','old_text_raw':old})
        tickets.decide(c,ticket['id'],ticket['token'],True)
        second=message('帮我记杯子100个，能加急赶货吗？',2)
        third=message('出表，同时能加急赶货吗？',3)
        old=cs.get_redline(c)['text_raw']
        ticket=tickets.create(c,'redline',None,{'kind':'redline','product_id':None,'text_raw':'','text_summary':'','old_text_raw':old})
        tickets.decide(c,ticket['id'],ticket['token'],True)
        fourth=message('可以加急赶货吗？',4)
        notes=[json.loads(r[0]) for r in c.execute('SELECT fields_json FROM cs_note')]
        results=[{'case':'unset','passed':'TEST-OWNER' not in first},
                 {'case':'approved_plus_record','passed':'TEST-OWNER' in second and len(notes)==1 and notes[0].get('数量')=='100个'},
                 {'case':'export_plus_redline','passed':'TEST-OWNER' in third and bool(api.documents)},
                 {'case':'cleared','passed':'TEST-OWNER' not in fourth}]
        if api.documents:(out/'微信红线采购清单.xlsx').write_bytes(api.documents[0][2])
        (out/'result.json').write_text(json.dumps({'results':results,'replies':[first,second,third,fourth],'notes':notes},ensure_ascii=False,indent=2))
        c.close()
    print(json.dumps(results,ensure_ascii=False))
    return all(x['passed'] for x in results)

if __name__=='__main__':sys.exit(0 if run(sys.argv[1] if len(sys.argv)>1 else 'audit/round18/real-wechat-policy') else 1)
