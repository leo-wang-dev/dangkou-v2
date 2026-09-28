from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image

from catalog.fast_import import parse_structured


def test_exact_header_groups_continuations_and_images(tmp_path):
    photo = tmp_path / 'photo.png'
    Image.new('RGB', (12, 12), 'red').save(photo)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    sheet.append(['产品型号', '产品名称', '产品图片', '产品价格', '装箱数量'])
    sheet.append(['A1', '一号', None, '82元', '50台/箱'])
    sheet.append([None, None, None, None, '30台/箱'])
    sheet.append([None, '二号', '#NAME?', '90元', '20台/箱'])
    sheet.add_image(ExcelImage(photo), 'C2')
    sheet.add_image(ExcelImage(photo), 'C3')
    path = tmp_path / 'products.xlsx'
    book.save(path)
    fields = [
        {'key': 'model', 'label': '产品型号', 'role': 'model'},
        {'key': 'name', 'label': '产品名称', 'role': 'spec'},
        {'key': 'image', 'label': '产品图片', 'role': 'image'},
        {'key': 'price', 'label': '产品价格', 'role': 'price'},
        {'key': 'stock', 'label': '装箱数量', 'role': 'stock'},
    ]
    result = parse_structured({'fields': fields}, path, tmp_path / 'out')
    assert [item['model'] for item in result['products']] == ['A1', 'A1']
    assert result['products'][0]['stock'] == '50台/箱\n30台/箱'
    assert result['products'][0]['source_rows'] == [2, 3]
    assert len(result['products'][0]['images']) == 2
    assert result['products'][1]['source_rows'] == [4]
    assert result['failures'][0]['source_rows'] == [4]


def test_mismatched_header_falls_back(tmp_path):
    book = Workbook()
    book.active.append(['型号', '品名'])
    book.active.append(['A1', '一号'])
    path = tmp_path / 'different.xlsx'
    book.save(path)
    assert parse_structured({'fields': [
        {'key': 'model', 'label': '产品型号', 'role': 'model'},
        {'key': 'name', 'label': '产品名称', 'role': 'spec'},
    ]}, path, tmp_path / 'out') is None


def test_product_import_uses_fast_path_and_keeps_coverage(tmp_path, monkeypatch):
    from catalog.dynamic_import import _agent_rows

    book = Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    sheet.append(['产品型号', '产品名称', '备注'])
    sheet.append(['A1', '一号', '首行'])
    sheet.append([None, None, '补充'])
    sheet.append(['A2', '二号', '末行'])
    path = tmp_path / 'products.xlsx'
    book.save(path)
    monkeypatch.setattr('catalog.agent.parse_dynamic', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('model called')))
    rows = _agent_rows({'key': 'test', 'fields': [
        {'key': 'model', 'label': '产品型号', 'role': 'model'},
        {'key': 'name', 'label': '产品名称', 'role': 'spec'},
        {'key': 'note', 'label': '备注', 'role': 'note'},
    ]}, str(path), tmp_path / 'work', sheet='Sheet1')
    assert len(rows) == 2
    assert rows[0]['data']['note'] == '首行\n补充'
    assert rows.coverage['uncertain'] is False
    assert rows.coverage['logical_products'] == 2
