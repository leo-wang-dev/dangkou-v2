"""图片存储接口：本期 Local 实现；后续切 OSS 专用桶时以同签名实现替换（仅改配置）。"""
import os


class LocalStorage:
    def __init__(self, base_dir):
        self.base = base_dir

    def save(self, category, product_id, filename, data: bytes) -> str:
        rel = os.path.join(category, product_id, filename)
        dest = os.path.join(self.base, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(data)
        return rel

    def abs_path(self, rel) -> str:
        return os.path.join(self.base, rel)

    def read(self, rel) -> bytes:
        return open(self.abs_path(rel), 'rb').read()
