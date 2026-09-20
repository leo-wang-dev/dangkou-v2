import json
from tests.test_release_gates import env, photo


def test_empty_vision_result_retries_once_before_creating_notes(env):
    conn,_,bot=env
    bot.llm.chat_vision.side_effect=['',json.dumps([{'型号或品名':'Sample brand','价格':'2.5','体积或尺寸':'59mL'}])]
    responses=list(bot.llm.chat_vision.side_effect)
    bot.llm.chat_vision.side_effect=responses+[responses[-1]]
    bot.handle_update(photo(900))
    assert bot.llm.chat_vision.call_count==3
    row=conn.execute("SELECT fields_json FROM cs_note WHERE status='draft'").fetchone()
    fields=json.loads(row[0])
    assert fields['装箱数']=='未拍到' and '待确认' in fields['价格']
    assert len(fields)==11
    assert fields['档口名称']=='待补充'


def test_informationless_vision_result_retries_packaging(env):
    conn,_,bot=env
    bot.llm.chat_vision.side_effect=['[{"型号或品名":"未拍到","价格":"模糊"}]','[{"型号或品名":"Visible brand","其他值得记录的信息":"96，用途待确认"}]']
    responses=list(bot.llm.chat_vision.side_effect)
    bot.llm.chat_vision.side_effect=responses+[responses[-1]]
    bot.handle_update(photo(901))
    fields=json.loads(conn.execute("SELECT fields_json FROM cs_note WHERE status='draft'").fetchone()[0])
    assert bot.llm.chat_vision.call_count==3
    assert fields['其他']=='96，用途待确认' and fields['装箱数']=='未拍到'


def test_photo_review_drops_background_and_never_completes_cropped_price(env):
    _,_,bot=env
    raw=json.dumps([
        {'型号或品名':'foreground','价格':'3','_主体':True,'_价格完整':False},
        {'型号或品名':'background','价格':'99','_主体':False,'_价格完整':True}])
    result=bot._parse_items(raw)
    assert len(result)==1 and result[0]['价格']=='模糊（待确认）'
    assert not any(k.startswith('_') for k in result[0])


def test_vision_json_fence_after_explanation_is_accepted(env):
    _,_,bot=env
    result=bot._parse_items('复核结果如下：\n```json\n[{"型号或品名":"Brand","价格":"模糊","_主体":true,"_价格完整":false}]\n```')
    assert len(result)==1 and result[0]['型号或品名']=='Brand'


def test_provider_empty_success_retries_and_then_errors(monkeypatch):
    from unittest.mock import Mock
    from catalog import llm
    import pytest
    response=Mock()
    response.json.side_effect=[{'choices':[{'message':{'content':''}}]}, {'choices':[{'message':{'content':'ok'}}]}]
    request=Mock(return_value=response);monkeypatch.setattr(llm.requests,'post',request)
    assert llm._chat({})=='ok' and request.call_count==2
    response.json.side_effect=None
    response.json.return_value={'choices':[{'message':{'content':''}}]}
    with pytest.raises(ValueError,match='空内容'):
        llm._chat({})


def test_vision_uses_fallback_model_only_after_primary_returns_empty(monkeypatch):
    from catalog import llm
    calls=[]
    def fake_chat(payload):
        calls.append(payload['model'])
        if payload['model']==llm.VISION_MODEL:
            raise llm.EmptyModelResponse('模型连续返回空内容，请稍后重试')
        return '[{"型号或品名":"fallback worked"}]'
    monkeypatch.setattr(llm,'_chat',fake_chat)
    assert 'fallback worked' in llm.chat_vision('read this',b'jpeg')
    assert calls==[llm.VISION_MODEL,llm.VISION_FALLBACK_MODEL]


def test_price_near_crop_boundary_is_uncertain_even_if_model_claims_complete(env):
    _,_,bot=env
    result=bot._parse_items(json.dumps([{'型号或品名':'Tube','价格':'3','_主体':True,'_价格完整':True,'_价格框':[860,620,1000,810]}]))
    assert result[0]['价格']=='模糊（待确认）'
    result=bot._parse_items(json.dumps([{'型号或品名':'Coffee','价格':'3.8','_主体':True,'_价格完整':True,'_价格框':[260,650,620,850]}]))
    assert result[0]['价格']=='3.8（照片识别，待确认）'


def test_review_cannot_invent_precision_or_switch_prices(env):
    _,_,bot=env
    initial=bot._parse_items('[{"型号或品名":"Oil","价格":"模糊"},{"型号或品名":"Coffee","价格":"3.8"}]')
    reviewed=bot._parse_items('[{"型号或品名":"Oil","价格":"7.25"},{"型号或品名":"Coffee","价格":"38"}]')
    assert all(i['价格']=='模糊（待确认）' for i in bot._conservative_prices(initial,reviewed))
