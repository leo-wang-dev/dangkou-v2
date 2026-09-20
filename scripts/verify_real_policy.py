"""Actual configured text model against frozen customer-service quote/redline examples."""
from pathlib import Path
import sys,json,time,sqlite3
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import db,llm,config
from catalog.csbot import CsBot

CASES=[
 ('greeting','你好',False,None),
 ('price30','TEST-01 30个多少钱',True,None),
 ('price60','TEST-01 60个多少钱',True,None),
 ('small','TEST-01 15个多少钱',True,None),
 ('below_cost','TEST-01 30个，8块钱卖不卖',True,None),
 ('bottom','TEST-01 1000个最低多少钱',True,None),
 ('credit','TEST-01 可以欠账月结吗',True,None),
 ('complaint','TEST-01 买的货质量差，要求退货',True,None),
 ('custom','TEST-01 能定制logo吗',True,None),
 ('prohibited','有军火弹药卖吗',True,None),
]


def run(out):
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 if not config.BAILIAN_API_KEY:raise RuntimeError('BAILIAN_API_KEY missing')
 def check(case):
  name,text,transfer,price=case
  conn=sqlite3.connect(':memory:',check_same_thread=False);conn.row_factory=sqlite3.Row;db.init_db(conn)
  conn.execute("UPDATE shop_profile SET owner_tg_username='test_owner',owner_wechat='TEST-WECHAT'")
  conn.execute("INSERT INTO product_curler(id,inner_code,item_no,price,cs_visible) VALUES('p1','TEST-1','TEST-01','8',1)")
  conn.commit()
  bot=CsBot(conn,None,llm=llm,notifier=lambda _:None,img_dir=str(out/'photos'))
  started=time.monotonic()
  try:
   reply=bot._on_text({'id':'buyer'},text)
   actual='TEST-WECHAT' in reply
   passed=actual==transfer and (price is None or price in reply)
   result={'case':name,'input':text,'reply':reply,'expected_transfer':transfer,'passed':passed,'elapsed_sec':round(time.monotonic()-started,2)}
  except Exception as exc:result={'case':name,'passed':False,'error_type':type(exc).__name__}
  finally:conn.close()
  (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(name,result['passed'],flush=True)
  return result
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(check,CASES))
 (out/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
 return all(r['passed'] for r in results)


if __name__=='__main__':
 sys.exit(0 if run(sys.argv[1] if len(sys.argv)>1 else 'audit/round6/real-policy') else 1)
