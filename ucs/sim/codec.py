"""Упаковка состояния группы в строку.

Симулятор работает без базы данных, поэтому состояние ездит в скрытом поле формы.
Это же делает ссылку самодостаточной: мастер может сохранить строку и вернуться к
той же точке прогона.

Подпись здесь не нужна: подделать состояние может только тот, кто и так сидит за
мастерским экраном. Единственное, от чего защищаемся, — от битой строки.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import json

from .engine import GroupState


class StateCodec(Exception):
    """Строка состояния не читается."""


def encode(state: GroupState) -> str:
    raw = json.dumps(state.to_dict(), ensure_ascii=False, separators=(',', ':'))
    packed = gzip.compress(raw.encode('utf-8'), compresslevel=9, mtime=0)
    return base64.urlsafe_b64encode(packed).decode('ascii')


def decode(token: str) -> GroupState:
    try:
        packed = base64.urlsafe_b64decode(token.encode('ascii'))
        raw = gzip.decompress(packed).decode('utf-8')
        return GroupState.from_dict(json.loads(raw))
    except (
        binascii.Error,
        gzip.BadGzipFile,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise StateCodec(f'состояние прогона не читается: {error}') from error
