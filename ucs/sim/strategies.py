"""Политики решений для пакетного прогона.

За столом решения принимают люди. Чтобы прогонять дерево тысячи раз, нужны
подстановки: как группа выбирает вариант, как расходуются ходы Джона/Джейн, как
берётся финал. Разные политики дают разные срезы модели — например, «ленивый
путь» проверяет утверждение из раздела 6, что он набирает больше меток, но одна
из них — СОМНЕНИЕ.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .engine import (
    SUBSTITUTE_ANY_MARK,
    SUBSTITUTE_RETURN_MOVE,
    SUBSTITUTE_SKIP_GUEST,
    Simulation,
)


@dataclass
class Decision:
    """Что делает группа в сцене гостя."""

    counter: bool = False
    substitute: str | None = None
    chosen_mark: str | None = None


class Strategy:
    """База. Подклассы переопределяют то, что им интересно."""

    name = 'base'
    description = ''

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    # --------------------------------------------------------------- выбор

    def choose(self, sim: Simulation) -> str:
        return self.rng.choice([c.key for c in sim.active_choices()])

    def choose_final(self, sim: Simulation) -> str:
        return self.rng.choice(sim.available_finals())

    # ----------------------------------------------------- ходы Джона/Джейн

    def guest_decision(self, sim: Simulation) -> Decision:
        """Тратить ли ход «противодействовать»."""
        if not sim.can_counter():
            return Decision()
        return self._counter(sim) if self._wants_counter(sim) else Decision()

    def _wants_counter(self, sim: Simulation) -> bool:
        return self.rng.random() < 0.5

    def _counter(self, sim: Simulation) -> Decision:
        """Собрать корректное решение с учётом того, что метка может быть уже есть."""
        options = sim.substitute_options()
        if not options:
            return Decision(counter=True)
        substitute = self.rng.choice(options)
        chosen_mark = None
        if substitute == SUBSTITUTE_ANY_MARK:
            missing = sim.marks_not_held()
            if not missing:
                return Decision()
            chosen_mark = self.rng.choice(missing)
        return Decision(counter=True, substitute=substitute, chosen_mark=chosen_mark)

    def wants_slow(self, sim: Simulation) -> bool:
        if not sim.can_slow():
            return False
        return self.rng.random() < 0.25

    def john_taken(self, sim: Simulation) -> bool | None:
        """Кого гость уводит в 3.1: None — жребий движка."""
        return None


class RandomStrategy(Strategy):
    name = 'random'
    description = 'Случайные выборы, ходы Джона наугад. Базовая карта дерева.'


class PassiveStrategy(Strategy):
    """Джон/Джейн не вмешивается. Показывает дерево в чистом виде."""

    name = 'passive'
    description = 'Случайные выборы, Джон/Джейн не тратит ходы ни разу.'

    def _wants_counter(self, sim: Simulation) -> bool:
        return False

    def wants_slow(self, sim: Simulation) -> bool:
        return False


class CounterStrategy(Strategy):
    """Все ходы уходят на противодействие гостям."""

    name = 'counter'
    description = 'Джон/Джейн тратит все ходы только на противодействие гостям.'

    def _wants_counter(self, sim: Simulation) -> bool:
        return True

    def wants_slow(self, sim: Simulation) -> bool:
        return False


class SlowStrategy(Strategy):
    """Все ходы уходят на замедление."""

    name = 'slow'
    description = 'Джон/Джейн тратит все ходы только на замедление группы.'

    def _wants_counter(self, sim: Simulation) -> bool:
        return False

    def wants_slow(self, sim: Simulation) -> bool:
        return sim.can_slow()


class LazyStrategy(Strategy):
    """Ленивый путь: всегда первый вариант — он же вариант «срезать угол».

    Проверяет утверждение раздела 6: такой путь набирает меток больше, но одна
    из них — СОМНЕНИЕ.
    """

    name = 'lazy'
    description = 'Группа всегда берёт первый вариант — путь наименьшего сопротивления.'

    def choose(self, sim: Simulation) -> str:
        return sim.active_choices()[0].key

    def choose_final(self, sim: Simulation) -> str:
        return sim.available_finals()[0]

    def _wants_counter(self, sim: Simulation) -> bool:
        return False

    def wants_slow(self, sim: Simulation) -> bool:
        return False


class ThoroughStrategy(Strategy):
    """Обратная ленивой: всегда последний вариант, обычно самый долгий."""

    name = 'thorough'
    description = 'Группа всегда берёт последний вариант — считать честно и долго.'

    def choose(self, sim: Simulation) -> str:
        return sim.active_choices()[-1].key

    def choose_final(self, sim: Simulation) -> str:
        return sim.available_finals()[-1]

    def _wants_counter(self, sim: Simulation) -> bool:
        return True

    def wants_slow(self, sim: Simulation) -> bool:
        return False


STRATEGIES = {
    cls.name: cls
    for cls in (
        RandomStrategy,
        PassiveStrategy,
        CounterStrategy,
        SlowStrategy,
        LazyStrategy,
        ThoroughStrategy,
    )
}


def get_strategy(name: str, rng: random.Random) -> Strategy:
    try:
        return STRATEGIES[name](rng)
    except KeyError:
        raise ValueError(
            f'неизвестная стратегия {name!r}; есть: {", ".join(sorted(STRATEGIES))}'
        ) from None
