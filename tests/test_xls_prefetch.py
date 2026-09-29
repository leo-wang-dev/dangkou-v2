from catalog.dynamic_import import _safe_prefetched_xls


def test_prefetched_xls_requires_converted_image_evidence_for_blank_column():
    early = {'evidence': [{'title': 'Products', 'grid': [['型号', '', '价格']],
                           '图片锚点列': {}, '合并区': []}],
             'discovered': [{'title': 'Products', 'fields': []}]}
    converted = [{'title': 'Products', 'grid': [['型号', '', '价格']],
                  '图片锚点列': {2: 12}, '合并区': []}]
    assert not _safe_prefetched_xls(early, converted)
    converted[0]['图片锚点列'] = {1: 12}
    assert _safe_prefetched_xls(early, converted)


def test_prefetched_xls_rejected_when_conversion_reveals_missing_header():
    early = {'evidence': [{'title': 'Products', 'grid': [['型号', '']],
                           '图片锚点列': {}, '合并区': []}],
             'discovered': [{'title': 'Products', 'fields': []}]}
    converted = [{'title': 'Products', 'grid': [['型号', '颜色']],
                  '图片锚点列': {}, '合并区': []}]
    assert not _safe_prefetched_xls(early, converted)
