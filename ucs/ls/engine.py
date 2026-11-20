"""Правила игры в виде чистых функций: без Django, без базы данных.

Здесь лежат правила синхронизации, этапы игры, генераторы задач для пяти моделей
тренировок и их проверка. Задача — обычный словарь из чисел, строк и списков,
поэтому её можно положить в JSON-поле игрока как есть. Ответ игрока проверяется
функциями `check_*` по сохранённой задаче: ни в какие HTML и JS проверка не заглядывает.
Генераторы детерминированы: одно зерно, уровень и этап дают одну и ту же задачу.
"""

from __future__ import annotations

import copy
import math
import random
import re
import string
import zlib

# --- Синхронизация ----------------------------------------------------------

PARAMS = ('nav', 'move', 'parts', 'sense', 'fire')
PARAM_TITLES = {
    'nav': 'Навигация',
    'move': 'Движение',
    'parts': 'Части',
    'sense': 'Восприятие',
    'fire': 'Огонь',
}
MODEL_TITLES = {
    'nav': 'Маяки',
    'move': 'Строй',
    'parts': 'Панель',
    'sense': 'Давление',
    'fire': 'Опознание',
}

SYNC_STEP = 25
SYNC_MAX = 100
OPEN_THRESHOLD = 50  # восприятие открывает огонь при этом значении
OPEN_FROM_START = ('nav', 'move', 'parts')  # мирные параметры открыты сразу
SENSE_OPENS_AT = 55  # восприятие открывается для всех с этой игровой минуты

MODE_CLOSED = 'closed'
MODE_INSTRUCTION = 'instruction'  # sync < 50: полная модель с инструкцией
MODE_FULL = 'full'                # 50..74: полная модель без инструкции
MODE_SHORT = 'short'              # 75: сокращённая модель
MODE_AUTO = 'auto'                # 100: автоматика


def is_open(sync: dict[str, int], param: str, minutes: float | None = None) -> bool:
    """Открыт ли параметр.

    Навигация, движение и части открыты с начала. Восприятие открывается для всех с
    55-й минуты. Огонь открывается, когда восприятие дошло до 50: «чтобы научиться
    стрелять, нужно сначала научиться чувствовать». Параметр с ненулевой синхронизацией
    открыт всегда, поэтому отказ по восприятию не закрывает уже начатый огонь.
    """
    if param in OPEN_FROM_START or sync.get(param, 0) > 0:
        return True
    if param == 'sense':
        return minutes is not None and minutes >= SENSE_OPENS_AT
    return sync.get('sense', 0) >= OPEN_THRESHOLD


def mode_of(sync: dict[str, int], param: str, minutes: float | None = None) -> str:
    """Режим модели по текущему состоянию игрока и игровому времени."""
    if not is_open(sync, param, minutes):
        return MODE_CLOSED
    return level_mode(sync.get(param, 0))


def level_mode(value: int) -> str:
    """Режим открытой модели по уровню синхронизации."""
    if value >= SYNC_MAX:
        return MODE_AUTO
    if value >= 75:
        return MODE_SHORT
    if value >= OPEN_THRESHOLD:
        return MODE_FULL
    return MODE_INSTRUCTION


def after_success(value: int, cap: int = SYNC_MAX) -> int:
    """Успешная задача: +25, но не выше потолка этапа и не выше 100.

    Если значение уже на потолке или выше (мастер мог поднять вручную), оно не меняется:
    потолок не отнимает синхронизацию, только не даёт расти.
    """
    return max(value, min(cap, SYNC_MAX, value + SYNC_STEP))


def after_deviation(value: int) -> int:
    """Намеренное отклонение: −25, не ниже 0."""
    return max(0, value - SYNC_STEP)


def tier(level: int) -> int:
    """Ступень формы навыка по синхронизации: 0 (0%), 1 (25%), 2 (50%), 3 (75% и выше)."""
    return max(0, min(level, 75)) // SYNC_STEP


# --- Этапы игры, потолок, паузы ---------------------------------------------
#
# Этап зависит только от игрового времени и общий для всего зала. Игроку он не
# показывается, только мастеру. До старта игры — первый этап.

STAGE_FAULTS_FROM = 55   # «Сбои»
STAGE_READY_FROM = 85    # «Готовность»
STAGE_TITLES = {1: 'Привыкание', 2: 'Сбои', 3: 'Готовность'}
STAGE_CAPS = {1: 50, 2: 75, 3: 100}
FAIL_PAUSE_SECONDS = 30                 # пауза перекалибровки после провала
DEVIATION_CARD_COOLDOWN = 20 * 60       # карта за отказ по параметру — не чаще, секунды
HALL_WINDOW_MINUTES = 15                # окно общего для зала сбоя тела


def stage(minutes: float | None) -> int:
    """Этап по игровым минутам: 1 «Привыкание» (<55), 2 «Сбои» (55–85), 3 «Готовность» (≥85)."""
    if minutes is None or minutes < STAGE_FAULTS_FROM:
        return 1
    if minutes < STAGE_READY_FROM:
        return 2
    return 3


def stage_cap(stage_no: int) -> int:
    """Потолок синхронизации на этапе: 50, 75, 100."""
    return STAGE_CAPS[stage_no]


def pause_left(failed_at: float | None, now: float) -> int:
    """Сколько целых секунд ещё длится пауза после провала в момент `failed_at` (0 — паузы нет)."""
    if failed_at is None:
        return 0
    left = failed_at + FAIL_PAUSE_SECONDS - now
    return max(0, math.ceil(left))


def deviation_card_allowed(last_card_at: float | None, now: float) -> bool:
    """Карту за отказ можно выдать, если по этому параметру карты за отказ не было 20 минут."""
    return last_card_at is None or now - last_card_at >= DEVIATION_CARD_COOLDOWN


def hall_seed(game_key: int | str, minutes: float | None, salt: str) -> int:
    """Зерно, общее для всего зала в пятнадцатиминутном окне игрового времени.

    Зависит только от игры, номера окна и `salt` (какой именно сбой): все, кто в этот
    момент на посту, получают один и тот же сбой тела.
    """
    window = math.floor(minutes / HALL_WINDOW_MINUTES) if minutes is not None else -1
    return zlib.crc32(f'{game_key}:{window}:{salt}'.encode())


# --- Модель 1. Навигация, «Маяки» -------------------------------------------
#
# Поле книжное, 8 × 11 клеток. На 0% маяки заданы клетками (звезда-маяк подсвечена),
# на 25% выход задан смещением от маяка, на 50% маяки даны карточками созвездий
# («звезда-маяк и три ближайших соседа»), на 75% — одна карточка, её клетка и есть выход.
# «Сбои»: один маяк «молчит» и задан серединой между двумя другими; карточки повёрнуты.
# «Готовность»: выход заменяется точкой встречи с движущейся учебной целью.

NAV_COLS = 8
NAV_ROWS = 11
NAV_STARS = {0: 46, 1: 46, 2: 70, 3: 70}
NAV_NEIGHBOURS = 3
NAV_TOLERANCE = 0.3          # допуск совпадения рисунка созвездия, клетки
NAV_ROTATIONS = (90, 180, 270)


def cell_label(col: int, row: int) -> str:
    """Клетка (столбец, строка), с нуля, в виде «A3»: буква — столбец, цифра — строка."""
    return f'{chr(ord("A") + col)}{row + 1}'


def _inside(col: int, row: int) -> bool:
    return 0 <= col < NAV_COLS and 0 <= row < NAV_ROWS


def offset_text(dx: int, dy: int) -> str:
    """Смещение словами: «2 вправо, 3 вниз»."""
    parts = []
    if dx:
        parts.append(f'{abs(dx)} {"вправо" if dx > 0 else "влево"}')
    if dy:
        parts.append(f'{abs(dy)} {"вниз" if dy > 0 else "вверх"}')
    return ', '.join(parts)


def _rotate(dx: float, dy: float, degrees: int) -> tuple[float, float]:
    """Поворот смещения по часовой стрелке на экране (ось y вниз) на 0/90/180/270°."""
    for _ in range((degrees // 90) % 4):
        dx, dy = -dy, dx
    return dx, dy


def _neighbours(stars: list[tuple[float, float]], index: int) -> list[tuple[float, float]]:
    """Смещения трёх ближайших соседей звезды."""
    x, y = stars[index]
    others = sorted((math.hypot(sx - x, sy - y), sx - x, sy - y)
                    for n, (sx, sy) in enumerate(stars) if n != index)
    return [(dx, dy) for _, dx, dy in others[:NAV_NEIGHBOURS]]


def _pattern_unique(stars: list[tuple[float, float]], index: int, pattern: list[tuple[float, float]]) -> bool:
    """Ни у какой другой звезды нет соседей в тех же относительных позициях (при любом повороте)."""
    for n, (x, y) in enumerate(stars):
        if n == index:
            continue
        for degrees in (0, 90, 180, 270):
            if all(any(math.hypot(sx - (x + px), sy - (y + py)) <= NAV_TOLERANCE for sx, sy in stars)
                   for px, py in (_rotate(dx, dy, degrees) for dx, dy in pattern)):
                return False
    return True


def _star_in_cell(rng: random.Random, col: int, row: int) -> tuple[float, float]:
    return (round(col + rng.uniform(0.25, 0.75), 2), round(row + rng.uniform(0.25, 0.75), 2))


def _beacon_cells(rng: random.Random, silent: bool) -> list[tuple[int, int]]:
    """Три клетки маяков. Если маяк 2 «молчит», маяки 1 и 3 одной чётности, а 2 — их середина."""
    while True:
        if not silent:
            cells = rng.sample([(c, r) for c in range(NAV_COLS) for r in range(NAV_ROWS)], 3)
            return cells
        c1, r1 = rng.randrange(NAV_COLS), rng.randrange(NAV_ROWS)
        c3, r3 = rng.randrange(c1 % 2, NAV_COLS, 2), rng.randrange(r1 % 2, NAV_ROWS, 2)
        if abs(c3 - c1) + abs(r3 - r1) >= 4:
            return [(c1, r1), ((c1 + c3) // 2, (r1 + r3) // 2), (c3, r3)]


def _offset_exit(rng: random.Random, base: tuple[int, int], taken: list[tuple[int, int]]):
    """Выход со сдвигом 1–3 по каждой оси от клетки `base`, внутри поля и не на маяке."""
    while True:
        dx = rng.choice((-3, -2, -1, 1, 2, 3))
        dy = rng.choice((-3, -2, -1, 1, 2, 3))
        cell = (base[0] + dx, base[1] + dy)
        if _inside(*cell) and cell not in taken:
            return cell, dx, dy


def _intercept(rng: random.Random, taken: list[tuple[int, int]]) -> tuple[tuple[int, int], dict]:
    """Точка встречи: метка учебной цели, её сдвиг за такт и число тактов N = 3–5."""
    while True:
        tx, ty = rng.randrange(NAV_COLS), rng.randrange(NAV_ROWS)
        dx, dy = rng.randint(-2, 2), rng.randint(-2, 2)
        n = rng.randint(3, 5)
        cell = (tx + n * dx, ty + n * dy)
        if (dx or dy) and _inside(*cell) and cell not in taken and (tx, ty) not in taken:
            return cell, {'x': tx, 'y': ty, 'dx': dx, 'dy': dy, 'n': n}


def gen_nav(seed: int, level: int, stage_no: int = 1) -> dict:
    """Задача «Маяки». Ответ — клетки маяков по порядку, затем выход (см. `nav_expected`)."""
    rng = random.Random(seed)
    t = tier(level)
    short = t == 3
    silent = t <= 1 and stage_no >= 2
    # Звёзды-маяки входят в общее число звёзд поля.
    background = NAV_STARS[t] - ((2 if silent else 3) if t <= 1 else 0)
    while True:
        stars = [(round(rng.uniform(0.1, NAV_COLS - 0.1), 2), round(rng.uniform(0.1, NAV_ROWS - 0.1), 2))
                 for _ in range(background)]
        beacons: list[tuple[int, int]] = []
        beacon_text: list[str] = []
        highlight: list[list[float]] = []
        cards: list[dict] = []
        if t <= 1:
            beacons = _beacon_cells(rng, silent)
            for n, (c, r) in enumerate(beacons):
                if silent and n == 1:
                    beacon_text.append('посередине между маяком 1 и маяком 3')
                    continue
                star = _star_in_cell(rng, c, r)
                stars.append(star)
                if t == 0:
                    highlight.append(list(star))
                beacon_text.append(cell_label(c, r))
        else:
            # Карточки созвездий: звезда-маяк ясно внутри своей клетки, рисунок однозначен.
            wanted = 1 if short else 2
            if short and stage_no >= 3:
                wanted = 0
            candidates = [n for n, (x, y) in enumerate(stars) if 0.2 <= x % 1 <= 0.8 and 0.2 <= y % 1 <= 0.8]
            rng.shuffle(candidates)
            for n in candidates:
                if len(cards) == wanted:
                    break
                cell = (int(stars[n][0]), int(stars[n][1]))
                pattern = _neighbours(stars, n)
                if cell in beacons or max(math.hypot(dx, dy) for dx, dy in pattern) > 2.5:
                    continue
                if not _pattern_unique(stars, n, pattern):
                    continue
                rot = rng.choice(NAV_ROTATIONS) if stage_no >= 2 else 0
                beacons.append(cell)
                cards.append({'rot': rot, 'points': [[round(a, 2), round(b, 2)]
                                                     for a, b in (_rotate(dx, dy, rot) for dx, dy in pattern)]})
            if len(cards) < wanted:
                continue

        target = None
        exit_text = ''
        if stage_no >= 3:
            exit_cell, target = _intercept(rng, beacons)
            exit_text = f'Выйти в точку встречи с учебной целью через {target["n"]} тактов.'
        elif short:
            exit_cell = beacons[0]
            exit_text = 'Клетка найденного маяка и есть точка выхода.'
        elif t == 0:
            exit_cell = rng.choice([(c, r) for c in range(NAV_COLS) for r in range(NAV_ROWS)
                                    if (c, r) not in beacons])
            exit_text = f'Точка выхода: {cell_label(*exit_cell)}.'
        else:
            base = len(beacons)
            exit_cell, dx, dy = _offset_exit(rng, beacons[-1], beacons)
            exit_text = f'Точка выхода: от маяка {base} — {offset_text(dx, dy)}.'
        break

    clicks = [] if short else [cell_label(c, r) for c, r in beacons]
    return {
        'kind': 'nav',
        'seed': seed,
        'short': short,
        'beacons': clicks,
        'beacon_text': beacon_text,
        'exit': cell_label(*exit_cell),
        'exit_text': exit_text,
        'stars': [list(s) for s in stars],
        'highlight': highlight,
        'cards': cards,
        'target': target,
    }


def nav_expected(task: dict) -> list[str]:
    """Верная последовательность кликов: маяки по порядку, затем выход."""
    return list(task['beacons']) + [task['exit']]


def parse_nav_answer(raw: str) -> list[str]:
    return [part.strip().upper() for part in (raw or '').split(',') if part.strip()]


def check_nav(task: dict, answer: list[str] | str) -> bool:
    if isinstance(answer, str):
        answer = parse_nav_answer(answer)
    return list(answer) == nav_expected(task)


# --- Модель 2. Движение, «Строй» --------------------------------------------
#
# Физика шарика на планке детерминирована: фиксированный шаг 1/30 с, дрейф от
# генератора mulberry32 из зерна задачи, толчки и сбой тяги заранее записаны в
# задаче. Та же физика повторена в `templates/ls/_move_physics.js`; операции и их
# порядок совпадают строка в строку, поэтому клиент и сервер получают одинаковые
# числа. Клиент присылает запись нажатий по шагам, сервер прогоняет её сам и решает,
# удержан ли шарик. Положение шарика x — от −1 до 1.

MOVE_FPS = 30
MOVE_TOLERANCE = 1.5          # допуск на расхождение часов клиента и сервера, секунды
MOVE_PHYSICS = {
    'gravity': 1.2,           # ускорение от наклона планки
    'drift_step': 3.0,        # случайное блуждание дрейфа
    'tilt_rate': 2.2,         # скорость наклона от кнопки
    'tilt_return': 0.8,       # возврат планки без кнопки
    'damp': 1.0,              # затухание скорости
    'push': 0.3,              # толчок: мгновенная добавка к скорости
}
# Ступень синхронизации: окно, дрейф, толчки, движение окна, сколько держать.
MOVE_TABLE = {
    0: {'half': 0.25, 'drift_max': 0.3, 'pushes': False, 'warn': False, 'moving': False, 'need': 20},
    1: {'half': 0.22, 'drift_max': 0.4, 'pushes': True, 'warn': True, 'moving': False, 'need': 25},
    2: {'half': 0.22, 'drift_max': 0.45, 'pushes': True, 'warn': False, 'moving': True, 'need': 30},
    3: {'half': 0.22, 'drift_max': 0.45, 'pushes': True, 'warn': False, 'moving': True, 'need': 15},
}
MOVE_AMPLITUDE = 0.3              # размах движения окна («держать дистанцию до ведущего»)
MOVE_PERIOD = (10, 14)            # период движения окна, секунды
MOVE_PUSH_GAP = {1: (8, 10), 2: (6, 10), 3: (6, 10)}   # между толчками, секунды
MOVE_GLITCH_SECONDS = 2.5         # «Сбои»: длительность сбоя тяги
MOVE_GLITCH_DELAY = 9             # запаздывание управления, шаги (0,3 с)
MOVE_GLITCH_TYPES = ('invert', 'delay')
MOVE_GLITCH_TITLES = {'invert': 'инверсия', 'delay': 'запаздывание'}
MOVE_NARROW = 10                  # «Готовность»: последние секунды плотного строя
MOVE_NARROW_HALF = {2: 0.15, 3: 0.17}
MOVE_RECORD = re.compile(r'([LRN])(\d{1,5})')
MASK32 = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    return (a * b) & MASK32


class Mulberry32:
    """Генератор псевдослучайных чисел mulberry32, как в JS: вызов даёт число в [0, 1)."""

    def __init__(self, seed: int):
        self.state = seed & MASK32

    def __call__(self) -> float:
        self.state = (self.state + 0x6D2B79F5) & MASK32
        a = self.state
        t = _imul(a ^ (a >> 15), a | 1)
        t = ((t + _imul(t ^ (t >> 7), t | 61)) & MASK32) ^ t
        return ((t ^ (t >> 14)) & MASK32) / 4294967296


def gen_move(seed: int, level: int, stage_no: int = 1, now: float = 0.0, hall: int = 0) -> dict:
    """Задача «Строй»: окно, дрейф, толчки и сбой тяги по ступени и этапу.

    Всё случайное, кроме дрейфа, записано в задаче (шаги толчков и сбоя), дрейф
    берётся из mulberry32(seed) по шагам. `hall` — зерно зала: тип сбоя тяги общий
    для всех в пятнадцатиминутном окне.
    """
    rng = random.Random(seed)
    t = tier(level)
    row = MOVE_TABLE[t]
    need = row['need']
    glitch = None
    if stage_no >= 2:
        start = rng.uniform(8, max(8, need - 5))
        glitch = {'start': int(start * MOVE_FPS), 'steps': int(MOVE_GLITCH_SECONDS * MOVE_FPS),
                  'type': MOVE_GLITCH_TYPES[hall % len(MOVE_GLITCH_TYPES)]}
    pushes = []
    if row['pushes']:
        lo, hi = MOVE_PUSH_GAP[t]
        at = rng.uniform(3, 5)
        while at < need - 1:
            step = int(at * MOVE_FPS)
            # Во время сбоя тяги и секунду после него толчков нет: два события сразу не выдержать.
            if not glitch or not glitch['start'] - MOVE_FPS <= step <= glitch['start'] + glitch['steps'] + MOVE_FPS:
                pushes.append([step, rng.choice((-1, 1))])
            at += rng.uniform(lo, hi)
    return {
        'kind': 'move',
        'seed': seed,
        'short': t == 3,
        'seconds': need,
        'issued_at': now,
        'physics': {
            **MOVE_PHYSICS,
            'fps': MOVE_FPS,
            'seed': seed & MASK32,
            'steps': need * MOVE_FPS,
            'half': row['half'],
            'drift_max': row['drift_max'],
            'drift0': round(rng.choice((-1, 1)) * rng.uniform(0.08, 0.2), 3),
            'amplitude': MOVE_AMPLITUDE if row['moving'] else 0.0,
            'period': rng.randint(*MOVE_PERIOD) if row['moving'] else 1,
            'pushes': pushes,
            'warn': MOVE_FPS if row['warn'] else 0,
            'glitch': glitch,
            'narrow': MOVE_NARROW_HALF[3 if t == 3 else 2] if stage_no >= 3 else 0.0,
            'narrow_seconds': MOVE_NARROW,
        },
    }


def move_window(p: dict, n: int) -> tuple[float, float]:
    """Центр и полуширина окна после шага `n` (с нуля)."""
    t = (n + 1) / p['fps']
    center = 0.0
    if p['amplitude']:
        phase = t / p['period']
        phase = phase - math.floor(phase)
        if phase < 0.5:
            q = phase * 2
            center = p['amplitude'] * (4 * q * (1 - q))
        else:
            q = (phase - 0.5) * 2
            center = p['amplitude'] * (-4 * q * (1 - q))
    half = p['half']
    if p['narrow']:
        start = p['steps'] / p['fps'] - p['narrow_seconds']
        if t > start:
            half = p['half'] + (p['narrow'] - p['half']) * ((t - start) / p['narrow_seconds'])
    return center, half


class MoveSim:
    """Пошаговая физика «Строя». Шаг `step(u)` принимает ввод игрока (−1, 0, 1) и
    возвращает True, если шарик после шага в окне. Порядок операций повторён в JS."""

    def __init__(self, p: dict):
        self.p = p
        self.rand = Mulberry32(p['seed'])
        self.dt = 1 / p['fps']
        self.pushes = {step: direction for step, direction in p['pushes']}
        self.history: list[int] = []
        self.n = 0
        self.x = 0.0
        self.v = 0.0
        self.angle = 0.0
        self.drift = p['drift0']

    def copy(self) -> 'MoveSim':
        """Независимая копия состояния (нужна для проверки, что задача проходима)."""
        twin = copy.copy(self)
        twin.rand = copy.copy(self.rand)
        twin.history = list(self.history)
        return twin

    def step(self, u: int) -> bool:
        p, dt, n = self.p, self.dt, self.n
        self.history.append(u)
        drift = self.drift + (self.rand() - 0.5) * p['drift_step'] * dt
        if drift > p['drift_max']:
            drift = p['drift_max']
        if drift < -p['drift_max']:
            drift = -p['drift_max']
        self.drift = drift
        glitch = p['glitch']
        if glitch and glitch['start'] <= n < glitch['start'] + glitch['steps']:
            if glitch['type'] == 'invert':
                u = -u
            else:
                u = self.history[n - MOVE_GLITCH_DELAY] if n >= MOVE_GLITCH_DELAY else 0
        angle = self.angle
        if u != 0:
            angle = angle + u * p['tilt_rate'] * dt
        else:
            angle = angle - angle * p['tilt_return'] * dt
        if angle > 1:
            angle = 1.0
        if angle < -1:
            angle = -1.0
        self.angle = angle
        v = self.v
        if n in self.pushes:
            v = v + self.pushes[n] * p['push']
        v = v + (angle * p['gravity'] + drift) * dt
        v = v - v * p['damp'] * dt
        self.v = v
        self.x = self.x + v * dt
        center, half = move_window(p, n)
        self.n = n + 1
        return abs(self.x - center) <= half


def simulate_move(p: dict, inputs: list[int]) -> dict:
    """Прогон физики по записи ввода (−1 влево, 0, 1 вправо на каждом шаге).

    Останавливается, когда шарик вышел из окна или когда записан весь срок.
    Возвращает {'ok', 'steps', 'x'}: удержан ли весь срок, сколько шагов прожито,
    последнее положение (для сверки с JS).
    """
    sim = MoveSim(p)
    steps = min(len(inputs), p['steps'])
    for n in range(steps):
        if not sim.step(inputs[n]):
            return {'ok': False, 'steps': n + 1, 'x': sim.x}
    return {'ok': steps >= p['steps'], 'steps': steps, 'x': sim.x}


def parse_move_record(raw: str, limit: int) -> list[int] | None:
    """Запись нажатий «L12N30R5» (буква и число повторов) в список −1/0/1; None, если запись битая."""
    raw = (raw or '').strip()
    pos = 0
    inputs: list[int] = []
    for match in MOVE_RECORD.finditer(raw):
        if match.start() != pos:
            return None
        pos = match.end()
        inputs.extend([{'L': -1, 'N': 0, 'R': 1}[match.group(1)]] * int(match.group(2)))
        if len(inputs) > limit:
            return None
    if pos != len(raw):
        return None
    return inputs


def encode_move_record(inputs: list[int]) -> str:
    """Обратное к `parse_move_record`: сжатие повторов (нужно тестам)."""
    out = []
    for value in inputs:
        letter = {-1: 'L', 0: 'N', 1: 'R'}[value]
        if out and out[-1][0] == letter:
            out[-1][1] += 1
        else:
            out.append([letter, 1])
    return ''.join(f'{letter}{count}' for letter, count in out)


def check_move(task: dict, record: str, now: float) -> bool:
    """Сервер сам прогоняет физику по записи нажатий: шарик весь срок в окне, записан
    весь срок, и с выдачи задачи прошло не меньше этого срока (минус допуск на часы)."""
    p = task['physics']
    inputs = parse_move_record(record, p['steps'] + MOVE_FPS)
    if inputs is None or len(inputs) < p['steps']:
        return False
    if now - task['issued_at'] < task['seconds'] - MOVE_TOLERANCE:
        return False
    return simulate_move(p, inputs)['ok']


# --- Модель 3. Части, «Панель» ----------------------------------------------
#
# Панель растёт вместе с телом: 6 тумблеров на 0%, 8 на 25%, 10 с 50%. На «Готовности»
# подвеска делится на две подвески и фиксатор. Пункт чек-листа — прямой («створка 1:
# открыть»), функциональный («Стабилизация», справочник функций виден только в режиме
# инструкции) или условный («если радиатор выпущен — створка 2: закрыть, иначе открыть»).
# Фантом — пункт про тумблер, которого на панели нет; его надо отметить «Нет на панели».

PARTS_PANELS = {
    0: ('створка 1', 'створка 2', 'створка 3', 'ориентация 1', 'ориентация 2', 'радиатор'),
    1: ('створка 1', 'створка 2', 'створка 3', 'ориентация 1', 'ориентация 2', 'ориентация 3',
        'радиатор', 'подвеска'),
    2: ('створка 1', 'створка 2', 'створка 3', 'ориентация 1', 'ориентация 2', 'ориентация 3',
        'радиатор', 'подвеска', 'шасси', 'двигатель'),
}
SUSPENSION_SPLIT = ('подвеска 1', 'подвеска 2', 'фиксатор подвесок')
# Тумблеры, которых нет ни на одной панели: общий для зала фантом выбирается из них.
PHANTOM_SWITCHES = ('створка 4', 'ориентация 4', 'двигатель 2', 'радиатор 2', 'створка 5')
# Слова тумблера: (действие «вкл», действие «выкл», состояние «вкл», состояние «выкл»).
SWITCH_WORDS = {
    'створка': ('открыть', 'закрыть', 'открыта', 'закрыта'),
    'ориентация': ('включить', 'выключить', 'включена', 'выключена'),
    'радиатор': ('выпустить', 'убрать', 'выпущен', 'убран'),
    'подвеска': ('выпустить', 'убрать', 'выпущена', 'убрана'),
    'шасси': ('выпустить', 'убрать', 'выпущено', 'убрано'),
    'двигатель': ('включить', 'выключить', 'включён', 'выключен'),
    'фиксатор подвесок': ('поставить', 'снять', 'стоит', 'снят'),
}
PARTS_FUNCTIONS = (
    ('Закрыть левый борт', (('створка 1', False), ('створка 2', False))),
    ('Сброс тепла', (('радиатор', True),)),
    ('Стабилизация', (('ориентация 1', True), ('ориентация 2', True))),
    ('Разворот', (('ориентация 3', True), ('ориентация 1', False))),
    ('Походное положение', (('шасси', False), ('подвеска', False))),
)
# Ступень: (пунктов, функциональных, условных).
PARTS_LAYOUT = {0: (3, 0, 0), 1: (4, 1, 0), 2: (5, 2, 1), 3: (3, 1, 0)}
PHANTOM_CHANCE = {1: 0.0, 2: 0.5, 3: 0.7}   # по этапу, начиная с 25%
FLIP_DELAY = (6, 12)                        # «Готовность»: самопроизвольное переключение, секунды


def _kind(name: str) -> str:
    """Вид тумблера по имени: «створка 2» → «створка»."""
    head, _, tail = name.rpartition(' ')
    return head if tail.isdigit() else name


def switch_words(name: str) -> tuple[str, str, str, str]:
    return SWITCH_WORDS[_kind(name)]


def _verb_for(name: str, value: bool) -> str:
    words = switch_words(name)
    return words[0] if value else words[1]


def _state_word(name: str, value: bool) -> str:
    words = switch_words(name)
    return words[2] if value else words[3]


def parts_panel(level: int, stage_no: int = 1) -> tuple[str, ...]:
    """Тумблеры на панели по ступени синхронизации; на «Готовности» подвеска делится на три."""
    panel = PARTS_PANELS[min(tier(level), 2)]
    if stage_no >= 3 and 'подвеска' in panel:
        index = panel.index('подвеска')
        panel = panel[:index] + SUSPENSION_SPLIT + panel[index + 1:]
    return panel


def function_text(name: str) -> str:
    """Строка справочника функций: «Стабилизация — ориентация 1: включить, ориентация 2: включить»."""
    sets = dict(PARTS_FUNCTIONS)[name]
    return f'{name} — ' + ', '.join(f'{switch}: {_verb_for(switch, value)}' for switch, value in sets)


def gen_parts(seed: int, level: int, stage_no: int = 1, hall: int = 0) -> dict:
    """Задача «Панель»: начальное положение тумблеров и чек-лист.

    Пункт чек-листа — словарь {label, sets, phantom}: `sets` — список пар
    [номер тумблера, нужное положение]. У фантома `sets` пуст. Наборы тумблеров
    разных пунктов не пересекаются. `hall` — зерно зала: имя фантома общее для всех
    в пятнадцатиминутном окне.
    """
    rng = random.Random(seed)
    t = tier(level)
    short = t == 3
    switches = list(parts_panel(level, stage_no))
    index = {name: i for i, name in enumerate(switches)}
    initial = [rng.random() < 0.5 for _ in switches]
    count, functional, conditional = PARTS_LAYOUT[t]

    phantom = t >= 1 and rng.random() < PHANTOM_CHANCE[stage_no]
    used: set[int] = set()
    items: list[dict] = []

    funcs = [(name, sets) for name, sets in PARTS_FUNCTIONS if all(s in index for s, _ in sets)]
    rng.shuffle(funcs)
    for name, sets in funcs:
        if sum(1 for i in items if i['type'] == 'function') >= functional:
            break
        ids = [index[s] for s, _ in sets]
        if used & set(ids):
            continue
        used.update(ids)
        items.append({'type': 'function', 'label': name,
                      'sets': [[index[s], value] for s, value in sets], 'phantom': False})

    for _ in range(conditional):
        free = [i for i in range(len(switches)) if i not in used]
        cond, target = rng.sample(free, 2)
        used.update((cond, target))
        cond_value = rng.random() < 0.5
        if_true = rng.random() < 0.5
        expected = if_true if initial[cond] == cond_value else not if_true
        cname, tname = switches[cond], switches[target]
        items.append({
            'type': 'condition',
            'label': (f'если {cname} {_state_word(cname, cond_value)} — {tname}: '
                      f'{_verb_for(tname, if_true)}, иначе {_verb_for(tname, not if_true)}'),
            'sets': [[target, expected]], 'phantom': False, 'cond': cond,
        })

    direct = count - len(items) - (1 if phantom else 0)
    if stage_no >= 3 and 'фиксатор подвесок' in index and direct > 0:
        # «Готовность»: в чек-листе появляются подвески и фиксатор.
        name, value = rng.choice((('фиксатор подвесок', False), ('подвеска 1', True), ('подвеска 2', True)))
        if index[name] not in used:
            used.add(index[name])
            items.append({'type': 'direct', 'label': '', 'sets': [[index[name], value]], 'phantom': False})
            direct -= 1
    free = [i for i in range(len(switches)) if i not in used]
    for i in rng.sample(free, direct):
        used.add(i)
        items.append({'type': 'direct', 'label': '', 'sets': [[i, None]], 'phantom': False})
    rng.shuffle(items)

    # Начальные положения: каждый реальный пункт требует действия; в сокращённом
    # режиме половина реальных пунктов уже выполнена.
    already = len(items) // 2 if short else 0
    for n, item in enumerate(items):
        done = n < already
        for pair in item['sets']:
            switch, value = pair
            if item['type'] == 'condition':
                if done:
                    initial[switch] = value
                elif initial[switch] == value:
                    # Условный пункт должен требовать действия: меняем условие, а не цель.
                    cond = item['cond']
                    initial[cond] = not initial[cond]
                    pair[1] = not value
                continue
            if value is None:
                value = initial[switch] if done else not initial[switch]
                pair[1] = value
            else:
                initial[switch] = value if done else not value
        if item['type'] == 'direct':
            switch, value = item['sets'][0]
            item['label'] = f'{switches[switch]}: {_verb_for(switches[switch], value)}'

    if phantom:
        name = PHANTOM_SWITCHES[hall % len(PHANTOM_SWITCHES)]
        value = rng.random() < 0.5
        items.insert(rng.randrange(len(items) + 1), {
            'type': 'phantom', 'label': f'{name}: {_verb_for(name, value)}', 'sets': [], 'phantom': True,
        })

    flip = None
    if stage_no >= 3:
        free = [i for i in range(len(switches)) if i not in used]
        if free:
            flip = {'switch': rng.choice(free), 'delay': rng.randint(*FLIP_DELAY)}
    return {
        'kind': 'parts',
        'seed': seed,
        'short': short,
        'switches': switches,
        'initial': initial,
        'items': items,
        'phantom': phantom,
        'marks': stage_no >= 2 and t >= 1,
        'reference': [function_text(name) for name, _ in PARTS_FUNCTIONS
                      if all(s in index for s, _ in dict(PARTS_FUNCTIONS)[name])] if t <= 1 else [],
        'flip': flip,
    }


def parts_target(task: dict) -> list[bool]:
    """Положения, которые должны быть у тумблеров после выполнения чек-листа."""
    state = list(task['initial'])
    for item in task['items']:
        for switch, value in item['sets']:
            state[switch] = value
    return state


def phantom_items(task: dict) -> set[int]:
    return {n for n, item in enumerate(task['items']) if item['phantom']}


def check_parts(task: dict, state: list[bool], marked: set[int] | None = None) -> bool:
    """Все пункты выполнены, остальные тумблеры стоят как в начале, и отмечены «Нет на
    панели» ровно фантомные пункты (реальный пункт отмечать нельзя)."""
    if len(state) != len(task['switches']):
        return False
    if [bool(v) for v in state] != parts_target(task):
        return False
    return set(marked or ()) == phantom_items(task)


def parse_parts_answer(raw: str, size: int) -> list[bool] | None:
    """Положения тумблеров — строка из `size` символов 0/1."""
    raw = (raw or '').strip()
    if len(raw) != size or set(raw) - {'0', '1'}:
        return None
    return [ch == '1' for ch in raw]


def parse_marked(raw: str) -> set[int]:
    """Номера пунктов, отмеченных «Нет на панели»: «0,3»."""
    return {int(part) for part in (raw or '').replace(' ', ',').split(',') if part.strip().isdigit()}


# --- Модель 4. Восприятие, «Давление» ---------------------------------------
#
# «Учебное наблюдение». На поле 8 × 11 скрыта масса. Звезда на расстоянии d от неё
# видна на расстоянии d' = (d + √(d² + 4R²)) / 2 — упрощённое линзирование: вокруг массы
# пустота радиусом R. По этой формуле звёзды у края пустоты не гуще, а реже, поэтому
# кольцо делается яркостью, как при настоящем линзировании: звезда с усилением
# μ = 1 / (1 − (R/d')⁴) рисуется крупнее. Игрок нажимает точку, где масса.
# Положение массы есть только в задаче на сервере, в HTML — лишь смещённые звёзды.

SENSE_TABLE = {
    0: {'radius': 1.2, 'false': 0, 'glow': 1.0, 'flash': 0},
    1: {'radius': 1.0, 'false': 0, 'glow': 0.45, 'flash': 0},
    2: {'radius': 0.8, 'false': 1, 'glow': 0.0, 'flash': 0},
    3: {'radius': 0.8, 'false': 1, 'glow': 0.0, 'flash': 4},
}
SENSE_STARS = (120, 160)
SENSE_HIT = 0.7               # успех: нажатие не дальше этого от центра массы, клетки
SENSE_EDGE = 1.5              # масса не ближе этого к краю поля
SENSE_APART = 4.0             # две массы и ложная пустота не ближе этого друг к другу
SENSE_FRAME_MS = 1200         # «Готовность»: два кадра движущейся массы
SENSE_AHEAD = 2               # нажать, где масса будет через столько тактов после второго кадра


SENSE_GLOW_MAX = 2.4          # предел увеличения звезды на кольце


def _lens(stars: list[tuple[float, float, float]], mass: tuple[float, float],
          radius: float) -> list[tuple[float, float, float]]:
    """Сдвинуть звёзды от массы: d' = (d + √(d² + 4R²)) / 2; третье число — во сколько раз
    крупнее рисовать звезду (μ^0,75, не больше SENSE_GLOW_MAX)."""
    mx, my = mass
    out = []
    for x, y, size in stars:
        dx, dy = x - mx, y - my
        d = math.hypot(dx, dy)
        if d < 1e-9:
            dx, dy, d = 1e-3, 0.0, 1e-3
        seen = (d + math.sqrt(d * d + 4 * radius * radius)) / 2
        gain = 1 / max(1e-6, 1 - (radius / seen) ** 4)
        out.append((mx + dx * seen / d, my + dy * seen / d, min(SENSE_GLOW_MAX, size * gain ** 0.75)))
    return out


def _on_field(points: list[tuple[float, float, float]]) -> list[list[float]]:
    return [[round(x, 2), round(y, 2), round(size, 2)] for x, y, size in points
            if 0 <= x <= NAV_COLS and 0 <= y <= NAV_ROWS]


def _mass_point(rng: random.Random, avoid: list[tuple[float, float]]) -> tuple[float, float]:
    """Точка не ближе SENSE_APART к уже выбранным; если места нет, требование понемногу слабеет."""
    attempt = 0
    while True:
        point = (round(rng.uniform(SENSE_EDGE, NAV_COLS - SENSE_EDGE), 2),
                 round(rng.uniform(SENSE_EDGE, NAV_ROWS - SENSE_EDGE), 2))
        apart = SENSE_APART * max(0.5, 1 - attempt / 400)
        if all(math.hypot(point[0] - x, point[1] - y) >= apart for x, y in avoid):
            return point
        attempt += 1


def _glow_side(point: tuple[float, float]) -> str:
    """С какой стороны экрана светится край: по преобладающему смещению от центра поля."""
    dx, dy = point[0] - NAV_COLS / 2, (point[1] - NAV_ROWS / 2) * NAV_COLS / NAV_ROWS
    if abs(dx) >= abs(dy):
        return 'left' if dx < 0 else 'right'
    return 'top' if dy < 0 else 'bottom'


def place_text(point: tuple[float, float]) -> str:
    """Положение словами: «Справа внизу», «Слева», «В центре»."""
    h = 'Слева' if point[0] < NAV_COLS / 3 else 'Справа' if point[0] > NAV_COLS * 2 / 3 else ''
    v = 'вверху' if point[1] < NAV_ROWS / 3 else 'внизу' if point[1] > NAV_ROWS * 2 / 3 else ''
    if h and v:
        return f'{h} {v}'
    return h or v.capitalize() or 'В центре'


def gen_sense(seed: int, level: int, stage_no: int = 1) -> dict:
    """Задача «Давление».

    Ступень: радиус пустоты 1,2 → 1,0 → 0,8; подсказка-свечение края на 0–25%; ложная
    пустота без кольца с 50%; на 75% поле гаснет через 4 с. «Сбои» (0–50%): две массы.
    «Готовность»: масса движется, поле показано двумя кадрами, нажать, где она будет
    через два такта. `targets` — точки, которые нужно нажать.
    """
    rng = random.Random(seed)
    t = tier(level)
    row = SENSE_TABLE[t]
    radius = row['radius']
    moving = stage_no >= 3
    count = 2 if stage_no == 2 and t <= 2 else 1
    true_stars = [(rng.uniform(0, NAV_COLS), rng.uniform(0, NAV_ROWS), 1.0)
                  for _ in range(rng.randint(*SENSE_STARS))]

    masses: list[tuple[float, float]] = []
    motion = None
    if moving:
        while True:
            start = _mass_point(rng, [])
            vx = rng.choice((-1, 1)) * round(rng.uniform(0.4, 0.8), 2)
            vy = rng.choice((-1, 1)) * round(rng.uniform(0.4, 0.8), 2)
            second = (start[0] + vx, start[1] + vy)
            target = (round(second[0] + SENSE_AHEAD * vx, 2), round(second[1] + SENSE_AHEAD * vy, 2))
            if all(SENSE_EDGE - 0.5 <= p[0] <= NAV_COLS - SENSE_EDGE + 0.5 and
                   SENSE_EDGE - 0.5 <= p[1] <= NAV_ROWS - SENSE_EDGE + 0.5 for p in (second, target)):
                break
        masses = [start]
        motion = {'second': [round(second[0], 2), round(second[1], 2)], 'velocity': [vx, vy]}
        targets = [target]
    else:
        for _ in range(count):
            masses.append(_mass_point(rng, masses))
        targets = list(masses)

    holes = []
    for _ in range(row['false']):
        holes.append(_mass_point(rng, masses + [tuple(m) for m in ([motion['second']] if motion else [])] + targets))

    def frame(centres: list[tuple[float, float]]) -> list[list[float]]:
        stars = list(true_stars)
        for centre in centres:
            stars = _lens(stars, centre, radius)
        for hx, hy in holes:
            stars = [(x, y, k) for x, y, k in stars if math.hypot(x - hx, y - hy) > radius * 1.05]
        return _on_field(stars)

    frames = [frame(masses)]
    if motion:
        frames.append(frame([tuple(motion['second'])]))
    return {
        'kind': 'sense',
        'seed': seed,
        'short': t == 3,
        'radius': radius,
        'targets': [[round(x, 2), round(y, 2)] for x, y in targets],
        'masses': [[round(x, 2), round(y, 2)] for x, y in masses],
        'motion': motion,
        'holes': [list(h) for h in holes],
        'frames': frames,
        'glow': [{'side': _glow_side(m), 'strength': row['glow']} for m in masses] if row['glow'] else [],
        'flash': row['flash'],
        'need': len(targets),
    }


def parse_points(raw: str, limit: int = 4) -> list[tuple[float, float]] | None:
    """Нажатия «x:y;x:y» в долях клетки; None, если запись битая."""
    points = []
    for part in (raw or '').split(';'):
        if not part.strip():
            continue
        try:
            x, y = (float(v) for v in part.split(':'))
        except ValueError:
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        points.append((x, y))
    return points if len(points) <= limit else None


def check_sense(task: dict, answer: str | list) -> bool:
    """Каждая масса отмечена ровно одним нажатием не дальше 0,7 клетки, лишних нажатий нет."""
    points = parse_points(answer) if isinstance(answer, str) else answer
    if points is None or len(points) != len(task['targets']):
        return False
    for tx, ty in task['targets']:
        near = [p for p in points if math.hypot(p[0] - tx, p[1] - ty) <= SENSE_HIT]
        if len(near) != 1:
            return False
    return True


def sense_note(task: dict, success: bool, stage_no: int) -> str:
    """Строка экрана итога: место массы словами («Сбои») или передача координат («Готовность»)."""
    if not success:
        return ''
    if task['motion']:
        return 'Объект без сигнатуры. Координаты переданы в модель «Опознание».'
    if stage_no >= 2:
        return ', '.join(place_text(tuple(t)) if n == 0 else place_text(tuple(t)).lower()
                         for n, t in enumerate(task['targets'])) + '. Корпус знал раньше глаз.'
    return ''


# --- Модель 5. Огонь, «Опознание» -------------------------------------------
#
# Класс цели не печатается: его определяют по силуэту. Соответствие постоянное
# на всю игру; в классах B и C есть гражданский силуэт, в классе A — нет.

SHAPES = ('fighter', 'tug', 'module', 'transport', 'container', 'probe')
SHAPE_TITLES = {
    'fighter': 'истребитель',
    'tug': 'буксир',
    'module': 'станция-модуль',
    'transport': 'транспорт',
    'container': 'контейнер',
    'probe': 'зонд',
}
SHAPE_CLASS = {
    'fighter': 'A', 'tug': 'A',
    'module': 'B', 'transport': 'B',
    'container': 'C', 'probe': 'C',
}
KLASSES = ('A', 'B', 'C')
CIVILIAN_SHAPES = ('transport', 'probe')
MILITARY_SHAPES = tuple(s for s in SHAPES if s not in CIVILIAN_SHAPES)
FIRE_REASONS = ('code', 'klass', 'sector')
FIRE_REASON_TITLES = {'code': 'код', 'klass': 'класс', 'sector': 'сектор'}
ROTATIONS = (0, 90, 180, 270)

FIRE_TARGETS = {0: 4, 1: 6, 2: 6, 3: 1}          # по ступени синхронизации
SECTOR_CHANCE = {0: 0.0, 1: 0.5, 2: 0.8, 3: 0.8}
SILENT_CHANCE = {0: 0.0, 1: 0.2, 2: 0.3, 3: 0.3}  # транспондер молчит
CIVILIAN_MATCH_CHANCE = {1: 0.15, 2: 0.4, 3: 0.6}  # по этапу: подходящая цель — гражданский силуэт
FIRE_VERBS = {1: 'Отметить', 2: 'Захват', 3: 'Пуск'}
FIRE_SHORT_SECONDS = 15                           # сокращённый режим: время на решение
FIRE_READY_SECONDS = {0: 60, 1: 60, 2: 45}        # «Готовность»: таймер полной задачи
FIRE_TIME_TOLERANCE = 3.0                         # запас на сеть и перерисовку, секунды
UNSIGNED_CHANCE = 0.3                             # «Готовность»: доля задач с целью без сигнатуры
CODE_ALPHABET = ''.join(ch for ch in string.ascii_uppercase + string.digits if ch not in '01OI')


def _code(rng: random.Random, avoid: set[str]) -> str:
    while True:
        value = ''.join(rng.choice(CODE_ALPHABET) for _ in range(4))
        if value not in avoid:
            return value


def in_sector(degrees: int, sector: list[int]) -> bool:
    """Попадание вектора в сектор [от, до] по часовой стрелке, градусы 0..359."""
    start, end = sector
    degrees %= 360
    if start <= end:
        return start <= degrees <= end
    return degrees >= start or degrees <= end


def target_klass(target: dict) -> str | None:
    """Класс цели по её силуэту; у цели без сигнатуры класса нет."""
    return SHAPE_CLASS.get(target.get('shape'))


def violations(criteria: dict, target: dict) -> set[str]:
    """Какие признаки «чужого» у цели не сходятся: код, класс, сектор.

    Цель без сигнатуры (силуэта нет) судится только по сектору: «цель без сигнатуры
    в секторе — чужая».
    """
    if target.get('unsigned'):
        if criteria['sector'] and in_sector(target['vector'], criteria['sector']):
            return set()
        return {'sector'}
    bad = set()
    if target['code'] in criteria['listed']:
        bad.add('code')
    if target_klass(target) != criteria['klass']:
        bad.add('klass')
    if criteria['sector'] and not in_sector(target['vector'], criteria['sector']):
        bad.add('sector')
    return bad


def target_matches(criteria: dict, target: dict) -> bool:
    """Цель «чужая»: код не из списка, класс по силуэту совпал и (если задан) вектор в секторе."""
    return not violations(criteria, target)


def is_civilian(target: dict) -> bool:
    return target.get('shape') in CIVILIAN_SHAPES


def criteria_text(criteria: dict) -> str:
    parts = [f'код не из списка: {", ".join(criteria["listed"])}', f'класс {criteria["klass"]}']
    if criteria['sector']:
        parts.append(f'вектор в секторе {criteria["sector"][0]}°–{criteria["sector"][1]}°')
    return '; '.join(parts)


def _vector(rng: random.Random, sector: list[int] | None, inside: bool) -> int:
    if not sector:
        return rng.randrange(0, 360, 5)
    start, end = sector
    width = (end - start) % 360
    if inside:
        return (start + rng.randint(0, width)) % 360
    return (end + rng.randint(1, 359 - width)) % 360


def _make_target(rng: random.Random, criteria: dict, *, matching: bool, civilian: bool,
                 silent: float, rotate: bool) -> dict:
    """Цель, подходящая под все критерии или нарушающая хотя бы один."""
    listed = criteria['listed']
    sector = criteria['sector']
    if matching:
        violate: set[str] = set()
    else:
        available = ['code', 'klass'] + (['sector'] if sector else [])
        violate = {name for name in available if rng.random() < 0.5} or {rng.choice(available)}

    if 'code' in violate:
        code = rng.choice(listed)
    elif rng.random() < silent:
        code = None  # транспондер молчит: «нет» не входит в список
    else:
        code = _code(rng, set(listed))
    klass = criteria['klass']
    if 'klass' in violate:
        shape = rng.choice([s for s in SHAPES if SHAPE_CLASS[s] != klass])
    elif matching:
        pool = CIVILIAN_SHAPES if civilian else MILITARY_SHAPES
        shape = rng.choice([s for s in pool if SHAPE_CLASS[s] == klass])
    else:
        shape = rng.choice([s for s in SHAPES if SHAPE_CLASS[s] == klass])
    vector = _vector(rng, sector, 'sector' not in violate)
    rot = rng.choice(ROTATIONS) if rotate else 0
    return {'shape': shape, 'code': code, 'vector': vector, 'rot': rot}


def gen_fire(seed: int, level: int, stage_no: int = 1) -> dict:
    """Задача «Опознание».

    Ступень синхронизации меняет форму: 0% — четыре цели и справочник силуэтов;
    25% — шесть целей, сектор с вероятностью 0,5; 50% — справочник убран, сектор чаще,
    силуэты повёрнуты; 75% — одна цель, подтвердить или отклонить с причиной, 15 с.
    Этап меняет содержание: долю гражданских силуэтов среди подходящих целей, глагол
    кнопки и таймер на «Готовности». Ровно одна цель подходит (в полной форме).
    """
    rng = random.Random(seed)
    t = tier(level)
    short = t == 3
    listed = [_code(rng, set())]
    listed.append(_code(rng, set(listed)))
    sector = None
    if rng.random() < SECTOR_CHANCE[t]:
        start = rng.randrange(0, 360, 15)
        sector = [start, (start + 120) % 360]
    civilian = rng.random() < CIVILIAN_MATCH_CHANCE[stage_no]
    klass = rng.choice(('B', 'C')) if civilian else rng.choice(KLASSES)
    criteria = {'listed': listed, 'klass': klass, 'sector': sector}
    options = {'civilian': civilian, 'silent': SILENT_CHANCE[t], 'rotate': t >= 2}

    if short:
        targets = [_make_target(rng, criteria, matching=rng.random() < 0.5, **options)]
        time_limit = FIRE_SHORT_SECONDS
    else:
        winner = rng.randrange(FIRE_TARGETS[t])
        targets = [_make_target(rng, criteria, matching=(i == winner), **options)
                   for i in range(FIRE_TARGETS[t])]
        time_limit = FIRE_READY_SECONDS[t] if stage_no >= 3 else None
    unsigned_rule = False
    if not short and stage_no >= 3 and sector and rng.random() < UNSIGNED_CHANCE:
        # Цель без сигнатуры: силуэта нет, есть масса и вектор. Чужая, если в секторе.
        n = rng.randrange(len(targets))
        targets[n] = {'shape': None, 'code': None, 'vector': _vector(rng, sector, n == winner), 'rot': 0,
                      'unsigned': True}
        unsigned_rule = True
    return {
        'kind': 'fire',
        'seed': seed,
        'short': short,
        'reference': t <= 1,
        'verb': FIRE_VERBS[stage_no],
        'time_limit': time_limit,
        'criteria': criteria,
        'targets': targets,
        'unsigned_rule': unsigned_rule,
    }


def fire_expected(task: dict) -> str:
    """Верный ответ: номер цели (с нуля) или confirm/reject для сокращённого режима."""
    matches = [i for i, t in enumerate(task['targets']) if target_matches(task['criteria'], t)]
    if task['short']:
        return 'confirm' if matches else 'reject'
    return str(matches[0])


def fire_time_over(task: dict, elapsed: float | None) -> bool:
    """Вышло ли время на решение (если у задачи есть таймер)."""
    limit = task.get('time_limit')
    return bool(limit) and elapsed is not None and elapsed > limit + FIRE_TIME_TOLERANCE


def check_fire(task: dict, answer: str, elapsed: float | None = None) -> bool:
    """Проверка «Опознания». В сокращённом режиме «отклонить» принимается только с верно
    названным нарушенным признаком (`reject:code`, `reject:klass`, `reject:sector`);
    если нарушено несколько, подходит любой из них."""
    if fire_time_over(task, elapsed):
        return False
    answer = str(answer)
    if not task['short']:
        return answer == fire_expected(task)
    bad = violations(task['criteria'], task['targets'][0])
    if not bad:
        return answer == 'confirm'
    kind, _, reason = answer.partition(':')
    return kind == 'reject' and reason in bad


def fire_chosen(task: dict, answer: str) -> dict | None:
    """Цель, по которой игрок нажал кнопку (для сокращённого режима — только при подтверждении)."""
    answer = str(answer)
    if task['short']:
        return task['targets'][0] if answer == 'confirm' else None
    if answer.isdigit() and int(answer) < len(task['targets']):
        return task['targets'][int(answer)]
    return None


def fire_note(task: dict, answer: str, success: bool, stage_no: int) -> str:
    """Строка экрана итога: с этапа «Сбои» успех по гражданскому силуэту называется прямо."""
    chosen = fire_chosen(task, answer)
    if success and stage_no >= 2 and chosen is not None and is_civilian(chosen):
        return f'Учебная цель: {SHAPE_TITLES[chosen["shape"]]}. Засчитано.'
    return ''


# --- Сводка задачи для дебрифа ----------------------------------------------

def attempt_summary(task: dict, data: dict | None = None, elapsed: float | None = None) -> dict:
    """Короткая сводка задачи для журнала мастера: была ли цель гражданской, был ли
    фантом и отмечен ли он, был ли сбой тяги. `data` — ответ игрока (None для отказа)."""
    kind = task.get('kind')
    summary: dict = {'stage': task.get('stage', 1)}
    if kind == 'fire':
        matches = [t for t in task['targets'] if target_matches(task['criteria'], t)]
        if matches and is_civilian(matches[0]):
            summary['civilian'] = SHAPE_TITLES[matches[0]['shape']]
        if task.get('unsigned_rule'):
            summary['unsigned'] = True
        if data is not None:
            answer = str(data.get('answer', ''))
            chosen = fire_chosen(task, answer)
            if chosen is not None and is_civilian(chosen):
                summary['fired_civilian'] = SHAPE_TITLES[chosen['shape']]
            if answer == 'timeout' or fire_time_over(task, elapsed):
                summary['timeout'] = True
    elif kind == 'parts':
        phantoms = [i for i in task['items'] if i['phantom']]
        if phantoms:
            summary['phantom'] = phantoms[0]['label'].split(':')[0]
            if data is not None:
                summary['phantom_marked'] = bool(phantom_items(task) & parse_marked(data.get('missing', '')))
        if task.get('flip'):
            summary['flip'] = True
    elif kind == 'move':
        glitch = task['physics'].get('glitch')
        if glitch:
            summary['glitch'] = glitch['type']
    elif kind == 'sense':
        if task.get('motion'):
            summary['moving'] = True
        elif task['need'] > 1:
            summary['masses'] = task['need']
    elif kind == 'nav':
        if task.get('target'):
            summary['intercept'] = True
    return summary


def summary_text(summary: dict) -> str:
    """Сводка одной строкой для журнала мастера."""
    parts = []
    if summary.get('fired_civilian'):
        parts.append(f'нажато по гражданскому силуэту: {summary["fired_civilian"]}')
    elif summary.get('civilian'):
        parts.append(f'подходящая цель — гражданский силуэт: {summary["civilian"]}')
    if summary.get('unsigned'):
        parts.append('цель без сигнатуры')
    if summary.get('timeout'):
        parts.append('время вышло')
    if summary.get('phantom'):
        marked = summary.get('phantom_marked')
        state = '' if marked is None else (': отмечен' if marked else ': пропущен')
        parts.append(f'фантом «{summary["phantom"]}»{state}')
    if summary.get('flip'):
        parts.append('самопереключение')
    if summary.get('glitch'):
        parts.append(f'сбой тяги: {MOVE_GLITCH_TITLES[summary["glitch"]]}')
    if summary.get('moving'):
        parts.append('движущаяся масса')
    if summary.get('masses'):
        parts.append(f'масс: {summary["masses"]}')
    if summary.get('intercept'):
        parts.append('точка встречи')
    return '; '.join(parts)


# --- Дела досье -------------------------------------------------------------

DOSSIER_PARTS = ('life', 'death', 'arrival')
CASE_LETTERS = 'АБВГДЕЖЗИКЛМ'


def case_label(index: int) -> str:
    """Метка дела по порядку открытия у игрока: «Дело А», «Дело Б», …"""
    return f'Дело {CASE_LETTERS[index]}'


def dossier_cases(opened: list[tuple[str, str]]) -> list[dict]:
    """Дела игрока из полученных карточек `(персонаж, часть)` в порядке получения.

    Дело открывает «жизнь» и получает метку по порядку. Результат — список словарей
    `{index, label, character_id, parts}`, где `parts` — полученные части дела по порядку.
    """
    cases: list[dict] = []
    for character_id, part in opened:
        case = next((c for c in cases if c['character_id'] == character_id), None)
        if case is None:
            case = {'index': len(cases), 'label': case_label(len(cases)),
                    'character_id': character_id, 'parts': []}
            cases.append(case)
        if part not in case['parts']:
            case['parts'].append(part)
    return cases


def next_dossier_part(parts: list[str]) -> str | None:
    """Следующая часть дела: после жизни смерть, затем попадание; `None`, если дело закрыто."""
    return DOSSIER_PARTS[len(parts)] if len(parts) < len(DOSSIER_PARTS) else None


def pick_new_case(rng: random.Random, character_ids: list[str], cases: list[dict]) -> str | None:
    """Случайный персонаж звена, чью «жизнь» игрок ещё не читал; `None`, если все открыты."""
    opened = {c['character_id'] for c in cases}
    unread = [cid for cid in character_ids if cid not in opened]
    return rng.choice(unread) if unread else None


# --- Жетоны и коды ----------------------------------------------------------

TOKEN_ALPHABET = string.digits
TOKEN_LENGTH = 4


def generate_codes(rng: random.Random, count: int, taken: set[str] | None = None) -> list[str]:
    """Уникальные четырёхзначные коды, не входящие в `taken`."""
    taken = set(taken or ())
    if len(taken) + count > len(TOKEN_ALPHABET) ** TOKEN_LENGTH:
        raise ValueError('кодов не хватает')
    codes: list[str] = []
    while len(codes) < count:
        code = ''.join(rng.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))
        if code not in taken:
            taken.add(code)
            codes.append(code)
    return codes


def parse_codes(raw: str) -> list[str]:
    """Коды из строки ввода: разделители — пробел и запятая."""
    return raw.replace(',', ' ').split()
