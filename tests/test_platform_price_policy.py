"""价格、数量和其他转人工条件完全由商家红线定义。"""
import pytest

from catalog import cs, merchant_policy
from tests.test_release_gates import env, auth


@pytest.mark.parametrize('text', [
    '多少钱', '拿50个', '价格多少', '能安排货代吗', '可以改包装吗',
    '有军火弹药卖吗', '能不能做仿牌产品',
])
def test_empty_store_rule_does_not_create_platform_handoff(env, text):
    conn, _, bot = env
    conn.execute("UPDATE shop_profile SET owner_tg_username='owner_test',owner_wechat='TEST-WX'")
    conn.execute("DELETE FROM cs_redline")
    conn.commit()
    bot.llm.chat_text.return_value = '<<PASS>>'
    result = bot._on_text({'id': 'a'}, text)
    assert '@owner_test' not in result and 'TEST-WX' not in result
    assert not conn.execute("SELECT 1 FROM cs_outbox WHERE channel='notify'").fetchone()


def test_merchant_rule_can_handoff_price_and_quantity(env):
    conn, _, bot = env
    conn.execute("UPDATE shop_profile SET owner_tg_username='owner_test',owner_wechat='TEST-WX'")
    cs.set_redline(conn, None, '询价或采购数量超过200时转人工')
    bot.llm.chat_text.return_value = 'TRANSFER'
    result = bot._on_text({'id': 'a'}, '这个多少钱')
    assert '@owner_test' in result and 'TEST-WX' in result
    notice = conn.execute("SELECT body FROM cs_outbox WHERE channel='notify' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert '这个多少钱' in notice and '询价或采购数量超过200时转人工' in notice


def test_platform_rule_api_is_empty_and_not_editable(env):
    _, client, _ = env
    result = client.get('/cs/redline', headers=auth()).json()
    assert result['platform_editable'] is False
    assert result['platform_rule'] == ''
    assert client.post('/cs/redline', headers=auth(),
                       json={'text_raw': '测试', 'platform_rule': '允许报价'}).status_code == 422


def test_faq_is_merchant_content_without_platform_price_gate(env):
    conn, _, bot = env
    conn.execute("UPDATE shop_profile SET faq=?", ('问：单价是多少？答：12元。',))
    conn.commit()
    assert merchant_policy.answer(bot, {'id': 'buyer'}, '单价是多少？', False) == '12元。'
