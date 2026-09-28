import json
from pathlib import Path
import openpyxl
from catalog.storage import LocalStorage
from tests.test_release_gates import env, auth


def test_quote_api_queues_file_in_same_database(env, tmp_path):
    conn, client, bot = env
    client.app.state.storage = LocalStorage(str(tmp_path))
    conn.execute("UPDATE product_dynamic SET data_json=json_set(data_json,'$.ctn','QTY：40 PCS') "
                 "WHERE id='p1'")
    conn.commit()
    response = client.post('/quote', headers=auth(), json={
        'items': [{'category': 'audit_cat', 'product_id': 'p1', 'quantity': 81}],
        'deposit_pct': 20})
    assert response.status_code == 200, response.text
    path = Path(response.json()['path']); assert path.exists()
    ws = openpyxl.load_workbook(path).active
    assert ws['A1'].value == '型号或品名'          # 行1=列头（纯代码生成）
    assert ws['F2'].value == 120                 # 81 个按 40/箱进位为 120
    assert ws['A3'].value == '合计'
    notice = conn.execute("SELECT body FROM cs_outbox WHERE channel='notify_file'").fetchone()
    assert notice and json.loads(notice['body'])['file_path'] == str(path)
