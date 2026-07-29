"""Форматирование сводки по прогонам в текст.

Отдельно от montecarlo, чтобы Django-команда и веб брали одни и те же цифры.
"""

from __future__ import annotations

from .loader import Tree
from .montecarlo import Report


def _bar(share: float, width: int = 24) -> str:
    filled = round(share * width)
    return '█' * filled + '·' * (width - filled)


def _line(label: str, value: int, report: Report, pad: int = 22) -> str:
    share = report.share(value)
    return f'  {label:<{pad}} {share:6.1%}  {_bar(share)}  {value}'


def format_report(report: Report, tree: Tree, top_paths: int = 8) -> str:
    out: list[str] = []
    add = out.append

    add(f'Дерево: {report.tree_title} ({report.tree_id})')
    add(f'Стратегия: {report.strategy} — {report.strategy_description}')
    add(
        f'Прогонов: {report.iterations}, '
        f'вероятность прихода гостя на лампу: {report.guest_probability:.0%}, '
        f'seed: {report.seed}'
    )

    add('')
    add('ФИНАЛЫ — что группе доступно')
    for status in sorted(
        tree.finals, key=lambda k: -report.finals_available[k]
    ):
        final = tree.final(status)
        add(_line(f'{status} {final.title}', report.finals_available[status], report))

    add('')
    add('ФИНАЛЫ — что группа взяла')
    for key in sorted(tree.finals, key=lambda k: -report.finals_chosen[k]):
        add(_line(f'{key} {tree.final(key).title}', report.finals_chosen[key], report))

    add('')
    add('Сколько финалов было доступно')
    for count in sorted(report.available_count):
        add(_line(f'{count} финала', report.available_count[count], report, pad=10))

    add('')
    add('МЕТКИ к моменту финала')
    for mark, value in report.marks_at_final.most_common():
        add(_line(mark, value, report, pad=12))
    for mark in report.unreached_marks(tree):
        add(f'  {mark:<12}   0.0%  {_bar(0)}  0')

    add('')
    add('Сколько меток донесли')
    for count in sorted(report.mark_count_at_final):
        add(_line(f'{count} шт.', report.mark_count_at_final[count], report, pad=10))

    add('')
    add('ХОДЫ ДЖОНА/ДЖЕЙН — израсходовано за игру')
    for count in sorted(report.john_moves_spent):
        add(_line(f'{count} из 3', report.john_moves_spent[count], report, pad=10))

    add('')
    add('ПРАВИЛА — как часто срабатывают')
    add(_line('нижняя граница', report.floor_rule_fired, report))
    for key in sorted(report.finals_closed):
        add(_line(f'закрыт финал {key}', report.finals_closed[key], report))
    for flag, value in report.flags.most_common():
        add(_line(_flag_title(flag), value, report))

    add('')
    add('КАРТОЧКИ — доля прогонов, где карточка была на столе')
    for node_id in sorted(tree.nodes, key=_node_sort_key):
        node = tree.node(node_id)
        tags = []
        if node.kind != 'normal':
            tags.append({'dead_end': 'тупик', 'false_exit': 'ложный выход'}[node.kind])
        if node.lamp:
            tags.append('лампа')
        label = f'{node_id} {node.title}' + (f' [{", ".join(tags)}]' if tags else '')
        add(_line(label, report.node_visits[node_id], report, pad=52))

    if top_paths:
        add('')
        add(f'ЧАСТЫЕ ПУТИ (первые {top_paths})')
        for path, value in report.paths.most_common(top_paths):
            add(f'  {report.share(value):6.1%}  {path}')
        add(f'  всего различных путей: {len(report.paths)}')

    if report.warnings:
        add('')
        add('РАСХОЖДЕНИЯ')
        for warning in report.warnings:
            add(f'  ! {warning}')

    return '\n'.join(out)


def format_sample_log(report: Report) -> str:
    if not report.sample_log:
        return ''
    return 'ЖУРНАЛ ПЕРВОГО ПРОГОНА\n' + '\n'.join(f'  {row}' for row in report.sample_log)


def _flag_title(flag: str) -> str:
    return {
        'number_left_room': 'число ушло из комнаты',
        'letter_sent': 'письмо в редакцию ушло',
    }.get(flag, flag)


def _node_sort_key(node_id: str) -> tuple:
    head, _, tail = node_id.partition('.')
    return (int(head), tail)
