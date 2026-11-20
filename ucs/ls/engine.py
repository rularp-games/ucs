"""Правила игры в виде чистых функций: без Django, без базы данных.

Здесь лежат правила синхронизации, генераторы задач для четырёх моделей
тренировок и их проверка. Задача — обычный словарь из чисел, строк и списков,
поэтому её можно положить в сессию как есть. Ответ игрока проверяется функциями
`check_*` по сохранённой задаче: ни в какие HTML и JS проверка не заглядывает.
"""

from __future__ import annotations

import random
import string

# --- Синхронизация ----------------------------------------------------------

PARAMS = ('nav', 'move', 'parts', 'fire')
PARAM_TITLES = {
    'nav': 'Навигация',
    'move': 'Движение',
    'parts': 'Части',
    'fire': 'Огонь',
}
MODEL_TITLES = {
    'nav': 'Маяки',
    'move': 'Строй',
    'parts': 'Панель',
    'fire': 'Опознание',
}

SYNC_STEP = 25
SYNC_MAX = 100
OPEN_THRESHOLD = 50  # предыдущий параметр открывает следующий при этом значении
OPEN_FROM_START = ('nav', 'move', 'parts')  # мирные параметры открыты сразу, огонь — по цепочке

MODE_CLOSED = 'closed'
MODE_INSTRUCTION = 'instruction'  # sync < 50: полная модель с инструкцией
MODE_FULL = 'full'                # 50..74: полная модель без инструкции
MODE_SHORT = 'short'              # 75: сокращённая модель
MODE_AUTO = 'auto'                # 100: автоматика


def is_open(sync: dict[str, int], param: str) -> bool:
    """Параметр открыт, если он мирный (навигация, движение, части), или уже
    сдвинут с нуля, или предыдущий >= 50. По цепочке открывается только огонь.

    Открытость считается по текущим значениям: отклонение по parts не закрывает
    fire обратно, пока у fire самого есть синхронизация.
    """
    if param in OPEN_FROM_START:
        return True
    index = PARAMS.index(param)
    if sync.get(param, 0) > 0:
        return True
    return sync.get(PARAMS[index - 1], 0) >= OPEN_THRESHOLD


def mode_of(sync: dict[str, int], param: str) -> str:
    """Режим модели по текущему состоянию игрока."""
    if not is_open(sync, param):
        return MODE_CLOSED
    value = sync.get(param, 0)
    if value >= SYNC_MAX:
        return MODE_AUTO
    if value >= 75:
        return MODE_SHORT
    if value >= OPEN_THRESHOLD:
        return MODE_FULL
    return MODE_INSTRUCTION


def after_success(value: int) -> int:
    """Успешная задача: +25, не выше 100."""
    return min(SYNC_MAX, value + SYNC_STEP)


def after_deviation(value: int) -> int:
    """Намеренное отклонение: −25, не ниже 0."""
    return max(0, value - SYNC_STEP)


# --- Модель 1. Навигация, «Маяки» -------------------------------------------

NAV_COLS = 12
NAV_ROWS = 8
NAV_BEACONS = 3
NAV_STARS = 46


def cell_label(col: int, row: int) -> str:
    """Клетка (столбец, строка), с нуля, в виде «A3»: буква — столбец, цифра — строка."""
    return f'{chr(ord("A") + col)}{row + 1}'


def gen_nav(seed: int, mode: str) -> dict:
    """Задача «Маяки»: три маяка и точка выхода. Для сокращённого режима — только выход."""
    rng = random.Random(seed)
    cells = [(c, r) for c in range(NAV_COLS) for r in range(NAV_ROWS)]
    picked = rng.sample(cells, NAV_BEACONS + 1)
    labels = [cell_label(c, r) for c, r in picked]
    stars = [
        [round(rng.uniform(0.1, NAV_COLS - 0.1), 2), round(rng.uniform(0.1, NAV_ROWS - 0.1), 2)]
        for _ in range(NAV_STARS)
    ]
    short = mode == MODE_SHORT
    return {
        'kind': 'nav',
        'seed': seed,
        'short': short,
        'beacons': [] if short else labels[:NAV_BEACONS],
        'exit': labels[NAV_BEACONS],
        'stars': stars,
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

MOVE_SECONDS_FULL = 30
MOVE_SECONDS_SHORT = 15
MOVE_TOLERANCE = 1.5  # допуск на расхождение часов клиента и сервера, секунды


def gen_move(seed: int, mode: str, now: float) -> dict:
    """Задача «Строй»: удержать шарик заданное время; момент выдачи пишется на сервере."""
    short = mode == MODE_SHORT
    return {
        'kind': 'move',
        'seed': seed,
        'short': short,
        'seconds': MOVE_SECONDS_SHORT if short else MOVE_SECONDS_FULL,
        'issued_at': now,
    }


def check_move(task: dict, result: str, elapsed: float, now: float) -> bool:
    """Результат принимается как есть (прототип), но с двумя сверками времени.

    Шарик должен быть удержан весь срок, и с момента выдачи задачи должно
    пройти не меньше этого срока. Иначе задача проваливается, даже если клиент
    прислал «ok».
    """
    if result != 'ok':
        return False
    need = task['seconds']
    if elapsed < need - 0.5:
        return False
    return (now - task['issued_at']) >= need - MOVE_TOLERANCE


# --- Модель 3. Части, «Панель» ----------------------------------------------

SWITCHES = (
    'створка 1', 'створка 2', 'створка 3',
    'ориентация 1', 'ориентация 2', 'ориентация 3',
    'радиатор', 'подвеска',
)
# Тумблеры, которых на панели нет: «тело сбоит».
PHANTOM_SWITCHES = ('створка 5', 'створка 4', 'ориентация 4', 'ориентация 5', 'двигатель 2')
PARTS_ITEMS_FULL = 5
PARTS_ITEMS_SHORT = 3
PHANTOM_CHANCE = 0.5
PHANTOM_MIN_SYNC = 50

VERB = {True: 'включить', False: 'выключить'}


def _verb_for(name: str, value: bool) -> str:
    if name.startswith('створка'):
        return 'открыть' if value else 'закрыть'
    if name == 'радиатор':
        return 'выпустить' if value else 'убрать'
    return VERB[value]


def gen_parts(seed: int, mode: str, sync: int) -> dict:
    """Задача «Панель»: начальное положение тумблеров и чек-лист.

    Пункт чек-листа — словарь {label, switch, value}; у несуществующего тумблера
    switch равен None, при проверке он игнорируется.
    """
    rng = random.Random(seed)
    short = mode == MODE_SHORT
    count = PARTS_ITEMS_SHORT if short else PARTS_ITEMS_FULL
    initial = [rng.random() < 0.5 for _ in SWITCHES]

    phantom = sync >= PHANTOM_MIN_SYNC and rng.random() < PHANTOM_CHANCE
    real_count = count - 1 if phantom else count
    chosen = rng.sample(range(len(SWITCHES)), real_count)
    # Сокращённый режим: половина нужных тумблеров уже стоит как надо.
    already = real_count // 2 if short else 0
    items = []
    for n, index in enumerate(chosen):
        value = initial[index] if n < already else (not initial[index])
        items.append({
            'label': f'{SWITCHES[index]}: {_verb_for(SWITCHES[index], value)}',
            'switch': index,
            'value': value,
        })
    if phantom:
        name = rng.choice(PHANTOM_SWITCHES)
        value = rng.random() < 0.5
        items.insert(rng.randrange(len(items) + 1), {
            'label': f'{name}: {_verb_for(name, value)}',
            'switch': None,
            'value': value,
        })
    return {
        'kind': 'parts',
        'seed': seed,
        'short': short,
        'initial': initial,
        'items': items,
        'phantom': phantom,
    }


def check_parts(task: dict, state: list[bool]) -> bool:
    """Все пункты выполнены, остальные тумблеры стоят как в начале."""
    if len(state) != len(SWITCHES):
        return False
    required = {item['switch']: item['value'] for item in task['items'] if item['switch'] is not None}
    for index, now_value in enumerate(state):
        want = required.get(index, task['initial'][index])
        if bool(now_value) != bool(want):
            return False
    return True


def parse_parts_answer(raw: str) -> list[bool] | None:
    """Ответ — строка из восьми символов 0/1."""
    raw = (raw or '').strip()
    if len(raw) != len(SWITCHES) or set(raw) - {'0', '1'}:
        return None
    return [ch == '1' for ch in raw]


# --- Модель 4. Огонь, «Опознание» -------------------------------------------

SHAPES = ('fighter', 'transport', 'module', 'tug', 'probe')
SHAPE_TITLES = {
    'fighter': 'истребитель',
    'transport': 'транспорт',
    'module': 'станция-модуль',
    'tug': 'буксир',
    'probe': 'зонд',
}
CIVILIAN_SHAPES = ('transport', 'probe')
MILITARY_SHAPES = ('fighter', 'module', 'tug')
KLASSES = ('A', 'B', 'C')
FIRE_TARGETS = 6
CODE_ALPHABET = ''.join(ch for ch in string.ascii_uppercase + string.digits if ch not in '01OI')
CIVILIAN_MATCH_CHANCE = 0.4
SECTOR_CHANCE = 0.5


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


def target_matches(criteria: dict, target: dict) -> bool:
    """Цель «чужая»: код не из списка, класс совпал и (если задан) вектор в секторе."""
    if target['code'] in criteria['listed']:
        return False
    if target['klass'] != criteria['klass']:
        return False
    if criteria['sector'] and not in_sector(target['vector'], criteria['sector']):
        return False
    return True


def criteria_text(criteria: dict) -> str:
    parts = [f'код не из списка: {", ".join(criteria["listed"])}', f'силуэт класса {criteria["klass"]}']
    if criteria['sector']:
        parts.append(f'вектор в секторе {criteria["sector"][0]}°–{criteria["sector"][1]}°')
    return '; '.join(parts)


def _make_target(rng: random.Random, criteria: dict, *, matching: bool, civilian: bool = False) -> dict:
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
    elif rng.random() < 0.2:
        code = None  # транспондер молчит: «нет» не входит в список
    else:
        code = _code(rng, set(listed))
    klass = rng.choice([k for k in KLASSES if k != criteria['klass']]) if 'klass' in violate else criteria['klass']
    if sector:
        start, end = sector
        width = (end - start) % 360
        if 'sector' in violate:
            vector = (end + rng.randint(1, 359 - width)) % 360
        else:
            vector = (start + rng.randint(0, width)) % 360
    else:
        vector = rng.randrange(0, 360, 5)
    if matching and civilian:
        shape = rng.choice(CIVILIAN_SHAPES)
    else:
        shape = rng.choice(SHAPES)
    return {'shape': shape, 'code': code, 'vector': vector, 'klass': klass}


def gen_fire(seed: int, mode: str) -> dict:
    """Задача «Опознание»: шесть целей, ровно одна подходит под критерии.

    В сокращённом режиме цель одна, а игрок подтверждает или отклоняет.
    """
    rng = random.Random(seed)
    short = mode == MODE_SHORT
    listed = [_code(rng, set())]
    listed.append(_code(rng, set(listed)))
    sector = None
    if not short and rng.random() < SECTOR_CHANCE:
        start = rng.randrange(0, 360, 15)
        sector = [start, (start + 120) % 360]
    criteria = {'listed': listed, 'klass': rng.choice(KLASSES), 'sector': sector}

    civilian_match = rng.random() < CIVILIAN_MATCH_CHANCE
    if short:
        matching = rng.random() < 0.5
        targets = [_make_target(rng, criteria, matching=matching, civilian=civilian_match)]
    else:
        winner = rng.randrange(FIRE_TARGETS)
        targets = [
            _make_target(rng, criteria, matching=(i == winner), civilian=civilian_match)
            for i in range(FIRE_TARGETS)
        ]
    return {
        'kind': 'fire',
        'seed': seed,
        'short': short,
        'criteria': criteria,
        'targets': targets,
    }


def fire_expected(task: dict) -> str:
    """Верный ответ: номер цели (с нуля) или confirm/reject для сокращённого режима."""
    matches = [i for i, t in enumerate(task['targets']) if target_matches(task['criteria'], t)]
    if task['short']:
        return 'confirm' if matches else 'reject'
    return str(matches[0])


def check_fire(task: dict, answer: str) -> bool:
    return str(answer) == fire_expected(task)


# --- Колода досье -----------------------------------------------------------

DOSSIER_PARTS = ('life', 'death', 'arrival')


def build_dossier_order(rng: random.Random, character_ids: list[str]) -> list[list[str]]:
    """Личный порядок карточек: все life вперемешку, затем все death, затем все arrival."""
    order: list[list[str]] = []
    for part in DOSSIER_PARTS:
        ids = list(character_ids)
        rng.shuffle(ids)
        order.extend([cid, part] for cid in ids)
    return order


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
