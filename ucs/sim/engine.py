"""Движок правил модели науки.

Автономный: Django не импортируется. Состояние группы — сериализуемый объект,
поэтому веб-интерфейс может быть без базы данных, а пакетный прогон — быстрым.

Порядок разрешения узла:

    вход на карточку -> метки on_enter
    -> лампа: приход гостя, окно хода «противодействовать»
    -> выбор группы (или безусловный переход)
    -> окно хода «замедлить»
    -> переход на следующую карточку

Гость разрешается до выбора: он может закрыть финал или снять вариант с блока
выбора, а изменить уже названный выбор — не может.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from .loader import (
    GUEST_COPY,
    GUEST_TAKE,
    GUEST_TAKE_FIRST_MARK,
    IF_BLOCKED_TAKE_PERSON,
    Node,
    Tree,
    get_tree,
)


class RuleError(Exception):
    """Действие противоречит правилам модели."""


class Phase(str, Enum):
    GUEST = 'guest'
    CHOICE = 'choice'
    FINAL = 'final'
    DONE = 'done'


# Чем можно заменить эффект «противодействия», если метка уже есть
SUBSTITUTE_SKIP_GUEST = 'skip_next_guest'
SUBSTITUTE_RETURN_MOVE = 'return_move'
SUBSTITUTE_ANY_MARK = 'any_mark'

JOHN_MOVES_TOTAL = 3
JOHN_MOVE_GAP = 2  # ход нельзя тратить в двух подряд идущих узлах


@dataclass
class Event:
    """Строка журнала прогона."""

    node: str
    kind: str
    text: str

    def __str__(self) -> str:
        return f'[{self.node}] {self.text}'


@dataclass
class GroupState:
    """Состояние одной группы. Полностью сериализуемо."""

    tree_id: str
    node: str | None
    marks: set[str] = field(default_factory=set)
    # Метки на входе в текущую карточку: блоки вариаций описывают то состояние,
    # в котором группа пришла, а не то, что осталось после on_enter. Без этого
    # группа со ШКАЛОЙ не услышала бы в 2.1 строку «число аннулировано».
    marks_on_entry: set[str] = field(default_factory=set)
    visited: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    closed_finals: set[str] = field(default_factory=set)
    disabled_choices: set[str] = field(default_factory=set)
    phase: Phase = Phase.GUEST
    guest_present: bool = False
    guest_resolved: bool = False
    john_moves_left: int = JOHN_MOVES_TOTAL
    john_last_move_index: int | None = None
    # Гость увёл человека: он выбывает на узел с этой позицией
    absent_from_index: int | None = None
    absent_is_john: bool = False
    absent_player: str | None = None
    suppress_next_guest: bool = False
    final: str | None = None
    floor_rule_fired: bool = False
    log: list[Event] = field(default_factory=list)

    # ------------------------------------------------------------ производные

    @property
    def node_index(self) -> int:
        """Позиция текущего узла в линейной последовательности пройденных."""
        return len(self.visited) - 1

    @property
    def finished(self) -> bool:
        return self.phase is Phase.DONE

    @property
    def player_absent(self) -> bool:
        """Проходит ли группа текущий узел неполным составом."""
        return self.absent_from_index == self.node_index

    def has(self, mark: str) -> bool:
        return mark in self.marks

    # ------------------------------------------------------- (де)сериализация

    def to_dict(self) -> dict:
        return {
            'tree_id': self.tree_id,
            'node': self.node,
            'marks': sorted(self.marks),
            'marks_on_entry': sorted(self.marks_on_entry),
            'visited': list(self.visited),
            'flags': sorted(self.flags),
            'closed_finals': sorted(self.closed_finals),
            'disabled_choices': sorted(self.disabled_choices),
            'phase': self.phase.value,
            'guest_present': self.guest_present,
            'guest_resolved': self.guest_resolved,
            'john_moves_left': self.john_moves_left,
            'john_last_move_index': self.john_last_move_index,
            'absent_from_index': self.absent_from_index,
            'absent_is_john': self.absent_is_john,
            'absent_player': self.absent_player,
            'suppress_next_guest': self.suppress_next_guest,
            'final': self.final,
            'floor_rule_fired': self.floor_rule_fired,
            'log': [[e.node, e.kind, e.text] for e in self.log],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> GroupState:
        return cls(
            tree_id=raw['tree_id'],
            node=raw['node'],
            marks=set(raw.get('marks') or ()),
            marks_on_entry=set(raw.get('marks_on_entry') or ()),
            visited=list(raw.get('visited') or ()),
            flags=set(raw.get('flags') or ()),
            closed_finals=set(raw.get('closed_finals') or ()),
            disabled_choices=set(raw.get('disabled_choices') or ()),
            phase=Phase(raw.get('phase', Phase.GUEST.value)),
            guest_present=bool(raw.get('guest_present')),
            guest_resolved=bool(raw.get('guest_resolved')),
            john_moves_left=int(raw.get('john_moves_left', JOHN_MOVES_TOTAL)),
            john_last_move_index=raw.get('john_last_move_index'),
            absent_from_index=raw.get('absent_from_index'),
            absent_is_john=bool(raw.get('absent_is_john')),
            absent_player=raw.get('absent_player'),
            suppress_next_guest=bool(raw.get('suppress_next_guest')),
            final=raw.get('final'),
            floor_rule_fired=bool(raw.get('floor_rule_fired')),
            log=[Event(*row) for row in raw.get('log') or ()],
        )


@dataclass(frozen=True)
class FinalStatus:
    key: str
    title: str
    available: bool
    reason: str
    suggested: bool


class Simulation:
    """Один прогон группы по дереву.

    Управляется извне: веб-интерфейс дёргает те же методы, что и пакетный
    прогон, поэтому правила описаны один раз.
    """

    def __init__(
        self,
        tree: Tree | None = None,
        state: GroupState | None = None,
        rng: random.Random | None = None,
        guest_probability: float = 1.0,
    ) -> None:
        self.tree = tree or get_tree()
        self.rng = rng or random.Random()
        self.guest_probability = guest_probability
        self._last_lamps = _last_lamp_nodes(self.tree)
        if state is None:
            self.state = GroupState(tree_id=self.tree.id, node=None)
            self._enter(self.tree.start)
        else:
            self.state = state

    # ------------------------------------------------------------------ чтение

    @property
    def node(self) -> Node:
        if self.state.node is None:
            raise RuleError('прогон завершён, текущего узла нет')
        return self.tree.node(self.state.node)

    def act(self) -> int:
        return self.node.act

    def active_choices(self):
        """Варианты выбора, доступные с учётом снятых гостем."""
        return [c for c in self.node.choices if c.key not in self.state.disabled_choices]

    def variations(self) -> list[tuple[str, list[str]]]:
        """Строки блоков вариаций, подходящие текущему состоянию."""
        out = []
        for block in self.node.variations:
            rows = [row for row in block.rows if self._row_matches(row)]
            if block.mode == 'first':
                rows = rows[:1]
            if rows:
                out.append((block.title, [row.text for row in rows]))
        return out

    def _row_matches(self, row) -> bool:
        entry_marks = self.state.marks_on_entry
        if row.if_mark:
            return row.if_mark in entry_marks
        if row.if_visited:
            return row.if_visited in self.state.visited
        if row.if_flag:
            return row.if_flag in self.state.flags
        if row.if_no_marks:
            return not entry_marks
        return True

    def can_john_move(self) -> bool:
        """Есть ли у Джона/Джейн право на ход в этом узле."""
        st = self.state
        if st.john_moves_left <= 0:
            return False
        if st.absent_is_john and st.player_absent:
            return False
        if st.john_last_move_index is not None:
            if st.node_index - st.john_last_move_index < JOHN_MOVE_GAP:
                return False
        return True

    def can_counter(self) -> bool:
        """«Противодействовать»: нужен гость, у которого есть что делать."""
        if not self.state.guest_present or self.state.guest_resolved:
            return False
        if not self.can_john_move():
            return False
        return self._guest_has_action()

    def can_slow(self) -> bool:
        """«Замедлить»: нужен блок выбора, не финальный, и узел не 2.1."""
        node = self.node
        if node.slow_forbidden or node.final_choice or not node.choices:
            return False
        return self.can_john_move()

    def is_last_lamp(self) -> bool:
        return self.state.node in self._last_lamps

    def counter_needs_substitute(self) -> bool:
        """Метка «при противодействии» уже есть — нужна замена эффекта."""
        counter = self.node.counter
        if counter is None:
            return False
        return all(self.state.has(m) for m in counter.marks_add)

    def substitute_options(self) -> list[str]:
        if not self.counter_needs_substitute():
            return []
        if self.is_last_lamp():
            return [SUBSTITUTE_ANY_MARK]
        return [SUBSTITUTE_SKIP_GUEST, SUBSTITUTE_RETURN_MOVE]

    def marks_not_held(self) -> list[str]:
        return [m for m in self.tree.marks if not self.state.has(m)]

    def slow_target(self, chosen_key: str) -> str | None:
        """Куда уводит «замедлить» выбор chosen_key."""
        node = self.node
        active = self.active_choices()
        if len(active) < 2:
            return None
        dead_end = next(
            (
                c
                for c in active
                if c.key != chosen_key and c.to and self.tree.node(c.to).is_dead_end
            ),
            None,
        )
        if dead_end is not None:
            return dead_end.key
        keys = [c.key for c in active]
        if chosen_key not in keys:
            return None
        return keys[(keys.index(chosen_key) + 1) % len(keys)]

    def finals_status(self) -> list[FinalStatus]:
        """Что доступно группе прямо сейчас. Чтение без побочных эффектов."""
        node = self.tree.node(self.state.node) if self.state.node else None
        suggested = {c.key for c in node.choices} if node and node.final_choice else set()
        out = []
        for key, final in self.tree.finals.items():
            if key in self.state.closed_finals:
                available, reason = False, 'закрыт указанием карточки'
            elif final.always_open:
                available, reason = True, 'открыт всегда'
            else:
                held = [m for m in final.opened_by if self.state.has(m)]
                available = bool(held)
                reason = (
                    'метка ' + ', '.join(held)
                    if held
                    else 'нет метки: ' + ' или '.join(final.opened_by)
                )
            out.append(
                FinalStatus(
                    key=key,
                    title=final.title,
                    available=available,
                    reason=reason,
                    suggested=key in suggested,
                )
            )
        return out

    def available_finals(self) -> list[str]:
        return [f.key for f in self.finals_status() if f.available]

    # ------------------------------------------------------------- управление

    def resolve_guest(
        self,
        counter: bool = False,
        substitute: str | None = None,
        chosen_mark: str | None = None,
        john_taken: bool | None = None,
    ) -> None:
        """Разрешить сцену гостя. Вызывается один раз на лампе.

        john_taken задаёт, кого гость увёл, если сработал щит РАСЧЁТА в 3.1:
        True — Джона/Джейн, False — любого из троих, None — решает жребий.
        """
        st = self.state
        if st.phase is not Phase.GUEST:
            raise RuleError(f'сцена гостя не открыта (фаза {st.phase.value})')
        if counter:
            if not self.can_counter():
                raise RuleError('ход «противодействовать» здесь недоступен')
            self._apply_counter(substitute, chosen_mark)
        elif st.guest_present:
            self._apply_guest(john_taken=john_taken)
        st.guest_resolved = True
        self._open_choice_phase()

    def apply_choice(self, key: str, john_slows: bool = False) -> None:
        """Назвать выбор и, при желании, потратить ход «замедлить»."""
        st = self.state
        if st.phase is not Phase.CHOICE:
            raise RuleError(f'блок выбора не открыт (фаза {st.phase.value})')
        node = self.node
        if key in st.disabled_choices:
            raise RuleError(f'вариант {key} снят гостем')
        choice = node.choice(key)

        if john_slows:
            if not self.can_slow():
                raise RuleError('ход «замедлить» здесь недоступен')
            target_key = self.slow_target(key)
            if target_key is None:
                raise RuleError('замедлять некуда: в узле нет второго варианта')
            self._spend_john_move()
            self._log(
                'john',
                f'ход Джона/Джейн «замедлить»: вариант {key} не сработал, '
                f'группа берёт {target_key}',
            )
            choice = node.choice(target_key)

        self._log('choice', f'выбор {choice.key}: {_short(choice.text)}')
        if choice.marks_add:
            self._grant(choice.marks_add, reason=choice.marks_reason)
        if choice.set_flag:
            st.flags.add(choice.set_flag)
        self._enter(choice.to)

    def advance(self) -> None:
        """Безусловный переход для узлов без блока выбора (1.3x, 2.3x)."""
        st = self.state
        if st.phase is not Phase.CHOICE:
            raise RuleError(f'переход недоступен (фаза {st.phase.value})')
        node = self.node
        if not node.next:
            raise RuleError(f'узел {node.id}: безусловного перехода нет')
        self._enter(node.next)

    def choose_final(self, key: str) -> None:
        st = self.state
        if st.phase is not Phase.FINAL:
            raise RuleError(f'выбор финала не открыт (фаза {st.phase.value})')
        if key not in self.available_finals():
            raise RuleError(f'финал {key} группе недоступен')
        st.final = key
        st.phase = Phase.DONE
        final = self.tree.final(key)
        self._log('final', f'финал {key} — {final.title}')

    # ------------------------------------------------------- внутренние шаги

    def _enter(self, node_id: str) -> None:
        st = self.state
        st.node = node_id
        st.visited.append(node_id)
        st.marks_on_entry = set(st.marks)
        st.disabled_choices = set()
        st.guest_present = False
        st.guest_resolved = False
        node = self.node

        self._log('enter', f'карточка «{node.title}»')
        self._apply_mark_effect(node)

        if node.lamp:
            self._log('lamp', 'группа зажигает лампу')
            if st.suppress_next_guest:
                st.suppress_next_guest = False
                self._log('guest', 'лампа зажглась, но гость не приходит (ход Джона)')
            elif self.rng.random() < self.guest_probability:
                st.guest_present = True
                self._log('guest', f'приходит гость, цель — {node.guest.item}')
            else:
                self._log('guest', 'гость не пришёл')
            st.phase = Phase.GUEST
            if not st.guest_present:
                st.guest_resolved = True
                self._open_choice_phase()
        else:
            st.guest_resolved = True
            self._open_choice_phase()

    def _open_choice_phase(self) -> None:
        st = self.state
        node = self.node
        if node.final_choice:
            self._apply_final_floor()
            st.phase = Phase.FINAL
        else:
            st.phase = Phase.CHOICE

    def _apply_mark_effect(self, node: Node) -> None:
        effect = node.on_enter
        for mark in effect.remove:
            if mark in self.state.marks:
                self.state.marks.discard(mark)
                self._log('mark', f'−{mark}')
        for grant in effect.add:
            new = [m for m in grant.grant if not self.state.has(m)]
            if new:
                self.state.marks.update(new)
                self._log('mark', '+' + ', +'.join(new))
            elif grant.else_marks:
                fallback = [m for m in grant.else_marks if not self.state.has(m)]
                if fallback:
                    self.state.marks.update(fallback)
                    reason = f' ({grant.else_reason})' if grant.else_reason else ''
                    self._log(
                        'mark',
                        f'{", ".join(grant.grant)} уже есть, иначе '
                        f'+{", +".join(fallback)}{reason}',
                    )
                else:
                    self._log(
                        'mark',
                        f'{", ".join(grant.grant)} и запасные уже есть — метка не идёт',
                    )
            else:
                self._log(
                    'mark', f'{", ".join(grant.grant)} уже есть — метка не складывается'
                )

    def _grant(self, marks, reason: str = '') -> None:
        new = [m for m in marks if not self.state.has(m)]
        if new:
            self.state.marks.update(new)
            tail = f' ({reason})' if reason else ''
            self._log('mark', '+' + ', +'.join(new) + tail)
        else:
            self._log('mark', f'{", ".join(marks)} уже есть — метка не складывается')

    def _guest_has_action(self) -> bool:
        guest = self.node.guest
        st = self.state
        if guest is None:
            return False
        if guest.mode == GUEST_TAKE_FIRST_MARK:
            if guest.blocked_by_mark and st.has(guest.blocked_by_mark):
                return guest.if_blocked == IF_BLOCKED_TAKE_PERSON
            return any(st.has(m) for m in guest.mark_order)
        if any(st.has(m) for m in guest.marks_remove):
            return True
        if any(f not in st.closed_finals for f in guest.closes_finals):
            return True
        if guest.disables_choice and guest.disables_choice not in st.disabled_choices:
            return True
        if guest.set_flag and guest.set_flag not in st.flags:
            return True
        return False

    def _apply_guest(self, john_taken: bool | None = None) -> None:
        guest = self.node.guest
        st = self.state
        if guest is None:
            return
        if not self._guest_has_action():
            self._log('guest', 'гостю нечего забрать — уходит ни с чем')
            return

        if guest.mode == GUEST_COPY:
            if guest.set_flag:
                st.flags.add(guest.set_flag)
            self._log('guest', f'гость снимает копию: {guest.item}, метка остаётся')
            return

        if guest.mode == GUEST_TAKE_FIRST_MARK:
            if guest.blocked_by_mark and st.has(guest.blocked_by_mark):
                self._take_person(guest, john_taken=john_taken)
                return
            for mark in guest.mark_order:
                if st.has(mark):
                    st.marks.discard(mark)
                    self._log('guest', f'гость уносит {guest.item} (−{mark})')
                    return
            return

        # GUEST_TAKE
        lost = [m for m in guest.marks_remove if st.has(m)]
        for mark in lost:
            st.marks.discard(mark)
        closed = [f for f in guest.closes_finals if f not in st.closed_finals]
        st.closed_finals.update(closed)
        if guest.disables_choice:
            st.disabled_choices.add(guest.disables_choice)

        parts = [f'гость уносит {guest.item}']
        if lost:
            parts.append('−' + ', −'.join(lost))
        if closed:
            parts.append('закрыт финал ' + ', '.join(closed))
        if guest.disables_choice:
            parts.append(f'вариант {guest.disables_choice} снят')
        self._log('guest', ' — '.join(parts))

    def _take_person(self, guest, john_taken: bool | None = None) -> None:
        """Щит РАСЧЁТА: гость не смог взять бумагу и уносит человека."""
        st = self.state
        if john_taken is None:
            john_taken = self.rng.random() < 0.25
        st.absent_is_john = john_taken
        st.absent_player = 'Джон/Джейн' if john_taken else 'один из троих'
        # Какой именно узел будет следующим, ещё неизвестно — привязываемся к его
        # позиции в последовательности.
        st.absent_from_index = st.node_index + 1
        self._log(
            'guest',
            f'{guest.blocked_by_mark} не даёт унести {guest.item}; '
            f'гость уносит человека ({st.absent_player}) — выбывает на следующий узел',
        )

    def _apply_counter(self, substitute: str | None, chosen_mark: str | None) -> None:
        st = self.state
        node = self.node
        counter = node.counter
        self._log('john', 'ход Джона/Джейн «противодействовать»: гость уходит ни с чем')
        if not self.counter_needs_substitute():
            self._spend_john_move()
            self._grant(counter.marks_add)
            return

        options = self.substitute_options()
        if substitute is None:
            raise RuleError(
                f'метка {", ".join(counter.marks_add)} уже есть — нужна замена: '
                f'{", ".join(options)}'
            )
        if substitute not in options:
            raise RuleError(f'замена {substitute!r} здесь недоступна: {options}')

        if substitute == SUBSTITUTE_RETURN_MOVE:
            self._log('john', 'метка уже есть — ход возвращается неистраченным')
            return
        if substitute == SUBSTITUTE_SKIP_GUEST:
            self._spend_john_move()
            st.suppress_next_guest = True
            self._log('john', 'метка уже есть — на следующей лампе гость не придёт')
            return
        # SUBSTITUTE_ANY_MARK: последняя лампа игры
        if chosen_mark is None:
            raise RuleError('последняя лампа: нужно назвать метку')
        if chosen_mark not in self.marks_not_held():
            raise RuleError(f'метка {chosen_mark} у группы уже есть')
        self._spend_john_move()
        st.marks.add(chosen_mark)
        self._log('john', f'последняя лампа: группа берёт метку +{chosen_mark}')

    def _spend_john_move(self) -> None:
        st = self.state
        st.john_moves_left -= 1
        st.john_last_move_index = st.node_index

    def _apply_final_floor(self) -> None:
        """Нижняя граница: у группы всегда есть выбор из двух финалов."""
        st = self.state
        available = self.available_finals()
        if len(available) != 1:
            return
        for rule in self.tree.floor:
            if rule.opens in st.closed_finals:
                continue
            if rule.opens in available:
                continue
            st.marks.add(rule.mark)
            st.floor_rule_fired = True
            self._log(
                'floor',
                f'доступен был один финал — группа получает +{rule.mark}, '
                f'открывается {rule.opens}',
            )
            return
        self._log('floor', 'доступен один финал, но открыть второй нечем')

    def _log(self, kind: str, text: str) -> None:
        self.state.log.append(Event(node=self.state.node or '—', kind=kind, text=text))


def _short(text: str, limit: int = 70) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _last_lamp_nodes(tree: Tree) -> set[str]:
    """Лампы, после которых лампы больше не будет."""
    memo: dict[str, bool] = {}

    def lamp_ahead(node_id: str) -> bool:
        if node_id in memo:
            return memo[node_id]
        memo[node_id] = False  # защита от циклов
        node = tree.node(node_id)
        result = any(
            tree.node(s).lamp or lamp_ahead(s) for s in tree.successors(node)
        )
        memo[node_id] = result
        return result

    return {n.id for n in tree.lamp_nodes() if not lamp_ahead(n.id)}
