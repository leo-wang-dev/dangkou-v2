"""Build deterministic files for the WeChat merchant assistant acceptance run."""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'audit' / 'round21' / 'wechat-manual-kit'


def photo(path: Path, color: str, label: str) -> None:
    image = Image.new('RGB', (480, 320), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 35, 445, 285), outline='white', width=8)
    draw.text((70, 135), label, fill='white')
    image.save(path, quality=92)


def workbook(path: Path, sheet_name: str, rows: list[list], photos: list[Path]) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.merge_cells('A1:H1')
    sheet['A1'] = f'{sheet_name}测试导入表（仅用于验收）'
    sheet.append(['序号', '产品型号', '颜色', '图片', '功率', '装箱数量', '成本', '备注'])
    for row_number, values in enumerate(rows, 3):
        sheet.append(values)
        image = ExcelImage(photos[(row_number - 3) % len(photos)])
        image.width, image.height = 120, 80
        image.anchor = f'D{row_number}'
        sheet.add_image(image)
        sheet.row_dimensions[row_number].height = 64
    for width, column in [(8, 'A'), (22, 'B'), (18, 'C'), (22, 'D'),
                          (12, 'E'), (16, 'F'), (12, 'G'), (32, 'H')]:
        sheet.column_dimensions[column].width = width
    book.save(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    red = OUT / 'WX-HD15-red.jpg'
    blue = OUT / 'WX-HD15-blue.jpg'
    silver = OUT / 'WX-HD16-silver.jpg'
    unknown = OUT / 'WX-unknown-green.jpg'
    photo(red, '#b91c1c', 'WX-HD15 RED')
    photo(blue, '#1d4ed8', 'WX-HD15 BLUE')
    photo(silver, '#64748b', 'WX-HD16 SILVER')
    photo(unknown, '#15803d', 'UNKNOWN TEST PHOTO')

    primary = OUT / '微信正向-吹风机.xlsx'
    workbook(primary, '微信验收吹风机', [
        [1, 'WX-HD15', '红色', None, '1600W', '20台/箱', 35, '重复型号的红色款'],
        [2, 'WX-HD15', '蓝色', None, '1600W', '20台/箱', 37, '重复型号的蓝色款'],
        [3, 'WX-HD16', '银色', None, '1800W', '12台/箱', 110, '独立型号'],
        [4, None, '金色', None, '1200W', '24台/箱', 28, '型号故意留空，必须保留'],
    ], [red, blue, silver, unknown])

    concurrent = OUT / '微信并发-配件.xlsx'
    workbook(concurrent, '微信验收配件', [
        [1, 'WX-PJ-01', '黑色', None, '不适用', '100个/箱', 2.5, '并发工单A'],
        [2, 'WX-PJ-02', '白色', None, '不适用', '100个/箱', 2.8, '并发工单B'],
    ], [red, blue])

    (OUT / '反向-损坏Excel.xlsx').write_bytes(b'not an xlsx workbook')
    (OUT / '反向-伪装图片.png').write_bytes(b'not an image')
    manifest = {
        'purpose': '微信档口助手正反向人工验收；所有名称、图片和价格均为测试数据',
        'primary_workbook': str(primary),
        'concurrent_workbook': str(concurrent),
        'known_photo': str(red),
        'unknown_photo': str(unknown),
        'corrupt_workbook': str(OUT / '反向-损坏Excel.xlsx'),
        'disguised_image': str(OUT / '反向-伪装图片.png'),
        'expected_primary_rows': 4,
        'expected_primary_models': ['WX-HD15', 'WX-HD15', 'WX-HD16', ''],
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
