"""Загрузка контента игры из YAML и проверка его целостности.

Модуль автономный: Django не импортируется. Результат — неизменяемые
dataclass'ы, как в `sim/loader.py`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent / 'data'
DEFAULT_CONTENT = 'game'

SYMBOL_KEYS = ('circle', 'square', 'triangle', 'cross', 'star')
MEMORY_TYPES = ('name', 'memory', 'link', 'body', 'death', 'arrival')
DOSSIER_PARTS = ('life', 'death', 'arrival')
PROGRAM_SIZE = 8
PARAM_KEYS = ('nav', 'move', 'parts', 'fire')


class ContentError(Exception):
    """Контент описан некорректно."""


@dataclass(frozen=True)
class MemoryPack:
    """Пакет потока «Память»: фрагмент о самом персонаже."""

    id: str
    type: str
    text: str
    not_before: int | None = None
    requires_param_open: str | None = None


@dataclass(frozen=True)
class Character:
    id: str
    callsign: str
    symbol: str
    is_imp: bool
    memory: tuple[MemoryPack, ...]
    dossier: dict[str, str]


@dataclass(frozen=True)
class ProgramPack:
    id: str
    topic: str
    text: str
    not_before: int


@dataclass(frozen=True)
class Content:
    name: str
    title: str
    symbols: dict[str, str]
    dossier_locks: dict[str, int]
    program: tuple[ProgramPack, ...]
    characters: tuple[Character, ...]

    def character(self, character_id: str) -> Character:
        for character in self.characters:
            if character.id == character_id:
                return character
        raise KeyError(character_id)


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ContentError(message)


def _parse_memory(char_id: str, raw: list) -> tuple[MemoryPack, ...]:
    packs = []
    seen: set[str] = set()
    for item in raw:
        pack = MemoryPack(
            id=str(item['id']),
            type=str(item['type']),
            text=str(item['text']).strip(),
            not_before=item.get('not_before'),
            requires_param_open=item.get('requires_param_open'),
        )
        _require(pack.type in MEMORY_TYPES, f'{char_id}/{pack.id}: неизвестный тип «{pack.type}»')
        _require(pack.id not in seen, f'{char_id}: повтор пакета {pack.id}')
        _require(pack.not_before is None or isinstance(pack.not_before, int),
                 f'{char_id}/{pack.id}: not_before должен быть числом минут или null')
        _require(pack.requires_param_open in (None, *PARAM_KEYS),
                 f'{char_id}/{pack.id}: неизвестный параметр в requires_param_open')
        _require(bool(pack.text), f'{char_id}/{pack.id}: пустой текст')
        seen.add(pack.id)
        packs.append(pack)
    return tuple(packs)


def parse_content(data: dict, name: str = DEFAULT_CONTENT) -> Content:
    symbols = dict(data.get('symbols') or {})
    _require(set(symbols) == set(SYMBOL_KEYS), f'symbols должны быть ровно {", ".join(SYMBOL_KEYS)}')

    locks = {part: int(minutes) for part, minutes in (data.get('dossier_locks') or {}).items()}
    _require(set(locks) == set(DOSSIER_PARTS), 'dossier_locks: нужны life, death, arrival')

    program = tuple(
        ProgramPack(id=str(p['id']), topic=str(p.get('topic', '')), text=str(p['text']).strip(),
                    not_before=int(p['not_before']))
        for p in data.get('program') or []
    )
    _require(len(program) == PROGRAM_SIZE, f'в программе должно быть {PROGRAM_SIZE} пакетов, а не {len(program)}')
    minutes = [p.not_before for p in program]
    _require(minutes == sorted(minutes), 'not_before пакетов программы должны не убывать')

    raw_chars = (data.get('flight_template') or {}).get('characters') or []
    characters = []
    ids: set[str] = set()
    callsigns: set[str] = set()
    for item in raw_chars:
        char_id = str(item['id'])
        _require(char_id not in ids, f'повтор персонажа {char_id}')
        ids.add(char_id)
        callsign = str(item['callsign'])
        _require(callsign not in callsigns, f'повтор позывного {callsign}')
        callsigns.add(callsign)
        symbol = str(item['symbol'])
        _require(symbol in symbols, f'{char_id}: неизвестный символ «{symbol}»')
        dossier = {part: str((item.get('dossier') or {}).get(part, '')).strip() for part in DOSSIER_PARTS}
        for part, text in dossier.items():
            _require(bool(text), f'{char_id}: нет досье «{part}»')
        memory = _parse_memory(char_id, item.get('memory') or [])
        _require(bool(memory), f'{char_id}: нет пакетов памяти')
        characters.append(Character(id=char_id, callsign=callsign, symbol=symbol,
                                    is_imp=bool(item.get('is_imp', False)), memory=memory, dossier=dossier))
    _require(bool(characters), 'в шаблоне звена нет персонажей')
    _require(sum(c.is_imp for c in characters) == 1, 'в звене должен быть ровно один ИМП')

    return Content(
        name=name,
        title=str(data.get('title', name)),
        symbols=symbols,
        dossier_locks=locks,
        program=program,
        characters=tuple(characters),
    )


def load_content(name: str = DEFAULT_CONTENT, data_dir: Path | None = None) -> Content:
    path = (data_dir or DATA_DIR) / f'{name}.yaml'
    if not path.is_file():
        raise ContentError(f'нет файла контента: {path.name}')
    with path.open(encoding='utf-8') as fh:
        return parse_content(yaml.safe_load(fh), name=name)


@functools.lru_cache(maxsize=8)
def get_content(name: str = DEFAULT_CONTENT) -> Content:
    """Контент по имени, с кэшем: он неизменяем."""
    return load_content(name)


def available_contents(data_dir: Path | None = None) -> list[str]:
    return sorted(p.stem for p in (data_dir or DATA_DIR).glob('*.yaml'))
