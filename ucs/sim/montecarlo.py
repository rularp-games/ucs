"""Пакетный прогон дерева и статистика по модели.

Отвечает на вопросы, которые за столом проверить нельзя: с какими метками группы
доходят до третьего акта, какие финалы им реально доступны, как часто срабатывает
правило нижней границы, куда уходят ходы Джона/Джейн, какие карточки не видит
почти никто.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .engine import JOHN_MOVES_TOTAL, Phase, Simulation
from .loader import Tree, get_tree
from .strategies import Strategy, get_strategy


@dataclass
class RunResult:
    """Итог одного прогона."""

    path: list[str]
    marks: list[str]
    final: str | None
    available_finals: list[str]
    closed_finals: list[str]
    flags: list[str]
    john_moves_spent: int
    floor_rule_fired: bool
    minutes: dict[int, int]
    log: list[str] = field(default_factory=list)


@dataclass
class Report:
    """Сводка по серии прогонов."""

    tree_id: str
    tree_title: str
    strategy: str
    strategy_description: str
    iterations: int
    guest_probability: float
    seed: int | None

    node_visits: Counter = field(default_factory=Counter)
    marks_at_final: Counter = field(default_factory=Counter)
    mark_count_at_final: Counter = field(default_factory=Counter)
    mark_sets: Counter = field(default_factory=Counter)
    finals_chosen: Counter = field(default_factory=Counter)
    finals_available: Counter = field(default_factory=Counter)
    available_count: Counter = field(default_factory=Counter)
    finals_closed: Counter = field(default_factory=Counter)
    flags: Counter = field(default_factory=Counter)
    john_moves_spent: Counter = field(default_factory=Counter)
    floor_rule_fired: int = 0
    paths: Counter = field(default_factory=Counter)
    minutes_per_act: Counter = field(default_factory=Counter)
    sample_log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- доступ

    def share(self, value: int) -> float:
        return value / self.iterations if self.iterations else 0.0

    def never_visited(self, tree: Tree) -> list[str]:
        return sorted(set(tree.nodes) - set(self.node_visits))

    def unreached_marks(self, tree: Tree) -> list[str]:
        return [m for m in tree.marks if not self.marks_at_final[m]]

    def unreached_finals(self, tree: Tree) -> list[str]:
        return [k for k in tree.finals if not self.finals_available[k]]


def run_once(
    tree: Tree,
    strategy: Strategy,
    rng: random.Random,
    guest_probability: float = 1.0,
    keep_log: bool = False,
) -> RunResult:
    sim = Simulation(tree=tree, rng=rng, guest_probability=guest_probability)
    minutes: Counter = Counter()

    guard = 0
    while not sim.state.finished:
        guard += 1
        if guard > 100:
            raise RuntimeError(f'прогон не сходится, путь: {sim.state.visited}')

        node = sim.node
        if sim.state.phase is Phase.GUEST:
            decision = strategy.guest_decision(sim)
            sim.resolve_guest(
                counter=decision.counter,
                substitute=decision.substitute,
                chosen_mark=decision.chosen_mark,
                john_taken=strategy.john_taken(sim),
            )
            continue

        if sim.state.phase is Phase.FINAL:
            minutes[node.act] += node.minutes
            sim.choose_final(strategy.choose_final(sim))
            continue

        # Phase.CHOICE
        minutes[node.act] += node.minutes
        if not node.choices:
            sim.advance()
            continue
        sim.apply_choice(strategy.choose(sim), john_slows=strategy.wants_slow(sim))

    st = sim.state
    return RunResult(
        path=list(st.visited),
        marks=sorted(st.marks),
        final=st.final,
        available_finals=sim.available_finals(),
        closed_finals=sorted(st.closed_finals),
        flags=sorted(st.flags),
        john_moves_spent=JOHN_MOVES_TOTAL - st.john_moves_left,
        floor_rule_fired=st.floor_rule_fired,
        minutes=dict(minutes),
        log=[str(e) for e in st.log] if keep_log else [],
    )


def run_batch(
    iterations: int = 10000,
    strategy_name: str = 'random',
    tree_name: str | None = None,
    seed: int | None = None,
    guest_probability: float = 1.0,
) -> tuple[Report, Tree]:
    tree = get_tree(tree_name) if tree_name else get_tree()
    rng = random.Random(seed)
    strategy = get_strategy(strategy_name, rng)
    report = Report(
        tree_id=tree.id,
        tree_title=tree.title,
        strategy=strategy.name,
        strategy_description=strategy.description,
        iterations=iterations,
        guest_probability=guest_probability,
        seed=seed,
    )

    for index in range(iterations):
        keep_log = index == 0
        result = run_once(tree, strategy, rng, guest_probability, keep_log=keep_log)
        if keep_log:
            report.sample_log = result.log

        report.node_visits.update(result.path)
        report.paths[' → '.join(result.path)] += 1
        report.marks_at_final.update(result.marks)
        report.mark_count_at_final[len(result.marks)] += 1
        report.mark_sets[', '.join(result.marks) or '— нет меток —'] += 1
        report.finals_available.update(result.available_finals)
        report.available_count[len(result.available_finals)] += 1
        report.finals_closed.update(result.closed_finals)
        report.flags.update(result.flags)
        report.john_moves_spent[result.john_moves_spent] += 1
        if result.final:
            report.finals_chosen[result.final] += 1
        if result.floor_rule_fired:
            report.floor_rule_fired += 1
        for act, value in result.minutes.items():
            report.minutes_per_act[act] += value

    report.warnings = _collect_warnings(report, tree)
    return report, tree


def _collect_warnings(report: Report, tree: Tree) -> list[str]:
    """Расхождения между моделью и тем, что даёт прогон."""
    problems: list[str] = []

    never = report.never_visited(tree)
    if never:
        problems.append(f'карточки, в которые не попал ни один прогон: {", ".join(never)}')

    unreached = report.unreached_marks(tree)
    if unreached:
        problems.append(
            f'метки, которых нет ни в одном финале: {", ".join(unreached)}'
        )

    dead_finals = report.unreached_finals(tree)
    if dead_finals:
        problems.append(
            f'финалы, недоступные ни в одном прогоне: {", ".join(dead_finals)}'
        )

    if report.available_count[0]:
        problems.append(
            f'прогонов без единого доступного финала: {report.available_count[0]} '
            '(так быть не должно, F3 открыт всегда)'
        )
    if report.available_count[1]:
        problems.append(
            f'прогонов с ровно одним доступным финалом: {report.available_count[1]} '
            '(правило нижней границы не закрыло случай)'
        )

    path_length = {len(p.split(' → ')) for p in report.paths}
    # Узлов в прогоне ровно столько, сколько в дереве пар (акт, позиция)
    expected = len({(n.act, n.slot) for n in tree.nodes.values()})
    if path_length != {expected}:
        problems.append(
            f'длина пути по узлам: {sorted(path_length)}, а не ровно {expected}'
        )

    over_budget = [
        f'акт {act}: {report.minutes_per_act[act] / report.iterations:.1f} мин'
        for act in sorted(report.minutes_per_act)
        if report.minutes_per_act[act] / report.iterations > 40
    ]
    if over_budget:
        problems.append('акты выходят за 40 минут — ' + '; '.join(over_budget))

    if report.john_moves_spent[3] == 0 and report.strategy != 'passive':
        problems.append(
            'ни один прогон не израсходовал все три хода Джона/Джейн'
        )

    return problems
