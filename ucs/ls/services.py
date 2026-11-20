"""Игровая логика поверх Django-моделей: создание игры, жетоны, коды, траты карт, отказы.

Правила синхронизации и генераторы задач — в `engine.py` (без базы). Здесь они
применяются к игроку и записываются в базу. Функции возвращают сообщения для
игрока, а решение, как их показать, остаётся за представлениями.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import engine
from .loader import (DOSSIER_PART_TITLES, FREE_MEMORY_TYPES, MEMORY_THEME_TITLES, MEMORY_THEMES,
                     get_content, render_gender)
from .models import (TOKEN_TTL, ArchiveOpen, DarkEntry, Flight, Game, Player, Purchase,
                     TaskAttempt, Token)

TOKENS_PER_ENTRY = 2
WRONG_CODES_LIMIT = 5                      # неверных кодов подряд до блокировки поля
CODES_BLOCK = timedelta(minutes=2)
RADIO_MAX = 2

LEVEL_OK = 'ok'
LEVEL_WARN = 'warn'
LEVEL_BAD = 'bad'


@dataclass(frozen=True)
class Outcome:
    """Итог одного действия игрока: уровень важности и текст для экрана."""

    level: str
    message: str
    payload: str = ''

    @property
    def ok(self) -> bool:
        return self.level == LEVEL_OK


# --- Создание игры ----------------------------------------------------------

@transaction.atomic
def create_game(title: str, code: str, content_name: str, flights_count: int) -> Game:
    """Игра со звеньями и игроками из контента: первые `flights_count` звеньев."""
    content = get_content(content_name)
    if not 1 <= flights_count <= len(content.flights):
        raise ValueError(f'звеньев должно быть от 1 до {len(content.flights)}')
    game = Game.objects.create(title=title, code=code, content_name=content_name)
    for flight_def in content.flights[:flights_count]:
        flight = Flight.objects.create(game=game, name=flight_def.name)
        for character in flight_def.characters:
            Player.objects.create(
                game=game,
                flight=flight,
                callsign=character.callsign,
                symbol=character.symbol,
                character_id=character.id,
                gender=character.gender,
            )
    return game


def game_genders(game: Game) -> dict[str, str]:
    """Полный словарь `{позывной: пол}` для подстановки меток рода.

    Основа — исходный пол из контента (в игре могут быть не все звенья, а записи архива
    ссылаются на персонажей любого), поверх него — пол, выставленный игрокам игры.
    """
    genders = {c.callsign: c.gender for c in get_content(game.content_name).characters}
    genders.update(game.players.values_list('callsign', 'gender'))
    return genders


# --- Синхронизация ----------------------------------------------------------

def stage_of(game: Game, now=None) -> int:
    """Этап игры по игровому времени (до старта — первый)."""
    return engine.stage(game.game_minutes(now))


def record_attempt(player: Player, param: str, success: bool, stage: int = 1,
                   summary: dict | None = None, now=None) -> bool:
    """Записать попытку; успех даёт +25 в пределах потолка этапа, провал ничего не меняет.

    Возвращает True, если успех упёрся в потолок этапа: «Предел калибровки на этом этапе».
    """
    level = player.get_sync(param)
    TaskAttempt.objects.create(player=player, param=param, level=level, success=success, stage=stage,
                               summary=summary or {}, created_at=now or timezone.now())
    if not success:
        return False
    new = engine.after_success(level, engine.stage_cap(stage))
    if new == level:
        return level < engine.SYNC_MAX
    player.set_sync(param, new)
    player.save(update_fields=[f'sync_{param}'])
    return False


def pause_seconds(player: Player, param: str, now=None) -> int:
    """Пауза перекалибровки: 30 с после провала по этому параметру, иначе 0.

    Считается по журналу попыток, поэтому её не обойти ни другим браузером, ни повторным входом.
    """
    now = now or timezone.now()
    last = player.attempts.filter(param=param, deviation=False).first()
    if last is None or last.success:
        return 0
    return engine.pause_left(last.created_at.timestamp(), now.timestamp())


def apply_deviation(player: Player, param: str, stage: int = 1, summary: dict | None = None, now=None) -> bool:
    """Намеренное отклонение: −25 по параметру и, может быть, +1 карта.

    Карта не выдаётся, если снижать уже нечего (sync равен нулю) и если по этому же
    параметру карта за отказ уже была меньше 20 минут назад: иначе отказ стал бы фермой
    карт. Синхронизация снижается в любом случае. Возвращает True, если карта выдана.
    """
    now = now or timezone.now()
    level = player.get_sync(param)
    last_card = player.attempts.filter(param=param, deviation=True, card=True).first()
    card = level > 0 and engine.deviation_card_allowed(
        last_card.created_at.timestamp() if last_card else None, now.timestamp())
    TaskAttempt.objects.create(player=player, param=param, level=level, success=False, deviation=True,
                               card=card, stage=stage, summary=summary or {}, created_at=now)
    if level <= 0:
        return False
    player.set_sync(param, engine.after_deviation(level))
    fields = [f'sync_{param}']
    if card:
        player.cards += 1
        fields.append('cards')
    player.save(update_fields=fields)
    return card


def debrief(game: Game) -> dict[int, dict]:
    """Короткая сводка тренировок для дебрифа по каждому игроку: поражения гражданских
    силуэтов, отказы на задачах с гражданским силуэтом, отмеченные и пропущенные фантомы,
    отказы и карты за них."""
    rows = {pk: {'civilian_hits': 0, 'civilian_refused': 0, 'phantom_marked': 0, 'phantom_missed': 0,
                 'refusals': 0, 'cards': 0}
            for pk in game.players.values_list('pk', flat=True)}
    for attempt in TaskAttempt.objects.filter(player__game=game):
        row = rows[attempt.player_id]
        summary = attempt.summary or {}
        if attempt.deviation:
            row['refusals'] += 1
            row['cards'] += attempt.card
            if attempt.param == 'fire' and summary.get('civilian'):
                row['civilian_refused'] += 1
            continue
        if attempt.param == 'fire' and attempt.success and summary.get('fired_civilian'):
            row['civilian_hits'] += 1
        if attempt.param == 'parts' and 'phantom_marked' in summary:
            row['phantom_marked' if summary['phantom_marked'] else 'phantom_missed'] += 1
    return rows


# --- Жетоны -----------------------------------------------------------------

@transaction.atomic
def issue_tokens(game: Game, players: list[Player], rng: random.Random | None = None, now=None) -> DarkEntry:
    """Группа уходит в темноту: каждому выдаются два жетона со случайными символами.

    Код жетона — четыре цифры, уникален в игре и не совпадает с кодом архива.
    """
    rng = rng or random.Random()
    now = now or timezone.now()
    content = get_content(game.content_name)
    entry = DarkEntry.objects.create(game=game, created_at=now)
    entry.players.set(players)
    taken = set(game.tokens.values_list('code', flat=True)) | {e.code for e in content.archive}
    codes = engine.generate_codes(rng, TOKENS_PER_ENTRY * len(players), taken)
    symbols = list(content.symbols)
    Token.objects.bulk_create(
        Token(game=game, code=codes[i * TOKENS_PER_ENTRY + n], symbol=rng.choice(symbols),
              issued_to=player, issued_at=now, dark_entry=entry)
        for i, player in enumerate(players)
        for n in range(TOKENS_PER_ENTRY)
    )
    return entry


def active_tokens(player: Player, now=None) -> list[Token]:
    """Жетоны, выданные игроку, которые ещё никто не ввёл и срок которых не вышел."""
    now = now or timezone.now()
    return list(player.tokens_issued.filter(used_by=None, burned=False, issued_at__gt=now - TOKEN_TTL)
                .order_by('issued_at', 'id'))


def block_seconds(player: Player, now=None) -> int:
    """Сколько секунд поле ввода ещё закрыто (0, если открыто)."""
    now = now or timezone.now()
    if player.codes_blocked_until is None or player.codes_blocked_until <= now:
        return 0
    return int((player.codes_blocked_until - now).total_seconds()) + 1


def wait_text(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    return f'{minutes} мин {rest} с' if minutes else f'{rest} с'


def _right_code(player: Player) -> None:
    """Верный код: счётчик неверных обнуляется (вызывается под блокировкой игрока)."""
    if player.wrong_codes:
        player.wrong_codes = 0
        player.save(update_fields=['wrong_codes'])


def _wrong_code(player: Player, code: str, now) -> Outcome:
    player.wrong_codes += 1
    message = f'{code}: неверный код.'
    if player.wrong_codes >= WRONG_CODES_LIMIT:
        player.wrong_codes = 0
        player.codes_blocked_until = now + CODES_BLOCK
        message += f' Поле закрыто на {wait_text(int(CODES_BLOCK.total_seconds()))}.'
    player.save(update_fields=['wrong_codes', 'codes_blocked_until'])
    return Outcome(LEVEL_BAD, message)


def _entry_payload(entry, genders: dict[str, str]) -> str:
    return f'{entry.code} · {entry.title}\n\n{render_gender(entry.text, genders)}'


def _redeem_token(player: Player, token: Token, now) -> Outcome:
    code = token.code
    if token.issued_to_id == player.pk:
        return Outcome(LEVEL_WARN, f'{code}: это ваш жетон. Его надо отдать.')
    if not token.is_valid(now):
        return Outcome(LEVEL_BAD, f'{code}: уже недействителен.')
    token.used_by = player
    token.used_at = now
    _right_code(player)
    if token.symbol == player.symbol:
        token.save(update_fields=['used_by', 'used_at'])
        player.cards += 1
        player.save(update_fields=['cards'])
        return Outcome(LEVEL_OK, f'{code}: совпадение. Получена карта сопротивления.')
    token.burned = True
    token.save(update_fields=['used_by', 'used_at', 'burned'])
    return Outcome(LEVEL_WARN, f'{code}: не ваш символ. Жетон сгорел.')


def _redeem_archive(player: Player, entry, now) -> Outcome:
    code = entry.code
    if player.archive_opens.filter(code=code).exists():
        _right_code(player)
        return Outcome(LEVEL_OK, f'{code}: запись уже открыта.', _entry_payload(entry, game_genders(player.game)))
    if not lock_open(entry.not_before, player.game.game_minutes(now)):
        return Outcome(LEVEL_WARN, f'{code}: запись недоступна. Повторите позже.')
    ArchiveOpen.objects.create(player=player, code=code, via='code', created_at=now)
    _right_code(player)
    return Outcome(LEVEL_OK, f'{code}: запись открыта.', _entry_payload(entry, game_genders(player.game)))


def _redeem_one(player: Player, code: str, now) -> Outcome:
    token = Token.objects.select_for_update().filter(game_id=player.game_id, code=code).first()
    if token is not None:
        return _redeem_token(player, token, now)
    entry = get_content(player.game.content_name).archive_entry(code)
    if entry is not None:
        return _redeem_archive(player, entry, now)
    return _wrong_code(player, code, now)


def redeem_codes(player: Player, raw: str, now=None) -> list[Outcome]:
    """Ввод одного или нескольких кодов через пробел или запятую.

    Код проверяется атомарно, под блокировкой игрока и жетона. Пять неверных кодов
    подряд закрывают поле на две минуты; остаток списка после этого не разбирается.
    """
    now = now or timezone.now()
    codes = engine.parse_codes(raw)
    if not codes:
        return [Outcome(LEVEL_WARN, 'Код не введён.')]
    outcomes: list[Outcome] = []
    for code in codes:
        with transaction.atomic():
            fresh = Player.objects.select_for_update().select_related('game').get(pk=player.pk)
            wait = block_seconds(fresh, now)
            if wait:
                outcomes.append(Outcome(LEVEL_BAD, f'Поле закрыто. Подождите {wait_text(wait)}.'))
                break
            outcomes.append(_redeem_one(fresh, code, now))
    player.refresh_from_db()
    return outcomes


# --- Траты карт -------------------------------------------------------------

def lock_open(not_before: int | None, minutes: float | None) -> bool:
    """Замок по времени. Без замка пакет открыт всегда; до старта закрыты все замки."""
    if not_before is None:
        return True
    return minutes is not None and minutes >= not_before


NOT_YET = Outcome(LEVEL_WARN, 'Пока нет. Карта не потрачена.')
NO_CARDS = Outcome(LEVEL_BAD, 'Нет карт сопротивления.')


def _spend(player: Player, kind: str, payload: str, key: str = '') -> None:
    player.cards -= 1
    player.save(update_fields=['cards'])
    Purchase.objects.create(player=player, kind=kind, key=key, payload=payload)


@dataclass(frozen=True)
class Option:
    """Вариант выбора за карту: тема памяти, действие с досье или вопрос о программе.

    `state`: `open` (можно купить), `locked` (закрыт по времени, виден неактивным) или
    `done` (уже получено). Подпись `note` не пересказывает закрытый факт.
    """

    key: str
    title: str
    state: str
    note: str = ''

    @property
    def available(self) -> bool:
        return self.state == 'open'


NOTE_LOCKED = 'пока недоступно'
NOTE_DONE = 'получено'


def _option(key: str, title: str, not_before: int | None, minutes: float | None, done: bool = False,
            done_note: str = NOTE_DONE) -> Option:
    if done:
        return Option(key, title, 'done', done_note)
    if not lock_open(not_before, minutes):
        return Option(key, title, 'locked', NOTE_LOCKED)
    return Option(key, title, 'open')


def _bought(player: Player, kind: str) -> set[str]:
    return set(player.purchases.filter(kind=kind).exclude(key='').values_list('key', flat=True))


def _rng(rng: random.Random | None) -> random.Random:
    return rng or random.Random()


# --- Память: «О себе» --------------------------------------------------------

def free_memory(player: Player) -> list[dict]:
    """Бесплатные пакеты памяти: № 1 сразу, № 2 после старта игры. Карта не нужна."""
    character = get_content(player.game.content_name).character(player.character_id)
    genders = game_genders(player.game)
    packs = [character.memory_pack(t) for t in FREE_MEMORY_TYPES]
    if not player.game.is_started:
        packs = packs[:1]
    return [{'title': 'Имя' if pack.type == 'name' else 'Воспоминание',
             'text': render_gender(pack.text, genders)} for pack in packs]


def memory_options(player: Player, minutes: float | None) -> list[Option]:
    """Четыре темы памяти: каждая один раз, независимо от остальных."""
    character = get_content(player.game.content_name).character(player.character_id)
    bought = _bought(player, 'self')
    return [_option(theme, MEMORY_THEME_TITLES[theme], character.memory_pack(theme).not_before,
                    minutes, done=theme in bought)
            for theme in MEMORY_THEMES]


def _buy_self(player: Player, minutes: float | None, choice: str, rng: random.Random) -> Outcome:
    if choice not in MEMORY_THEMES:
        return Outcome(LEVEL_WARN, 'Выберите тему. Карта не потрачена.')
    if choice in _bought(player, 'self'):
        return Outcome(LEVEL_WARN, 'Эта тема уже получена. Карта не потрачена.')
    pack = get_content(player.game.content_name).character(player.character_id).memory_pack(choice)
    if not lock_open(pack.not_before, minutes):
        return NOT_YET
    text = render_gender(pack.text, game_genders(player.game))
    _spend(player, 'self', text, key=choice)
    return Outcome(LEVEL_OK, f'Получена тема «{MEMORY_THEME_TITLES[choice]}».', text)


# --- Досье: «О звене» --------------------------------------------------------

def dossier_cases_of(player: Player) -> list[dict]:
    """Дела игрока по купленным карточкам досье; метки постоянные, по порядку открытия."""
    opened = [tuple(key.split(':', 1)) for key in
              player.purchases.filter(kind='flight').exclude(key='').values_list('key', flat=True)]
    return engine.dossier_cases(opened)


def flight_options(player: Player, minutes: float | None) -> list[Option]:
    """Новое дело и продолжение каждого открытого дела."""
    content = get_content(player.game.content_name)
    cases = dossier_cases_of(player)
    all_opened = len(cases) >= len(content.flight_characters(player.character_id))
    options = [_option('new', 'Новое дело', content.dossier_locks['life'], minutes,
                       done=all_opened, done_note='все дела открыты')]
    for case in cases:
        part = engine.next_dossier_part(case['parts'])
        title = f'{case["label"]}: продолжить'
        if part is None:
            options.append(Option(f'case:{case["index"]}', title, 'done', 'дело закрыто'))
        else:
            options.append(_option(f'case:{case["index"]}', title, content.dossier_locks[part], minutes))
    return options


def _buy_flight(player: Player, minutes: float | None, choice: str, rng: random.Random) -> Outcome:
    content = get_content(player.game.content_name)
    cases = dossier_cases_of(player)
    if choice == 'new':
        character_id = engine.pick_new_case(
            rng, [c.id for c in content.flight_characters(player.character_id)], cases)
        if character_id is None:
            return Outcome(LEVEL_WARN, 'Все дела уже открыты. Карта не потрачена.')
        part = 'life'
        label = engine.case_label(len(cases))
    elif choice.startswith('case:') and choice[5:].isdigit() and int(choice[5:]) < len(cases):
        case = cases[int(choice[5:])]
        character_id, label = case['character_id'], case['label']
        part = engine.next_dossier_part(case['parts'])
        if part is None:
            return Outcome(LEVEL_WARN, 'Дело уже закрыто. Карта не потрачена.')
    else:
        return Outcome(LEVEL_WARN, 'Выберите дело. Карта не потрачена.')
    if not lock_open(content.dossier_locks[part], minutes):
        return NOT_YET
    text = render_gender(content.character(character_id).dossier[part], game_genders(player.game))
    _spend(player, 'flight', text, key=f'{character_id}:{part}')
    return Outcome(LEVEL_OK, f'Получена карточка досье: {label}.', f'{label} · {DOSSIER_PART_TITLES[part]}\n\n{text}')


# --- Программа: «О программе» ------------------------------------------------

def background_summaries(player: Player, now=None) -> list[dict]:
    """Фоновые сводки, чей замок наступил: появляются сами, без карты и без объявления."""
    minutes = player.game.game_minutes(now)
    genders = game_genders(player.game)
    return [{'topic': p.topic, 'text': render_gender(p.text, genders)}
            for p in get_content(player.game.content_name).free_program
            if lock_open(p.not_before, minutes)]


def program_options(player: Player, minutes: float | None) -> list[Option]:
    """Три вопроса о программе: каждый один раз, независимо от остальных."""
    bought = _bought(player, 'program')
    return [_option(q.id, q.title, q.not_before, minutes, done=q.id in bought)
            for q in get_content(player.game.content_name).program_questions]


def _buy_program(player: Player, minutes: float | None, choice: str, rng: random.Random) -> Outcome:
    content = get_content(player.game.content_name)
    question = content.program_question(choice)
    if question is None:
        return Outcome(LEVEL_WARN, 'Выберите вопрос. Карта не потрачена.')
    if choice in _bought(player, 'program'):
        return Outcome(LEVEL_WARN, 'Этот вопрос уже задан. Карта не потрачена.')
    if not lock_open(question.not_before, minutes):
        return NOT_YET
    genders = game_genders(player.game)
    text = '\n\n'.join(render_gender(content.program_pack(pid).text, genders) for pid in question.packs)
    _spend(player, 'program', text, key=choice)
    return Outcome(LEVEL_OK, f'Ответ на вопрос «{question.title}».', text)


# --- Архив, рация ------------------------------------------------------------

def _buy_archive(player: Player, minutes: float | None, choice: str, rng: random.Random) -> Outcome:
    queue = get_content(player.game.content_name).archive_queue
    opened = set(player.archive_opens.values_list('code', flat=True))
    entry = next((e for e in queue if e.code not in opened), None)
    if entry is None:
        return Outcome(LEVEL_WARN, 'Весь архив уже получен. Карта не потрачена.')
    if not lock_open(entry.not_before, minutes):
        return NOT_YET
    ArchiveOpen.objects.create(player=player, code=entry.code, via='card')
    _spend(player, 'archive', f'{entry.code} · {entry.title}')
    return Outcome(LEVEL_OK, 'Получена запись архива.', _entry_payload(entry, game_genders(player.game)))


def _buy_radio(player: Player, minutes: float | None, choice: str, rng: random.Random) -> Outcome:
    if player.radio_level >= RADIO_MAX:
        return Outcome(LEVEL_WARN, 'Рация уже работает и на посту. Карта не потрачена.')
    player.radio_level += 1
    player.save(update_fields=['radio_level'])
    text = ('Рация получена. Открыт «Эфир».' if player.radio_level == 1
            else 'Рация работает и на посту.')
    _spend(player, 'radio', text)
    return Outcome(LEVEL_OK, text, text)


BUYERS = {
    'self': _buy_self,
    'flight': _buy_flight,
    'program': _buy_program,
    'archive': _buy_archive,
    'radio': _buy_radio,
}


def buy(player: Player, kind: str, choice: str = '', now=None, rng: random.Random | None = None) -> Outcome:
    """Потратить одну карту. Если выбранное закрыто по времени, уже получено или кончилось, карта остаётся.

    `choice` — выбор игрока: тема памяти, `new` или `case:<номер>` для досье, id вопроса
    программы; для архива и рации он не нужен. `rng` нужен тестам для случайного дела.
    """
    if kind not in BUYERS:
        return Outcome(LEVEL_BAD, 'Неизвестная трата.')
    with transaction.atomic():
        player = Player.objects.select_for_update().select_related('game').get(pk=player.pk)
        if player.cards < 1:
            return NO_CARDS
        return BUYERS[kind](player, player.game.game_minutes(now), choice, _rng(rng))
