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
ARCHIVE_KINDS = ('История', 'Диалог', 'Серия А', 'Дело')
CODE_LENGTH = 4


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
class ArchiveEntry:
    """Запись архива: открывается четырёхзначным кодом или картой (если есть место в очереди)."""

    code: str
    title: str
    kind: str
    text: str
    not_before: int
    queue: int | None = None


@dataclass(frozen=True)
class FlightDef:
    """Звено из контента: название и его персонажи."""

    name: str
    characters: tuple[Character, ...]


@dataclass(frozen=True)
class Content:
    name: str
    title: str
    symbols: dict[str, str]
    dossier_locks: dict[str, int]
    program: tuple[ProgramPack, ...]
    flights: tuple[FlightDef, ...]
    archive: tuple[ArchiveEntry, ...]

    @property
    def characters(self) -> tuple[Character, ...]:
        """Все персонажи всех звеньев."""
        return tuple(c for flight in self.flights for c in flight.characters)

    def character(self, character_id: str) -> Character:
        for character in self.characters:
            if character.id == character_id:
                return character
        raise KeyError(character_id)

    def flight_characters(self, character_id: str) -> tuple[Character, ...]:
        """Персонажи звена, в котором состоит указанный персонаж (он сам включён)."""
        for flight in self.flights:
            if any(c.id == character_id for c in flight.characters):
                return flight.characters
        raise KeyError(character_id)

    def archive_entry(self, code: str) -> ArchiveEntry | None:
        for entry in self.archive:
            if entry.code == code:
                return entry
        return None

    @property
    def archive_queue(self) -> tuple[ArchiveEntry, ...]:
        """Записи, доступные по карте, в порядке очереди."""
        return tuple(sorted((e for e in self.archive if e.queue is not None), key=lambda e: e.queue))


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


def _parse_character(item: dict, symbols: dict[str, str]) -> Character:
    char_id = str(item['id'])
    symbol = str(item['symbol'])
    _require(symbol in symbols, f'{char_id}: неизвестный символ «{symbol}»')
    dossier = {part: str((item.get('dossier') or {}).get(part, '')).strip() for part in DOSSIER_PARTS}
    for part, text in dossier.items():
        _require(bool(text), f'{char_id}: нет досье «{part}»')
    memory = _parse_memory(char_id, item.get('memory') or [])
    _require(bool(memory), f'{char_id}: нет пакетов памяти')
    return Character(id=char_id, callsign=str(item['callsign']), symbol=symbol,
                     is_imp=bool(item.get('is_imp', False)), memory=memory, dossier=dossier)


def _parse_flights(raw: list, symbols: dict[str, str]) -> tuple[FlightDef, ...]:
    flights = []
    ids: set[str] = set()
    callsigns: set[str] = set()
    for item in raw:
        name = str(item.get('name', '')).strip()
        _require(bool(name), 'у звена нет названия')
        characters = []
        flight_symbols: set[str] = set()
        for raw_char in item.get('characters') or []:
            character = _parse_character(raw_char, symbols)
            _require(character.id not in ids, f'повтор персонажа {character.id}')
            ids.add(character.id)
            _require(character.callsign not in callsigns, f'повтор позывного {character.callsign}')
            callsigns.add(character.callsign)
            _require(character.symbol not in flight_symbols,
                     f'{name}: символ «{character.symbol}» повторяется в звене')
            flight_symbols.add(character.symbol)
            characters.append(character)
        _require(bool(characters), f'{name}: в звене нет персонажей')
        _require(sum(c.is_imp for c in characters) == 1, f'{name}: в звене должен быть ровно один ИМП')
        flights.append(FlightDef(name=name, characters=tuple(characters)))
    _require(bool(flights), 'нет ни одного звена')
    return tuple(flights)


def _parse_archive(raw: list) -> tuple[ArchiveEntry, ...]:
    entries = []
    codes: set[str] = set()
    queues: set[int] = set()
    for item in raw:
        code = str(item['code'])
        _require(len(code) == CODE_LENGTH and code.isdigit(), f'архив: код «{code}» должен быть из {CODE_LENGTH} цифр')
        _require(code not in codes, f'архив: повтор кода {code}')
        codes.add(code)
        title = str(item.get('title', '')).strip()
        text = str(item.get('text', '')).strip()
        _require(bool(title) and bool(text), f'архив {code}: нужны название и текст')
        kind = str(item.get('kind', ''))
        _require(kind in ARCHIVE_KINDS, f'архив {code}: неизвестный вид «{kind}»')
        not_before = item.get('not_before')
        _require(isinstance(not_before, int) and not isinstance(not_before, bool),
                 f'архив {code}: not_before должен быть числом минут')
        queue = item.get('queue')
        _require(queue is None or (isinstance(queue, int) and queue >= 1),
                 f'архив {code}: queue должна быть положительным числом или null')
        if queue is not None:
            _require(queue not in queues, f'архив: повтор места в очереди {queue}')
            queues.add(queue)
        entries.append(ArchiveEntry(code=code, title=title, kind=kind, text=text,
                                    not_before=not_before, queue=queue))
    return tuple(entries)


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

    flights = _parse_flights(data.get('flights') or [], symbols)
    archive = _parse_archive(data.get('archive') or [])

    return Content(
        name=name,
        title=str(data.get('title', name)),
        symbols=symbols,
        dossier_locks=locks,
        program=program,
        flights=flights,
        archive=archive,
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
