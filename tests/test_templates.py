from catalog import templates


def test_two_categories_with_dedup_keys():
    assert set(templates.TEMPLATES) == {'razor', 'curler'}
    assert templates.TEMPLATES['razor'].dedup_field == 'model_no'
    assert templates.TEMPLATES['curler'].dedup_field == 'item_no'


def test_field_labels_match_prd():
    razor_labels = [l for _, l in templates.TEMPLATES['razor'].fields]
    assert '产品型号' in razor_labels and '彩盒尺寸(mm)' in razor_labels
    curler_labels = [l for _, l in templates.TEMPLATES['curler'].fields]
    assert '发热体' in curler_labels and '频率' in curler_labels


def test_row_to_dict_maps_template_fields():
    t = templates.TEMPLATES['razor']
    row = {'id': 'x', 'inner_code': 'KS-AAAAAAAA', 'model_no': '8225',
           'color': '黑', 'status': 'approved', 'image_main': 'a.png'}
    d = templates.row_to_dict(t, row)
    assert d['产品型号'] == '8225' and d['颜色'] == '黑' and d['内部货号'] == 'KS-AAAAAAAA'
