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
from .loader import get_content, render_gender
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
                is_imp=character.is_imp,
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

def record_attempt(player: Player, param: str, success: bool) -> Player:
    """Записать попытку; успех даёт +25, провал ничего не меняет."""
    level = player.get_sync(param)
    TaskAttempt.objects.create(player=player, param=param, level=level, success=success)
    if success:
        player.set_sync(param, engine.after_success(level))
        player.save(update_fields=[f'sync_{param}'])
    return player


def apply_deviation(player: Player, param: str) -> bool:
    """Намеренное отклонение: −25 по параметру и +1 карта.

    Если снижать уже нечего (sync равен нулю), карта не выдаётся: иначе отказ
    на нулевом параметре был бы бесконечным источником карт.
    Возвращает True, если карта выдана.
    """
    level = player.get_sync(param)
    TaskAttempt.objects.create(player=player, param=param, level=level, success=False, deviation=True)
    if level <= 0:
        return False
    player.set_sync(param, engine.after_deviation(level))
    player.cards += 1
    player.save(update_fields=[f'sync_{param}', 'cards'])
    return True


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


def _spend(player: Player, kind: str, payload: str) -> None:
    player.cards -= 1
    player.save(update_fields=['cards'])
    Purchase.objects.create(player=player, kind=kind, payload=payload)


def _buy_self(player: Player, minutes: float | None) -> Outcome:
    packs = get_content(player.game.content_name).character(player.character_id).memory
    if player.memory_index >= len(packs):
        return Outcome(LEVEL_WARN, 'Всё о себе уже получено. Карта не потрачена.')
    pack = packs[player.memory_index]
    if not lock_open(pack.not_before, minutes):
        return NOT_YET
    if pack.requires_param_open and not engine.is_open(player.sync_values(), pack.requires_param_open):
        return NOT_YET
    text = render_gender(pack.text, game_genders(player.game))
    player.memory_index += 1
    player.save(update_fields=['memory_index'])
    _spend(player, 'self', text)
    return Outcome(LEVEL_OK, 'Получен пакет памяти.', text)


def _buy_flight(player: Player, minutes: float | None) -> Outcome:
    content = get_content(player.game.content_name)
    if not player.dossier_order:
        player.dossier_order = engine.build_dossier_order(
            random.Random(), [c.id for c in content.flight_characters(player.character_id)])
        player.save(update_fields=['dossier_order'])
    received = player.purchases.filter(kind='flight').count()
    if received >= len(player.dossier_order):
        return Outcome(LEVEL_WARN, 'Все карточки досье уже получены. Карта не потрачена.')
    char_id, part = player.dossier_order[received]
    if not lock_open(content.dossier_locks[part], minutes):
        return NOT_YET
    text = render_gender(content.character(char_id).dossier[part], game_genders(player.game))
    _spend(player, 'flight', text)
    return Outcome(LEVEL_OK, 'Получена карточка досье.', text)


def _buy_program(player: Player, minutes: float | None) -> Outcome:
    program = get_content(player.game.content_name).program
    if player.program_index >= len(program):
        return Outcome(LEVEL_WARN, 'Вся программа уже получена. Карта не потрачена.')
    pack = program[player.program_index]
    if not lock_open(pack.not_before, minutes):
        return NOT_YET
    text = render_gender(pack.text, game_genders(player.game))
    player.program_index += 1
    player.save(update_fields=['program_index'])
    _spend(player, 'program', text)
    return Outcome(LEVEL_OK, 'Получен пакет о программе.', text)


def _buy_archive(player: Player, minutes: float | None) -> Outcome:
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


def _buy_radio(player: Player, minutes: float | None) -> Outcome:
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


def buy(player: Player, kind: str, now=None) -> Outcome:
    """Потратить одну карту. Если пакет закрыт или кончился, карта остаётся."""
    if kind not in BUYERS:
        return Outcome(LEVEL_BAD, 'Неизвестная трата.')
    with transaction.atomic():
        player = Player.objects.select_for_update().select_related('game').get(pk=player.pk)
        if player.cards < 1:
            return NO_CARDS
        return BUYERS[kind](player, player.game.game_minutes(now))
