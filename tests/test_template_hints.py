from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image

from catalog.dynamic_import import _attach_image_header_hints
from catalog import notify


def test_unlabelled_image_column_has_explicit_review_hint(tmp_path):
    photo = tmp_path / 'photo.png'
    Image.new('RGB', (12, 12), 'red').save(photo)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    sheet.append(['ITEM.NO 型号', None, '价格'])
    sheet.append(['8225', None, 21.5])
    sheet.add_image(ExcelImage(photo), 'B2')
    path = tmp_path / 'huayue.xlsx'
    book.save(path)

    sections = [{'source_sheet': 'Sheet1'}]
    discovered = [{'source_sheet': 'Sheet1', 'header_row': 1, 'fields': [
        {'key': 'model', 'role': 'model', 'source_column': 1},
        {'key': 'image', 'role': 'image', 'source_column': 2},
    ]}]
    _attach_image_header_hints(sections, discovered, str(path))
    assert sections[0]['review_hints'] == [{
        'kind': 'unlabeled_image_column', 'column': 2, 'image_count': 1, 'mapped': True}]

    sections = [{'source_sheet': 'Sheet1'}]
    discovered[0]['fields'].pop()
    _attach_image_header_hints(sections, discovered, str(path))
    assert sections[0]['review_hints'][0]['mapped'] is False


def test_template_notification_explains_unlabelled_image_column(monkeypatch):
    monkeypatch.setenv('CATALOG_V2_MANAGE_URL', 'https://example.test/manage')
    payload = {'stats': {'phase': 'template', 'categories': ['华岳电器'],
                         'review_hints': [{'kind': 'unlabeled_image_column',
                                           'source_sheet': 'Sheet1', 'column': 2,
                                           'image_count': 58, 'mapped': False}]}}
    message = notify.render_import(payload)
    assert 'Sheet1 的 B 列有图片但没有文字表头' in message
    assert '补“图片”字段' in message

    payload['stats']['review_hints'][0]['mapped'] = True
    message = notify.render_import(payload)
    assert 'AI 已建议“图片”字段' in message
    assert '补“图片”字段' not in message
