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


def test_image_only_changes_and_identical_renamed_images(tmp_path):
    import json
    old = tmp_path/'old'; new = tmp_path/'new'
    old.mkdir(); new.mkdir()
    (old/'original.jpg').write_bytes(b'old-image')
    (new/'renamed.jpg').write_bytes(b'old-image')
    row={'id':'a','model_no':'8225','price':'20','images':json.dumps(['original.jpg'])}
    draft={'model_no':'8225','price':'20','images':['renamed.jpg']}
    args={'draft_image_root':str(new),'existing_image_root':str(old)}
    assert not classify.classify('razor',[draft],[row],**args)['update']
    (new/'renamed.jpg').write_bytes(b'new-image')
    assert len(classify.classify('razor',[draft],[row],**args)['update'])==1
    assert len(classify.classify('razor',[{**draft,'images':[]}],[row],**args)['update'])==1
    # If the parser supplied no image fields, preserve the existing image collection.
    assert not classify.classify('razor',[{'model_no':'8225','price':'20'}],[row],**args)['update']
