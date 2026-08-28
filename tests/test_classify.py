from catalog import classify


def test_four_way_classification():
    existing = [
        {'id': 'a', 'model_no': '8225', 'price': '20'},
        {'id': 'b', 'model_no': 'OLD1', 'price': '9'},
    ]
    drafts = [
        {'model_no': '8225', 'price': '23'},   # 改值=更新
        {'model_no': '8226', 'price': '25'},   # 新型号=新增
    ]
    r = classify.classify('razor', drafts, existing)
    assert len(r['new']) == 1 and r['new'][0]['model_no'] == '8226'
    assert len(r['update']) == 1 and r['update'][0][0]['id'] == 'a'
    assert r['update'][0][1]['price'] == '23'
    assert len(r['delist']) == 1 and r['delist'][0]['id'] == 'b'


def test_same_value_not_update():
    existing = [{'id': 'a', 'model_no': '8225', 'price': '20'}]
    r = classify.classify('razor', [{'model_no': '8225', 'price': '20'}], existing)
    assert r['update'] == [] and r['delist'] == []
