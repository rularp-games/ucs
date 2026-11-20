"""Загрузка контента игры из YAML и проверка его целостности.

Модуль автономный: Django не импортируется. Результат — неизменяемые
dataclass'ы, как в `sim/loader.py`.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent / 'data'
DEFAULT_CONTENT = 'game'

SYMBOL_KEYS = ('circle', 'square', 'triangle', 'cross', 'star')
MEMORY_TYPES = ('name', 'memory', 'link', 'body', 'death', 'arrival')
FREE_MEMORY_TYPES = ('name', 'memory')        # пакеты № 1 и № 2: бесплатно, без выбора
MEMORY_THEMES = ('link', 'body', 'death', 'arrival')   # темы, которые игрок выбирает за карту
MEMORY_THEME_TITLES = {
    'link': 'Связь',
    'body': 'Тело',
    'death': 'Смерть',
    'arrival': 'Попадание',
}
DOSSIER_PART_TITLES = {'life': 'Жизнь', 'death': 'Смерть', 'arrival': 'Попадание'}
DOSSIER_PARTS = ('life', 'death', 'arrival')
PROGRAM_SIZE = 8
FLIGHT_SIZE = 5                                 # пять равноправных игроков в звене
ARCHIVE_KINDS = ('История', 'Диалог', 'Серия А', 'Дело')
CODE_LENGTH = 4
GENDERS = ('m', 'f')
GENDER_MARK = re.compile(r'\{([^{}|:]+):([^{}|]*)\|([^{}|]*)\}')


class ContentError(Exception):
    """Контент описан некорректно."""


def render_gender(text: str, genders: dict[str, str]) -> str:
    """Подставить в метки `{Позывной:мужской|женский}` вариант по полу позывного.

    `genders` — полный словарь `{позывной: 'm' | 'f'}`; позывной, которого в нём нет,
    даёт `KeyError`: исходный пол из контента здесь не подставляется, это дело вызывающего.
    """
    return GENDER_MARK.sub(lambda m: m.group(2) if genders[m.group(1)] == 'm' else m.group(3), text)


def check_gender_marks(text: str, callsigns: set[str], where: str) -> None:
    """Все метки рода в тексте корректны и указывают на известные позывные."""
    for m in GENDER_MARK.finditer(text):
        _require(m.group(1) in callsigns, f'{where}: метка ссылается на неизвестный позывной «{m.group(1)}»')
    rest = GENDER_MARK.sub('', text)
    _require('{' not in rest and '}' not in rest, f'{where}: некорректная метка рода или лишняя скобка')


@dataclass(frozen=True)
class MemoryPack:
    """Пакет потока «Память»: фрагмент о самом персонаже."""

    id: str
    type: str
    text: str
    not_before: int | None = None


@dataclass(frozen=True)
class Character:
    id: str
    callsign: str
    symbol: str
    memory: tuple[MemoryPack, ...]
    dossier: dict[str, str]
    gender: str                             # исходный пол роли, 'm' или 'f'
    names: dict[str, str]                   # полное имя по полу: {'m': ..., 'f': ...}

    def memory_pack(self, pack_type: str) -> MemoryPack:
        """Пакет памяти по типу: name, memory, link, body, death или arrival."""
        for pack in self.memory:
            if pack.type == pack_type:
                return pack
        raise KeyError(pack_type)


@dataclass(frozen=True)
class ProgramPack:
    """Пакет программы. Фоновая сводка (`free`) появляется сама, остальные раскрывает вопрос."""
    id: str
    topic: str
    text: str
    not_before: int
    free: bool = False


@dataclass(frozen=True)
class ProgramQuestion:
    """Вопрос о программе: за одну карту раскрывает один или несколько пакетов."""
    id: str
    title: str
    packs: tuple[str, ...]
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
    program_questions: tuple[ProgramQuestion, ...]
    flights: tuple[FlightDef, ...]
    archive: tuple[ArchiveEntry, ...]

    @property
    def free_program(self) -> tuple[ProgramPack, ...]:
        """Фоновые сводки, которые появляются без карты."""
        return tuple(p for p in self.program if p.free)

    def program_pack(self, pack_id: str) -> ProgramPack:
        for pack in self.program:
            if pack.id == pack_id:
                return pack
        raise KeyError(pack_id)

    def program_question(self, question_id: str) -> ProgramQuestion | None:
        for question in self.program_questions:
            if question.id == question_id:
                return question
        return None

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
        )
        _require(pack.type in MEMORY_TYPES, f'{char_id}/{pack.id}: неизвестный тип «{pack.type}»')
        _require(pack.id not in seen, f'{char_id}: повтор пакета {pack.id}')
        _require(pack.not_before is None or isinstance(pack.not_before, int),
                 f'{char_id}/{pack.id}: not_before должен быть числом минут или null')
        _require(bool(pack.text), f'{char_id}/{pack.id}: пустой текст')
        seen.add(pack.id)
        packs.append(pack)
    _require([p.type for p in packs] == list(MEMORY_TYPES),
             f'{char_id}: пакеты памяти должны идти в порядке {", ".join(MEMORY_TYPES)}')
    for pack in packs:
        if pack.type in MEMORY_THEMES:
            _require(pack.not_before is not None, f'{char_id}/{pack.id}: у темы «{pack.type}» нужен замок not_before')
        else:
            _require(pack.not_before is None, f'{char_id}/{pack.id}: бесплатный пакет не имеет замка')
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
    gender = str(item.get('gender', ''))
    _require(gender in GENDERS, f'{char_id}: gender должен быть m или f')
    raw_names = item.get('names') or {}
    names = {g: str(raw_names.get(g, '')).strip() for g in GENDERS}
    _require(all(names.values()), f'{char_id}: names должны содержать имена m и f')
    return Character(id=char_id, callsign=str(item['callsign']), symbol=symbol,
                     memory=memory, dossier=dossier,
                     gender=gender, names=names)


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
        _require(len(characters) == FLIGHT_SIZE, f'{name}: в звене должно быть {FLIGHT_SIZE} персонажей')
        flights.append(FlightDef(name=name, characters=tuple(characters)))
    _require(bool(flights), 'нет ни одного звена')
    return tuple(flights)


def _parse_program_questions(raw: list, program: tuple[ProgramPack, ...]) -> tuple[ProgramQuestion, ...]:
    """Вопросы программы: каждый платный пакет принадлежит ровно одному вопросу, фоновые — ни одному."""
    by_id = {p.id: p for p in program}
    questions = []
    used: list[str] = []
    ids: set[str] = set()
    for item in raw:
        question = ProgramQuestion(id=str(item['id']), title=str(item.get('title', '')).strip(),
                                   packs=tuple(str(x) for x in item.get('packs') or ()),
                                   not_before=int(item['not_before']))
        _require(question.id not in ids, f'программа: повтор вопроса {question.id}')
        ids.add(question.id)
        _require(bool(question.title) and bool(question.packs), f'вопрос {question.id}: нужны заголовок и пакеты')
        for pack_id in question.packs:
            _require(pack_id in by_id, f'вопрос {question.id}: нет пакета {pack_id}')
            _require(not by_id[pack_id].free, f'вопрос {question.id}: пакет {pack_id} фоновый')
        used.extend(question.packs)
        questions.append(question)
    paid = sorted(p.id for p in program if not p.free)
    _require(sorted(used) == paid, 'каждый платный пакет программы должен принадлежать ровно одному вопросу')
    return tuple(questions)


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


def _check_all_marks(flights: tuple[FlightDef, ...], program: tuple[ProgramPack, ...],
                     archive: tuple[ArchiveEntry, ...]) -> None:
    """Метки рода во всех текстах: память, досье, программа, архив."""
    callsigns = {c.callsign for f in flights for c in f.characters}
    for f in flights:
        for c in f.characters:
            for pack in c.memory:
                check_gender_marks(pack.text, callsigns, f'{c.id}/{pack.id}')
            for part, text in c.dossier.items():
                check_gender_marks(text, callsigns, f'{c.id}/досье {part}')
    for p in program:
        check_gender_marks(p.text, callsigns, f'программа {p.id}')
    for e in archive:
        check_gender_marks(e.text, callsigns, f'архив {e.code}')


def parse_content(data: dict, name: str = DEFAULT_CONTENT) -> Content:
    symbols = dict(data.get('symbols') or {})
    _require(set(symbols) == set(SYMBOL_KEYS), f'symbols должны быть ровно {", ".join(SYMBOL_KEYS)}')

    locks = {part: int(minutes) for part, minutes in (data.get('dossier_locks') or {}).items()}
    _require(set(locks) == set(DOSSIER_PARTS), 'dossier_locks: нужны life, death, arrival')

    program = tuple(
        ProgramPack(id=str(p['id']), topic=str(p.get('topic', '')), text=str(p['text']).strip(),
                    not_before=int(p['not_before']), free=bool(p.get('free', False)))
        for p in data.get('program') or []
    )
    _require(len(program) == PROGRAM_SIZE, f'в программе должно быть {PROGRAM_SIZE} пакетов, а не {len(program)}')
    _require(len({p.id for p in program}) == len(program), 'повтор id пакета программы')
    questions = _parse_program_questions(data.get('program_questions') or [], program)

    flights = _parse_flights(data.get('flights') or [], symbols)
    archive = _parse_archive(data.get('archive') or [])
    _check_all_marks(flights, program, archive)

    return Content(
        name=name,
        title=str(data.get('title', name)),
        symbols=symbols,
        dossier_locks=locks,
        program=program,
        program_questions=questions,
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
