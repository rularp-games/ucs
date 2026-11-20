"""Страницы игрока и панель мастера."""

from __future__ import annotations

import random
import time
from functools import wraps

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import engine, services
from .loader import (DOSSIER_PART_TITLES, GENDERS, MEMORY_THEME_TITLES, ContentError, available_contents,
                     get_content, render_gender)
from .models import TOKEN_TTL, Game, Player, RadioMessage
from .services import LEVEL_BAD, LEVEL_OK

SESSION_KEY = 'ls_player_id'
RADIO_LIMIT = 100
DARK_LIMIT = 10

MODE_LABELS = {
    engine.MODE_CLOSED: 'закрыто',
    engine.MODE_INSTRUCTION: 'открыто',
    engine.MODE_FULL: 'открыто',
    engine.MODE_SHORT: 'открыто',
    engine.MODE_AUTO: 'автоматика',
}
MESSAGE_LEVELS = {
    LEVEL_OK: messages.SUCCESS,
    LEVEL_BAD: messages.ERROR,
}


# --- Общее для игрока -------------------------------------------------------

def player_required(view):
    """Пускает только вошедшего игрока; иначе на страницу входа."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        player_id = request.session.get(SESSION_KEY)
        player = (
            Player.objects.select_related('game', 'flight').filter(pk=player_id).first()
            if player_id else None
        )
        if player is None:
            return redirect('ls:login')
        request.player = player
        return view(request, *args, **kwargs)

    return wrapper


def flash(request, outcome: services.Outcome) -> None:
    """Сообщение об исходе действия. Текст пакета, если он есть, идёт отдельным блоком:
    игрок должен прочитать купленное сразу, не уходя на страницу «Что я знаю»."""
    messages.add_message(request, MESSAGE_LEVELS.get(outcome.level, messages.WARNING), outcome.message)
    if outcome.payload:
        messages.add_message(request, messages.INFO, outcome.payload, extra_tags='payload')


def render_player(request, template: str, context: dict | None = None, status: int = 200):
    """Отрисовка страницы поста: общие поля игрока добавляются ко всем страницам."""
    player = request.player
    base = {
        'player': player,
        'started': player.game.is_started,
        'has_radio': player.radio_level >= 1,
    }
    base.update(context or {})
    return render(request, template, base, status=status)


def login_view(request):
    if request.session.get(SESSION_KEY) and Player.objects.filter(pk=request.session[SESSION_KEY]).exists():
        return redirect('ls:post')
    error = ''
    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        callsign = request.POST.get('callsign', '').strip()
        game = next((g for g in Game.objects.all() if g.code.casefold() == code.casefold()), None)
        player = None
        if game is not None:
            player = next(
                (p for p in game.players.all() if p.callsign.casefold() == callsign.casefold()), None
            )
        if player is None:
            error = 'Нет такого сеанса или позывного.'
        else:
            request.session.cycle_key()
            request.session[SESSION_KEY] = player.pk
            return redirect('ls:post')
    return render(request, 'ls/login.html', {'error': error, 'code': request.POST.get('code', '')})


@require_POST
def logout_view(request):
    request.session.pop(SESSION_KEY, None)
    return redirect('ls:login')


@player_required
def post(request):
    player = request.player
    values = player.sync_values()
    minutes = player.game.game_minutes()
    params = []
    for key in engine.PARAMS:
        mode = engine.mode_of(values, key, minutes)
        params.append({
            'key': key,
            'title': engine.PARAM_TITLES[key],
            'model': engine.MODEL_TITLES[key],
            'value': values[key],
            'mode': mode,
            'label': MODE_LABELS[mode],
        })
    return render_player(request, 'ls/post.html', {
        'params': params,
        'dark_tokens': services.active_tokens(player),
        'summaries': services.background_summaries(player),
        'active': 'post',
    })


# --- Модели тренировок ------------------------------------------------------

NAV_CELL = 50          # клетка поля в единицах SVG
NAV_MARGIN = 34        # поле для подписей столбцов и строк
NAV_CARD_PAD = 0.45    # запас вокруг созвездия на карточке, клетки


def _field_context() -> dict:
    """Общая разметка книжного поля 8 × 11 (навигация и восприятие): клетки, подписи, размеры."""
    width, height = engine.NAV_COLS * NAV_CELL, engine.NAV_ROWS * NAV_CELL
    return {
        'cells': [{'label': engine.cell_label(c, r), 'x': c * NAV_CELL, 'y': r * NAV_CELL}
                  for r in range(engine.NAV_ROWS) for c in range(engine.NAV_COLS)],
        'cell': NAV_CELL,
        'width': width,
        'height': height,
        'margin': NAV_MARGIN,
        'view_w': width + NAV_MARGIN + 6,
        'view_h': height + NAV_MARGIN + 6,
        'col_labels': [{'text': chr(ord('A') + c), 'x': c * NAV_CELL + NAV_CELL // 2}
                       for c in range(engine.NAV_COLS)],
        'row_labels': [{'text': r + 1, 'y': r * NAV_CELL + NAV_CELL // 2 + 7}
                       for r in range(engine.NAV_ROWS)],
    }


def _nav_context(task: dict) -> dict:
    field = _field_context()
    cards = []
    for n, card in enumerate(task['cards']):
        extent = max(max(abs(x), abs(y)) for x, y in card['points']) + NAV_CARD_PAD
        size = round(2 * extent * NAV_CELL, 1)
        cards.append({
            'number': n + 1,
            'size': size,
            'half': round(size / 2, 1),
            # Карточка в масштабе 1:1 с полем: её ширина — доля ширины поля.
            'width_pct': round(100 * size / field['view_w'], 2),
            'points': [{'x': round(x * NAV_CELL, 1), 'y': round(y * NAV_CELL, 1)} for x, y in card['points']],
        })
    target = None
    if task['target']:
        t = task['target']
        cx, cy = (t['x'] + 0.5) * NAV_CELL, (t['y'] + 0.5) * NAV_CELL
        target = {'x': cx, 'y': cy, 'x2': cx + t['dx'] * NAV_CELL, 'y2': cy + t['dy'] * NAV_CELL,
                  'rx': cx - 9, 'ry': cy - 9, 'lx': min(cx + 12, NAV_CELL * (engine.NAV_COLS - 3)),
                  'ly': cy - 12 if cy > NAV_CELL else cy + 26}
    return {
        **field,
        'stars': [{'x': round(x * NAV_CELL, 1), 'y': round(y * NAV_CELL, 1)} for x, y in task['stars']],
        'highlight': [{'x': round(x * NAV_CELL, 1), 'y': round(y * NAV_CELL, 1)} for x, y in task['highlight']],
        'beacon_lines': [{'number': n + 1, 'text': text} for n, text in enumerate(task['beacon_text'])],
        'nav_cards': cards,
        'target_mark': target,
        'need': len(task['beacons']) + 1,
    }


def _sense_context(task: dict) -> dict:
    field = _field_context()
    frames = [[{'x': round(x * NAV_CELL, 1), 'y': round(y * NAV_CELL, 1), 'r': round(2.0 * k, 2)}
               for x, y, k in frame]
              for frame in task['frames']]
    return {
        **field,
        'frames': frames,
        'glow': task['glow'],
        'need': task['need'],
        'frame_ms': engine.SENSE_FRAME_MS,
    }


def _parts_context(task: dict) -> dict:
    switches = []
    for i, name in enumerate(task['switches']):
        words = engine.switch_words(name)
        switches.append({'index': i, 'name': name, 'on': task['initial'][i], 'on_word': words[2],
                         'off_word': words[3]})
    return {
        'switches': switches,
        'checklist': [{'index': n, 'label': item['label']} for n, item in enumerate(task['items'])],
    }


def _fire_context(task: dict, now: float) -> dict:
    targets = []
    for index, target in enumerate(task['targets']):
        targets.append({
            **target,
            'index': index,
            'number': index + 1,
            'code_text': target['code'] or 'нет',
        })
    reference = [
        {'klass': k, 'shapes': [{'key': s, 'title': engine.SHAPE_TITLES[s]}
                                for s in engine.SHAPES if engine.SHAPE_CLASS[s] == k]}
        for k in engine.KLASSES
    ]
    limit = task.get('time_limit')
    return {
        'targets': targets,
        'criteria_text': engine.criteria_text(task['criteria']),
        'reference': reference,
        'reasons': [{'key': r, 'title': engine.FIRE_REASON_TITLES[r]} for r in engine.FIRE_REASONS
                    if r != 'sector' or task['criteria']['sector']],
        'time_left': max(0, round(limit - (now - task['issued_at']))) if limit else None,
    }


def _new_task(param: str, level: int, stage: int, game: Game, now: float) -> dict:
    """Новая задача по уровню синхронизации и этапу игры. Зерно случайное, задача целиком на сервере."""
    seed = random.getrandbits(32)
    if param == 'nav':
        task = engine.gen_nav(seed, level, stage)
    elif param == 'move':
        task = engine.gen_move(seed, level, stage, now, engine.hall_seed(game.pk, game.game_minutes(), 'move'))
    elif param == 'parts':
        task = engine.gen_parts(seed, level, stage, engine.hall_seed(game.pk, game.game_minutes(), 'parts'))
    elif param == 'sense':
        task = engine.gen_sense(seed, level, stage)
    else:
        task = engine.gen_fire(seed, level, stage)
    task.update({'level': level, 'stage': stage, 'issued_at': now})
    return task


def _check_task(param: str, task: dict, data, now: float) -> tuple[bool, str]:
    """Серверная проверка ответа: данные формы разбираются и сверяются с задачей.

    Возвращает успех и строку для экрана итога (обычно пустую).
    """
    if param == 'nav':
        return engine.check_nav(task, data.get('answer', '')), ''
    if param == 'move':
        return engine.check_move(task, data.get('record', ''), now), ''
    if param == 'parts':
        state = engine.parse_parts_answer(data.get('answer', ''), len(task['switches']))
        marked = engine.parse_marked(data.get('missing', ''))
        return state is not None and engine.check_parts(task, state, marked), ''
    if param == 'sense':
        success = engine.check_sense(task, data.get('answer', ''))
        return success, engine.sense_note(task, success, task['stage'])
    answer = data.get('answer', '')
    success = engine.check_fire(task, answer, now - task['issued_at'])
    return success, engine.fire_note(task, answer, success, task['stage'])


def _take_task(player: Player, param: str) -> dict | None:
    """Снять выданную задачу атомарно: один ответ на одну задачу, даже при двойной отправке."""
    with transaction.atomic():
        fresh = Player.objects.select_for_update().get(pk=player.pk)
        task = fresh.tasks.pop(param, None)
        if task is not None:
            fresh.save(update_fields=['tasks'])
    player.tasks = fresh.tasks
    return task


def _current_task(player: Player, param: str, level: int, stage: int, now: float) -> dict:
    """Выданная задача или новая. Новая выдаётся только если прежней нет (ответ или отказ сняли её)
    или если уровень игрока с тех пор изменился (мастер поправил синхронизацию)."""
    with transaction.atomic():
        fresh = Player.objects.select_for_update().select_related('game').get(pk=player.pk)
        task = fresh.tasks.get(param)
        if not task or task.get('level') != level:
            task = _new_task(param, level, stage, fresh.game, now)
            fresh.tasks[param] = task
            fresh.save(update_fields=['tasks'])
    player.tasks = fresh.tasks
    return task


def _model_result(request, param: str, before: dict[str, int], kind: str, *, capped: bool = False,
                  card: bool = False, note: str = ''):
    """Страница итога: «Выполнено», «Провал» или «Отказ принят»."""
    player = Player.objects.select_related('game', 'flight').get(pk=request.player.pk)
    request.player = player
    values = player.sync_values()
    minutes = player.game.game_minutes()
    unlocked = [engine.PARAM_TITLES[key] for key in engine.PARAMS
                if key != param and engine.is_open(values, key, minutes) and not engine.is_open(before, key, minutes)]
    return render_player(request, 'ls/model_result.html', {
        'param': param,
        'param_title': engine.PARAM_TITLES[param],
        'kind': kind,
        'value': values[param],
        'mode': engine.mode_of(values, param, minutes),
        'unlocked': ', '.join(unlocked),
        'capped': capped,
        'card': card,
        'note': note,
        'pause': services.pause_seconds(player, param) if kind == 'fail' else 0,
        'active': 'post',
    })


@player_required
def model(request, param):
    if param not in engine.PARAMS:
        raise Http404
    player = request.player
    mode = engine.mode_of(player.sync_values(), param, player.game.game_minutes())
    if mode == engine.MODE_CLOSED:
        messages.warning(request, f'Параметр «{engine.PARAM_TITLES[param]}» закрыт.')
        return redirect('ls:post')
    level = player.get_sync(param)
    stage = services.stage_of(player.game)

    if request.method == 'POST':
        if mode == engine.MODE_AUTO:
            return redirect('ls:model', param=param)
        before = player.sync_values()
        if request.POST.get('action') == 'refuse':
            task = _take_task(player, param)
            summary = engine.attempt_summary(task) if task else {'stage': stage}
            card = services.apply_deviation(player, param, stage, summary)
            return _model_result(request, param, before, 'deviation', card=card)
        task = _take_task(player, param)
        if not task or task.get('level') != level:
            return redirect('ls:model', param=param)
        now = time.time()
        success, note = _check_task(param, task, request.POST, now)
        summary = engine.attempt_summary(task, request.POST, now - task['issued_at'])
        capped = services.record_attempt(player, param, success, stage, summary)
        return _model_result(request, param, before, 'success' if success else 'fail', capped=capped, note=note)

    context = {
        'param': param,
        'param_title': engine.PARAM_TITLES[param],
        'model_title': engine.MODEL_TITLES[param],
        'value': level,
        'mode': mode,
        'auto': mode == engine.MODE_AUTO,
        'instruction': mode == engine.MODE_INSTRUCTION,
        'short': mode == engine.MODE_SHORT,
        'active': 'post',
    }
    if mode == engine.MODE_AUTO:
        return render_player(request, f'ls/model_{param}.html', context)

    if param not in player.tasks:
        pause = services.pause_seconds(player, param)
        if pause:
            return render_player(request, 'ls/model_pause.html', {**context, 'pause': pause})
    now = time.time()
    task = _current_task(player, param, level, stage, now)
    context['task'] = task
    if param == 'nav':
        context.update(_nav_context(task))
    elif param == 'parts':
        context.update(_parts_context(task))
    elif param == 'sense':
        context.update(_sense_context(task))
    elif param == 'fire':
        context.update(_fire_context(task, now))
    return render_player(request, f'ls/model_{param}.html', context)


# --- Сопротивление ----------------------------------------------------------

@player_required
def codes(request):
    player = request.player
    if request.method == 'POST':
        for outcome in services.redeem_codes(player, request.POST.get('codes', '')):
            flash(request, outcome)
        return redirect('ls:codes')
    return render_player(request, 'ls/codes.html', {
        'dark_tokens': services.active_tokens(player),
        'blocked_seconds': services.block_seconds(player),
        'ttl_minutes': int(TOKEN_TTL.total_seconds() // 60),
        'active': 'codes',
    })


CARD_KINDS = [
    ('self', 'О себе', 'Одна тема памяти на выбор. Каждая тема один раз, предыдущие покупать не нужно. '
                       'Первые два фрагмента бесплатны, они на странице «Что я знаю».'),
    ('flight', 'О звене', 'Новое дело на кого-то из звена или продолжение уже открытого. '
                          'Чей это позывной, не сказано.'),
    ('program', 'О программе', 'Один из трёх вопросов, каждый один раз. Фоновые сводки приходят сами.'),
    ('archive', 'Архив', 'Следующая запись из очереди архива. Записи вне очереди открываются только кодом.'),
    ('radio', 'Рация', 'Первая покупка открывает эфир, вторая позволяет пользоваться рацией и на посту.'),
]


def _card_options(player: Player) -> dict[str, list[services.Option]]:
    minutes = player.game.game_minutes()
    return {
        'self': services.memory_options(player, minutes),
        'flight': services.flight_options(player, minutes),
        'program': services.program_options(player, minutes),
    }


@player_required
def cards(request):
    if request.method == 'POST':
        flash(request, services.buy(request.player, request.POST.get('kind', ''), request.POST.get('choice', '')))
        return redirect('ls:cards')
    options = _card_options(request.player)
    kinds = [{'key': k, 'title': t, 'hint': h, 'options': options.get(k)} for k, t, h in CARD_KINDS]
    return render_player(request, 'ls/cards.html', {'kinds': kinds, 'active': 'cards'})


def _known_sections(player: Player) -> list[dict]:
    """«Что я знаю»: память, досье по делам, программа. Группа — подзаголовок с карточками."""
    content = get_content(player.game.content_name)
    purchases = list(player.purchases.all())

    memory = [{'title': f['title'], 'text': f['text']} for f in services.free_memory(player)]
    memory += [{'title': MEMORY_THEME_TITLES.get(p.key, 'Память'), 'text': p.payload}
               for p in purchases if p.kind == 'self']

    by_key = {p.key: p.payload for p in purchases if p.kind == 'flight' and p.key}
    cases = []
    for case in services.dossier_cases_of(player):
        cases.append({'title': case['label'], 'items': [
            {'title': DOSSIER_PART_TITLES[part], 'text': by_key[f'{case["character_id"]}:{part}']}
            for part in case['parts']]})
    old = [{'title': '', 'text': p.payload} for p in purchases if p.kind == 'flight' and not p.key]
    if old:
        cases.append({'title': 'Прежние карточки', 'items': old})

    questions = {q.id: q.title for q in content.program_questions}
    program = [{'title': 'Сводки', 'items': services.background_summaries(player)}]
    asked = [{'title': questions.get(p.key, ''), 'text': p.payload} for p in purchases if p.kind == 'program']
    if asked:
        program.append({'title': 'Вопросы', 'items': asked})

    return [
        {'key': 'self', 'title': 'О себе', 'groups': [{'title': '', 'items': memory}]},
        {'key': 'flight', 'title': 'О звене', 'groups': cases},
        {'key': 'program', 'title': 'О программе', 'groups': program},
    ]


@player_required
def known(request):
    player = request.player
    entries = {e.code: e for e in get_content(player.game.content_name).archive}
    genders = services.game_genders(player.game)
    archive = [
        {'code': o.code, 'title': entries[o.code].title, 'text': render_gender(entries[o.code].text, genders)}
        for o in player.archive_opens.all() if o.code in entries
    ]
    return render_player(request, 'ls/known.html', {
        'sections': _known_sections(player), 'archive': archive, 'active': 'known'})


@player_required
def radio(request):
    player = request.player
    if player.radio_level < 1:
        messages.warning(request, 'Рации нет.')
        return redirect('ls:post')
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if text:
            RadioMessage.objects.create(game=player.game, player=player, text=text[:500])
        return redirect('ls:radio')
    items = RadioMessage.objects.filter(game=player.game).select_related('player')[:RADIO_LIMIT]
    return render_player(request, 'ls/radio.html', {'items': items, 'active': 'radio'})


# --- Панель мастера ---------------------------------------------------------

def format_minutes(minutes: float | None) -> str:
    if minutes is None:
        return 'не начата'
    sign = '-' if minutes < 0 else ''
    total = int(abs(minutes))
    return f'{sign}{total // 60}:{total % 60:02d}'


def _content_flights(contents: list[str]) -> dict[str, int]:
    """Сколько звеньев в каждом контенте; нечитаемый контент считается пустым."""
    result = {}
    for name in contents:
        try:
            result[name] = len(get_content(name).flights)
        except ContentError:
            result[name] = 0
    return result


@staff_member_required
def master_index(request):
    contents = available_contents()
    content_flights = _content_flights(contents)
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        code = request.POST.get('code', '').strip()
        content_name = request.POST.get('content', '')
        try:
            flights = int(request.POST.get('flights', '1'))
        except ValueError:
            flights = 0
        error = ''
        if not title or not code:
            error = 'Нужны название и код.'
        elif any(g.code.casefold() == code.casefold() for g in Game.objects.all()):
            error = 'Игра с таким кодом уже есть.'
        elif content_name not in contents:
            error = 'Неизвестный контент.'
        elif not 1 <= flights <= content_flights[content_name]:
            error = f'Звеньев в этом контенте: {content_flights[content_name]}. Нужно от 1 до этого числа.'
        if not error:
            try:
                game = services.create_game(title, code, content_name, flights)
            except ContentError as exc:
                error = f'Контент не загрузился: {exc}'
            else:
                messages.success(request, f'Игра «{game.title}» создана.')
                return redirect('ls:master_game', game_id=game.pk)
        messages.error(request, error)
        return redirect('ls:master_index')
    games = Game.objects.annotate(player_count=Count('players', distinct=True))
    return render(request, 'ls/master_index.html', {
        'games': games,
        'contents': [{'name': name, 'flights': content_flights[name]} for name in contents],
        'max_flights': max(content_flights.values(), default=1) or 1,
    })


def _character_name(content, player: Player) -> str:
    """Полное имя персонажа по текущему полу игрока."""
    return content.character(player.character_id).names[player.gender]


@staff_member_required
def master_game(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    minutes = game.game_minutes()
    stage = engine.stage(minutes)
    players = list(game.players.select_related('flight').annotate(
        purchase_count=Count('purchases', distinct=True),
        archive_count=Count('archive_opens', distinct=True),
    ))
    content = get_content(game.content_name)
    debrief = services.debrief(game)
    for p in players:
        p.character_name = _character_name(content, p)
        p.debrief = debrief[p.pk]
        p.sync_cells = [
            {'key': key, 'title': engine.PARAM_TITLES[key], 'value': p.get_sync(key),
             'mode': engine.mode_of(p.sync_values(), key, minutes)}
            for key in engine.PARAMS
        ]
    flights = [{'flight': f, 'players': [p for p in players if p.flight_id == f.pk]}
               for f in game.flights.all()]
    return render(request, 'ls/master_game.html', {
        'game': game,
        'players': players,
        'flights': flights,
        'dark_entries': game.dark_entries.prefetch_related('players')[:DARK_LIMIT],
        'minutes': format_minutes(minutes),
        'offset_minutes': game.clock_offset_seconds // 60,
        'stage': stage,
        'stage_title': engine.STAGE_TITLES[stage],
        'stage_cap': engine.stage_cap(stage),
    })


@staff_member_required
@require_POST
def master_action(request, game_id):
    """Одна точка входа для кнопок мастера: старт, часы, правки игрока."""
    game = get_object_or_404(Game, pk=game_id)
    op = request.POST.get('op', '')
    anchor = ''
    if op == 'start':
        if not game.is_started:
            game.started_at = timezone.now()
            game.save(update_fields=['started_at'])
    elif op == 'clock':
        try:
            delta = int(request.POST.get('delta', '0'))
        except ValueError:
            delta = 0
        game.clock_offset_seconds += delta
        game.save(update_fields=['clock_offset_seconds'])
    elif op == 'dark':
        ids = [int(i) for i in request.POST.getlist('players') if i.isdigit()]
        chosen = list(game.players.filter(pk__in=ids))
        if not chosen:
            messages.error(request, 'Никто не отмечен.')
        else:
            entry = services.issue_tokens(game, chosen)
            messages.success(request, 'В темноту: ' + ', '.join(p.callsign for p in chosen)
                             + f'. Выдано жетонов: {entry.tokens.count()}.')
    else:
        player = get_object_or_404(Player, pk=request.POST.get('player', 0), game=game)
        anchor = f'#p{player.pk}'
        parts = op.split(':')
        try:
            if parts[0] == 'sync' and parts[1] in engine.PARAMS:
                value = player.get_sync(parts[1]) + int(parts[2])
                player.set_sync(parts[1], max(0, min(engine.SYNC_MAX, value)))
            elif parts[0] == 'cards':
                player.cards = max(0, player.cards + int(parts[1]))
            elif parts[0] == 'gender' and parts[1] in GENDERS:
                player.gender = parts[1]
            elif parts[0] == 'radio':
                player.radio_level = max(0, min(services.RADIO_MAX, player.radio_level + int(parts[1])))
            else:
                raise ValueError(op)
        except (IndexError, ValueError):
            messages.error(request, 'Непонятная команда.')
            return redirect(reverse('ls:master_game', args=[game.pk]))
        player.save()
    return redirect(reverse('ls:master_game', args=[game.pk]) + anchor)


TOKEN_STATE_LABELS = {
    'waiting': 'ждёт',
    'matched': 'совпал',
    'burned': 'сгорел',
    'expired': 'просрочен',
}


@staff_member_required
def master_player(request, game_id, player_id):
    game = get_object_or_404(Game, pk=game_id)
    player = get_object_or_404(Player.objects.select_related('flight'), pk=player_id, game=game)
    now = timezone.now()
    entries = {e.code: e for e in get_content(game.content_name).archive}
    issued = [
        {'token': t, 'state': TOKEN_STATE_LABELS[t.state(now)]}
        for t in player.tokens_issued.select_related('used_by')
    ]
    opens = [
        {'code': o.code, 'title': entries[o.code].title if o.code in entries else '?',
         'via': o.get_via_display(), 'created_at': o.created_at}
        for o in player.archive_opens.all()
    ]
    return render(request, 'ls/master_player.html', {
        'game': game,
        'player': player,
        'character_name': _character_name(get_content(game.content_name), player),
        'attempts': [{'a': a, 'text': engine.summary_text(a.summary or {})} for a in player.attempts.all()[:200]],
        'purchases': player.purchases.all(),
        'tokens_issued': issued,
        'tokens_used': player.tokens_used.select_related('issued_to'),
        'archive_opens': opens,
        'titles': engine.PARAM_TITLES,
    })
