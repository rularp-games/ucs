"""Страницы игрока и панель мастера."""

from __future__ import annotations

import random
import time
from functools import wraps

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import engine, services
from .loader import ContentError, available_contents, get_content
from .models import TOKEN_TTL, Game, Player, RadioMessage
from .services import LEVEL_BAD, LEVEL_OK

SESSION_KEY = 'ls_player_id'
TASK_KEY = 'ls_task_{}'
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
    params = []
    for key in engine.PARAMS:
        mode = engine.mode_of(values, key)
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
        'active': 'post',
    })


# --- Модели тренировок ------------------------------------------------------

NAV_CELL = 50


def _nav_context(task: dict) -> dict:
    cells = [
        {'label': engine.cell_label(c, r), 'x': c * NAV_CELL, 'y': r * NAV_CELL}
        for r in range(engine.NAV_ROWS) for c in range(engine.NAV_COLS)
    ]
    return {
        'cells': cells,
        'cell': NAV_CELL,
        'width': engine.NAV_COLS * NAV_CELL,
        'height': engine.NAV_ROWS * NAV_CELL,
        'col_labels': [{'text': chr(ord('A') + c), 'x': c * NAV_CELL + NAV_CELL // 2}
                       for c in range(engine.NAV_COLS)],
        'row_labels': [{'text': r + 1, 'y': r * NAV_CELL + NAV_CELL // 2 + 4}
                       for r in range(engine.NAV_ROWS)],
        'stars': [{'x': round(x * NAV_CELL, 1), 'y': round(y * NAV_CELL, 1)} for x, y in task['stars']],
        'beacons_text': ', '.join(task['beacons']),
        'need': len(task['beacons']) + 1,
    }


def _parts_context(task: dict) -> dict:
    return {
        'switches': [
            {'index': i, 'name': name, 'on': task['initial'][i]}
            for i, name in enumerate(engine.SWITCHES)
        ],
        'checklist': [item['label'] for item in task['items']],
    }


def _fire_context(task: dict) -> dict:
    targets = []
    for index, target in enumerate(task['targets']):
        targets.append({
            **target,
            'index': index,
            'number': index + 1,
            'code_text': target['code'] or 'нет',
            'shape_title': engine.SHAPE_TITLES[target['shape']],
        })
    return {'targets': targets, 'criteria_text': engine.criteria_text(task['criteria'])}


def _new_task(param: str, mode: str, sync: int) -> dict:
    seed = random.getrandbits(32)
    if param == 'nav':
        task = engine.gen_nav(seed, mode)
    elif param == 'move':
        task = engine.gen_move(seed, mode, time.time())
    elif param == 'parts':
        task = engine.gen_parts(seed, mode, sync)
    else:
        task = engine.gen_fire(seed, mode)
    task['mode'] = mode
    return task


def _check_task(param: str, task: dict, data) -> bool:
    """Серверная проверка ответа: данные формы разбираются и сверяются с задачей."""
    if param == 'nav':
        return engine.check_nav(task, data.get('answer', ''))
    if param == 'move':
        try:
            elapsed = float(data.get('elapsed', '0'))
        except ValueError:
            return False
        return engine.check_move(task, data.get('result', ''), elapsed, time.time())
    if param == 'parts':
        state = engine.parse_parts_answer(data.get('answer', ''))
        return state is not None and engine.check_parts(task, state)
    return engine.check_fire(task, data.get('answer', ''))


def _model_result(request, param: str, before: int, kind: str):
    """Страница итога: «Выполнено», «Провал» или «Отказ принят»."""
    player = Player.objects.select_related('game', 'flight').get(pk=request.player.pk)
    request.player = player
    values = player.sync_values()
    index = engine.PARAMS.index(param)
    unlocked = ''
    if index + 1 < len(engine.PARAMS):
        nxt = engine.PARAMS[index + 1]
        was = engine.is_open({**values, param: before}, nxt)
        if engine.is_open(values, nxt) and not was:
            unlocked = engine.PARAM_TITLES[nxt]
    return render_player(request, 'ls/model_result.html', {
        'param': param,
        'param_title': engine.PARAM_TITLES[param],
        'kind': kind,
        'value': values[param],
        'mode': engine.mode_of(values, param),
        'unlocked': unlocked,
        'active': 'post',
    })


@player_required
def model(request, param):
    if param not in engine.PARAMS:
        raise Http404
    player = request.player
    mode = engine.mode_of(player.sync_values(), param)
    if mode == engine.MODE_CLOSED:
        messages.warning(request, f'Параметр «{engine.PARAM_TITLES[param]}» закрыт.')
        return redirect('ls:post')
    session_key = TASK_KEY.format(param)

    if request.method == 'POST':
        if mode == engine.MODE_AUTO:
            return redirect('ls:model', param=param)
        before = player.get_sync(param)
        if request.POST.get('action') == 'refuse':
            request.session.pop(session_key, None)
            services.apply_deviation(player, param)
            return _model_result(request, param, before, 'deviation')
        task = request.session.get(session_key)
        if not task or task.get('mode') != mode:
            return redirect('ls:model', param=param)
        request.session.pop(session_key, None)
        success = _check_task(param, task, request.POST)
        services.record_attempt(player, param, success)
        return _model_result(request, param, before, 'success' if success else 'fail')

    context = {
        'param': param,
        'param_title': engine.PARAM_TITLES[param],
        'model_title': engine.MODEL_TITLES[param],
        'value': player.get_sync(param),
        'mode': mode,
        'auto': mode == engine.MODE_AUTO,
        'instruction': mode == engine.MODE_INSTRUCTION,
        'short': mode == engine.MODE_SHORT,
        'active': 'post',
    }
    if mode == engine.MODE_AUTO:
        return render_player(request, f'ls/model_{param}.html', context)

    task = request.session.get(session_key)
    if request.GET.get('new') or not task or task.get('mode') != mode:
        task = _new_task(param, mode, player.get_sync(param))
        request.session[session_key] = task
    context['task'] = task
    if param == 'nav':
        context.update(_nav_context(task))
    elif param == 'parts':
        context.update(_parts_context(task))
    elif param == 'fire':
        context.update(_fire_context(task))
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
    ('self', 'О себе', 'Следующий пакет памяти вашего персонажа.'),
    ('flight', 'О звене', 'Следующая карточка досье на кого-то из звена. Чей это позывной, не сказано.'),
    ('program', 'О программе', 'Следующий пакет о программе и мире вокруг неё.'),
    ('archive', 'Архив', 'Следующая запись из очереди архива. Записи вне очереди открываются только кодом.'),
    ('radio', 'Рация', 'Первая покупка открывает эфир, вторая позволяет пользоваться рацией и на посту.'),
]


@player_required
def cards(request):
    if request.method == 'POST':
        flash(request, services.buy(request.player, request.POST.get('kind', '')))
        return redirect('ls:cards')
    kinds = [{'key': k, 'title': t, 'hint': h} for k, t, h in CARD_KINDS]
    return render_player(request, 'ls/cards.html', {'kinds': kinds, 'active': 'cards'})


@player_required
def known(request):
    player = request.player
    streams = [
        ('self', 'О себе'),
        ('flight', 'О звене'),
        ('program', 'О программе'),
    ]
    sections = [
        {'title': title, 'items': list(player.purchases.filter(kind=kind))}
        for kind, title in streams
    ]
    entries = {e.code: e for e in get_content(player.game.content_name).archive}
    archive = [
        {'code': o.code, 'title': entries[o.code].title, 'text': entries[o.code].text}
        for o in player.archive_opens.all() if o.code in entries
    ]
    return render_player(request, 'ls/known.html', {'sections': sections, 'archive': archive, 'active': 'known'})


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


@staff_member_required
def master_game(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    players = list(game.players.select_related('flight').annotate(
        purchase_count=Count('purchases', distinct=True),
        archive_count=Count('archive_opens', distinct=True),
    ))
    for p in players:
        p.sync_cells = [
            {'key': key, 'title': engine.PARAM_TITLES[key], 'value': p.get_sync(key),
             'mode': engine.mode_of(p.sync_values(), key)}
            for key in engine.PARAMS
        ]
    flights = [{'flight': f, 'players': [p for p in players if p.flight_id == f.pk]}
               for f in game.flights.all()]
    return render(request, 'ls/master_game.html', {
        'game': game,
        'players': players,
        'flights': flights,
        'dark_entries': game.dark_entries.prefetch_related('players')[:DARK_LIMIT],
        'minutes': format_minutes(game.game_minutes()),
        'offset_minutes': game.clock_offset_seconds // 60,
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
        'attempts': player.attempts.all()[:200],
        'purchases': player.purchases.all(),
        'tokens_issued': issued,
        'tokens_used': player.tokens_used.select_related('issued_to'),
        'archive_opens': opens,
        'titles': engine.PARAM_TITLES,
    })
