"""Веб-интерфейс симулятора.

Две страницы. «Стол» ведёт одну группу по дереву в реальном времени — это
инструмент мастера за игрой. «Сводка» прогоняет дерево тысячи раз и показывает,
как модель ведёт себя в целом.

Базы данных нет: состояние прогона ездит в скрытом поле формы, см. codec.
"""

from __future__ import annotations

import random

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .codec import StateCodec, decode, encode
from .engine import (
    SUBSTITUTE_ANY_MARK,
    Phase,
    RuleError,
    Simulation,
)
from .loader import TreeError, available_trees, get_tree
from .montecarlo import run_batch
from .strategies import STRATEGIES

MAX_ITERATIONS = 50000


def index(request):
    """Начальная страница: выбор дерева и режима."""
    return render(
        request,
        'sim/index.html',
        {
            'trees': [_tree_card(name) for name in available_trees()],
            'strategies': sorted(STRATEGIES),
        },
    )


# ------------------------------------------------------------------ мастерский стол


@require_http_methods(['GET', 'POST'])
def table(request):
    """Прогон одной группы под управлением мастера."""
    tree_name = request.GET.get('tree') or request.POST.get('tree') or None
    try:
        tree = get_tree(tree_name) if tree_name else get_tree()
    except TreeError as error:
        raise Http404(str(error)) from error

    guest_probability = _float(
        request.POST.get('guest_probability', request.GET.get('guest_probability')),
        default=1.0,
    )

    token = request.POST.get('state') or request.GET.get('state')
    error_message = ''

    if token:
        try:
            state = decode(token)
        except StateCodec as error:
            return render(
                request,
                'sim/table.html',
                {'tree': tree, 'fatal': str(error)},
                status=400,
            )
        sim = Simulation(
            tree=tree,
            state=state,
            rng=random.Random(),
            guest_probability=guest_probability,
        )
        if request.method == 'POST':
            try:
                _apply_action(sim, request.POST)
            except RuleError as rule_error:
                error_message = str(rule_error)
    else:
        sim = Simulation(
            tree=tree, rng=random.Random(), guest_probability=guest_probability
        )

    return render(request, 'sim/table.html', _table_context(sim, tree, error_message))


def _apply_action(sim: Simulation, data) -> None:
    action = data.get('action')

    if action == 'guest':
        sim.resolve_guest(
            counter=data.get('counter') == '1',
            substitute=data.get('substitute') or None,
            chosen_mark=data.get('chosen_mark') or None,
            john_taken=_tristate(data.get('john_taken')),
        )
        return

    if action == 'choice':
        key = data.get('choice')
        if not key:
            raise RuleError('вариант не назван')
        sim.apply_choice(key, john_slows=data.get('john_slows') == '1')
        return

    if action == 'advance':
        sim.advance()
        return

    if action == 'final':
        key = data.get('final')
        if not key:
            raise RuleError('финал не назван')
        sim.choose_final(key)
        return

    raise RuleError(f'неизвестное действие {action!r}')


def _table_context(sim: Simulation, tree, error_message: str = '') -> dict:
    state = sim.state
    context = {
        'tree': tree,
        'state': state,
        'token': encode(state),
        'error': error_message,
        'guest_probability': sim.guest_probability,
        'marks_held': [m for m in tree.marks if state.has(m)],
        'marks_missing': sim.marks_not_held(),
        'mark_descriptions': tree.mark_descriptions,
        'finals': sim.finals_status(),
        'log': list(reversed(state.log)),
        'john_moves_left': state.john_moves_left,
        'phase': state.phase.value,
    }

    if state.finished:
        context['final'] = tree.final(state.final)
        context['path'] = state.visited
        return context

    node = sim.node
    context.update(
        {
            'node': node,
            'act': tree.acts.get(node.act, {}),
            'variations': sim.variations(),
            'choices': sim.active_choices(),
            'disabled_choices': sorted(state.disabled_choices),
            'roles': tree.roles,
            'can_john_move': sim.can_john_move(),
            'can_counter': sim.can_counter(),
            'can_slow': sim.can_slow(),
            'slow_targets': _slow_targets(sim),
            'counter_needs_substitute': sim.counter_needs_substitute(),
            'substitute_options': sim.substitute_options(),
            'substitute_any_mark': SUBSTITUTE_ANY_MARK,
            'is_last_lamp': sim.is_last_lamp(),
            'guest_takes_person': _guest_takes_person(sim),
            'player_absent': state.player_absent,
            'awaiting_guest': state.phase is Phase.GUEST,
            'awaiting_choice': state.phase is Phase.CHOICE,
            'awaiting_final': state.phase is Phase.FINAL,
            'has_choices': bool(node.choices),
        }
    )
    return context


def _slow_targets(sim: Simulation) -> dict[str, str]:
    """Куда уведёт «замедлить» каждый из вариантов — мастеру видно заранее."""
    if not sim.can_slow():
        return {}
    targets = {}
    for choice in sim.active_choices():
        target_key = sim.slow_target(choice.key)
        if target_key:
            target = sim.node.choice(target_key)
            targets[choice.key] = f'{target_key} → {target.to}'
    return targets


def _guest_takes_person(sim: Simulation) -> bool:
    """Сработает ли щит РАСЧЁТА: гость не возьмёт бумагу и уведёт человека."""
    node = sim.node
    guest = node.guest
    if not sim.state.guest_present or guest is None or not guest.blocked_by_mark:
        return False
    return sim.state.has(guest.blocked_by_mark)


def restart(request):
    tree_name = request.POST.get('tree') or request.GET.get('tree') or ''
    url = reverse('sim:table')
    return redirect(f'{url}?tree={tree_name}' if tree_name else url)


# ------------------------------------------------------------------------- сводка


def report(request):
    """Сводка по серии прогонов."""
    tree_name = request.GET.get('tree') or None
    strategy = request.GET.get('strategy', 'random')
    if strategy not in STRATEGIES:
        strategy = 'random'
    iterations = min(max(_int(request.GET.get('iterations'), 2000), 1), MAX_ITERATIONS)
    seed = _int(request.GET.get('seed'), 0) or None
    guest_probability = min(max(_float(request.GET.get('guest_probability'), 1.0), 0.0), 1.0)

    try:
        batch, tree = run_batch(
            iterations=iterations,
            strategy_name=strategy,
            tree_name=tree_name,
            seed=seed,
            guest_probability=guest_probability,
        )
    except TreeError as error:
        raise Http404(str(error)) from error

    return render(
        request,
        'sim/report.html',
        {
            'tree': tree,
            'report': batch,
            'strategies': sorted(STRATEGIES),
            'iterations': iterations,
            'seed': seed,
            'guest_probability': guest_probability,
            'strategy': strategy,
            'finals_rows': _finals_rows(batch, tree),
            'marks_rows': _marks_rows(batch, tree),
            'nodes_rows': _nodes_rows(batch, tree),
            'available_rows': _counter_rows(batch, batch.available_count),
            'mark_count_rows': _counter_rows(batch, batch.mark_count_at_final),
            'john_rows': _counter_rows(batch, batch.john_moves_spent),
            'floor_share': batch.share(batch.floor_rule_fired),
            'top_paths': [
                {'path': path, 'share': batch.share(value), 'count': value}
                for path, value in batch.paths.most_common(10)
            ],
            'path_total': len(batch.paths),
            'flags_rows': [
                {'label': flag, 'share': batch.share(value), 'count': value}
                for flag, value in batch.flags.most_common()
            ],
        },
    )


def _finals_rows(batch, tree) -> list[dict]:
    return [
        {
            'key': key,
            'title': tree.final(key).title,
            'opened_by': ' или '.join(tree.final(key).opened_by) or 'всегда',
            'available_share': batch.share(batch.finals_available[key]),
            'chosen_share': batch.share(batch.finals_chosen[key]),
            'closed_share': batch.share(batch.finals_closed[key]),
        }
        for key in sorted(tree.finals)
    ]


def _marks_rows(batch, tree) -> list[dict]:
    return [
        {
            'mark': mark,
            'description': tree.mark_descriptions.get(mark, ''),
            'share': batch.share(batch.marks_at_final[mark]),
            'count': batch.marks_at_final[mark],
        }
        for mark in sorted(tree.marks, key=lambda m: -batch.marks_at_final[m])
    ]


def _nodes_rows(batch, tree) -> list[dict]:
    labels = {'dead_end': 'тупик', 'false_exit': 'ложный выход'}
    rows = []
    for node_id in sorted(tree.nodes, key=_node_sort_key):
        node = tree.node(node_id)
        rows.append(
            {
                'id': node_id,
                'title': node.title,
                'act': node.act,
                'kind': labels.get(node.kind, ''),
                'lamp': node.lamp,
                'minutes': node.minutes,
                'share': batch.share(batch.node_visits[node_id]),
                'count': batch.node_visits[node_id],
            }
        )
    return rows


def _counter_rows(batch, counter) -> list[dict]:
    return [
        {'label': key, 'share': batch.share(counter[key]), 'count': counter[key]}
        for key in sorted(counter)
    ]


def _tree_card(name: str) -> dict:
    tree = get_tree(name)
    return {
        'name': name,
        'title': tree.title,
        'nodes': len(tree.nodes),
        'finals': len(tree.finals),
        'marks': len(tree.marks),
        'lamps': len(tree.lamp_nodes()),
        'source': tree.source,
    }


def _node_sort_key(node_id: str) -> tuple:
    head, _, tail = node_id.partition('.')
    return (int(head), tail)


def _int(raw, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float(raw, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _tristate(raw) -> bool | None:
    if raw == '1':
        return True
    if raw == '0':
        return False
    return None
