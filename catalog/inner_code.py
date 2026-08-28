"""内部货号：KS- + 8 位去混淆字符（沿 v1 规则，商家内部视角）。"""
import secrets

_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # 无 0/O/1/I


def gen() -> str:
    return 'KS-' + ''.join(secrets.choice(_ALPHABET) for _ in range(8))
