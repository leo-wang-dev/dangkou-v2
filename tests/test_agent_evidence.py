from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image


def test_small_workbook_evidence_preserves_cells_and_anchor(tmp_path):
    from catalog.agent_evidence import prepare

    photo = tmp_path / 'photo.png'
    Image.new('RGB', (8, 8), 'red').save(photo)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    sheet.append(['型号', '图片', None])
    sheet.append(['KZ-228', None, '备注原文'])
    sheet.add_image(ExcelImage(photo), 'B2')
    path = tmp_path / 'source.xlsx'
    book.save(path)
    evidence = prepare(path, tmp_path / 'work', sheet='Sheet1')
    assert evidence['sheets'][0]['rows'][1]['cells'] == [[1, 'KZ-228'], [3, '备注原文']]
    assert evidence['sheets'][0]['images'][0]['row'] == 2
    assert (tmp_path / 'work' / evidence['sheets'][0]['images'][0]['name']).is_file()
