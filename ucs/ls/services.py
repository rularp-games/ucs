"""Игровая логика поверх Django-моделей: создание игры, жетоны, траты карт, отказы.

Правила синхронизации и генераторы задач — в `engine.py` (без базы). Здесь они
применяются к игроку и записываются в базу. Функции возвращают сообщения для
игрока, а решение, как их показать, остаётся за представлениями.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from . import engine
from .loader import get_content
from .models import Flight, Game, Player, Purchase, TaskAttempt, Token

TOKENS_PER_SYMBOL = 20
FLIGHT_SUFFIXES = 'АБВГДЕЖЗ'
MAX_FLIGHTS = len(FLIGHT_SUFFIXES)
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

def callsign_for(base: str, flight_index: int, flights_total: int) -> str:
    """Позывной с суффиксом звена, если звеньев больше одного: «Семь-Три-Б»."""
    if flights_total <= 1:
        return base
    return f'{base}-{FLIGHT_SUFFIXES[flight_index]}'


@transaction.atomic
def create_game(title: str, code: str, content_name: str, flights_count: int,
                rng: random.Random | None = None) -> Game:
    """Игра со звеньями, игроками из шаблона звена и сотней жетонов."""
    rng = rng or random.Random()
    content = get_content(content_name)
    game = Game.objects.create(title=title, code=code, content_name=content_name)
    for index in range(flights_count):
        flight = Flight.objects.create(game=game, name=f'Звено {index + 1}')
        for character in content.characters:
            Player.objects.create(
                game=game,
                flight=flight,
                callsign=callsign_for(character.callsign, index, flights_count),
                symbol=character.symbol,
                is_imp=character.is_imp,
                character_id=character.id,
            )
    codes = engine.generate_codes(rng, TOKENS_PER_SYMBOL * len(content.symbols))
    Token.objects.bulk_create(
        Token(game=game, code=codes[i * TOKENS_PER_SYMBOL + n], symbol=symbol)
        for i, symbol in enumerate(content.symbols)
        for n in range(TOKENS_PER_SYMBOL)
    )
    return game


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

def redeem_codes(player: Player, raw: str, now=None) -> list[Outcome]:
    """Ввод одного или нескольких кодов через пробел или запятую."""
    now = now or timezone.now()
    outcomes: list[Outcome] = []
    codes = [c for c in raw.replace(',', ' ').split() if c]
    if not codes:
        return [Outcome(LEVEL_WARN, 'Код не введён.')]
    for code in codes:
        code = code.upper()
        with transaction.atomic():
            token = Token.objects.select_for_update().filter(game_id=player.game_id, code=code).first()
            if token is None:
                outcomes.append(Outcome(LEVEL_BAD, f'{code}: неизвестный код.'))
                continue
            if token.used_by_id is not None:
                outcomes.append(Outcome(LEVEL_BAD, f'{code}: уже использован.'))
                continue
            token.used_by = player
            token.used_at = now
            token.save(update_fields=['used_by', 'used_at'])
            if token.symbol == player.symbol:
                player.cards += 1
                player.save(update_fields=['cards'])
                outcomes.append(Outcome(LEVEL_OK, f'{code}: совпадение. Получена карта сопротивления.'))
            else:
                outcomes.append(Outcome(LEVEL_WARN, f'{code}: не ваш символ. Жетон сгорел.'))
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
    player.memory_index += 1
    player.save(update_fields=['memory_index'])
    _spend(player, 'self', pack.text)
    return Outcome(LEVEL_OK, 'Получен пакет памяти.', pack.text)


def _buy_flight(player: Player, minutes: float | None) -> Outcome:
    content = get_content(player.game.content_name)
    if not player.dossier_order:
        player.dossier_order = engine.build_dossier_order(random.Random(), [c.id for c in content.characters])
        player.save(update_fields=['dossier_order'])
    received = player.purchases.filter(kind='flight').count()
    if received >= len(player.dossier_order):
        return Outcome(LEVEL_WARN, 'Все карточки досье уже получены. Карта не потрачена.')
    char_id, part = player.dossier_order[received]
    if not lock_open(content.dossier_locks[part], minutes):
        return NOT_YET
    text = content.character(char_id).dossier[part]
    _spend(player, 'flight', text)
    return Outcome(LEVEL_OK, 'Получена карточка досье.', text)


def _buy_program(player: Player, minutes: float | None) -> Outcome:
    program = get_content(player.game.content_name).program
    if player.program_index >= len(program):
        return Outcome(LEVEL_WARN, 'Вся программа уже получена. Карта не потрачена.')
    pack = program[player.program_index]
    if not lock_open(pack.not_before, minutes):
        return NOT_YET
    player.program_index += 1
    player.save(update_fields=['program_index'])
    _spend(player, 'program', pack.text)
    return Outcome(LEVEL_OK, 'Получен пакет о программе.', pack.text)


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
