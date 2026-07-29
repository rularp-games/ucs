"""Загрузка дерева прозрений из YAML и его проверка на целостность.

Модуль автономный: Django не импортируется, движок можно запускать и тестировать
как обычный Python.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent / 'data'
DEFAULT_TREE = 'false_vacuum'

KIND_NORMAL = 'normal'
KIND_DEAD_END = 'dead_end'
KIND_FALSE_EXIT = 'false_exit'
KINDS = (KIND_NORMAL, KIND_DEAD_END, KIND_FALSE_EXIT)

GUEST_TAKE = 'take'
GUEST_COPY = 'copy'
GUEST_TAKE_FIRST_MARK = 'take_first_mark'
GUEST_MODES = (GUEST_TAKE, GUEST_COPY, GUEST_TAKE_FIRST_MARK)

IF_BLOCKED_TAKE_PERSON = 'take_person'


class TreeError(Exception):
    """Дерево описано некорректно."""


@dataclass(frozen=True)
class MarkGrant:
    """Одно правило выдачи меток.

    Выдаются все метки из grant, которых у группы нет. Если ни одна из них не
    оказалась новой, выдаётся else_marks — это запись «иначе +МЕТКА» из карточки.
    """

    grant: tuple[str, ...]
    else_marks: tuple[str, ...] = ()
    else_reason: str = ''


@dataclass(frozen=True)
class MarkEffect:
    """Изменение меток при входе на карточку."""

    remove: tuple[str, ...] = ()
    add: tuple[MarkGrant, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.remove or self.add)


@dataclass(frozen=True)
class Choice:
    key: str
    text: str
    roles: tuple[str, ...] = ()
    development: str = ''
    to: str | None = None
    marks_add: tuple[str, ...] = ()
    marks_reason: str = ''
    set_flag: str | None = None


@dataclass(frozen=True)
class Guest:
    """Что гость делает на этой лампе."""

    item: str
    mode: str
    marks_remove: tuple[str, ...] = ()
    closes_finals: tuple[str, ...] = ()
    disables_choice: str | None = None
    set_flag: str | None = None
    mark_order: tuple[str, ...] = ()
    blocked_by_mark: str | None = None
    if_blocked: str | None = None
    note: str = ''
    blocked_note: str = ''
    if_blocked_note: str = ''
    master_note: str = ''


@dataclass(frozen=True)
class Counter:
    """Эффект хода «противодействовать»."""

    marks_add: tuple[str, ...] = ()
    note: str = ''


@dataclass(frozen=True)
class VariationRow:
    text: str
    if_mark: str | None = None
    if_visited: str | None = None
    if_flag: str | None = None
    if_no_marks: bool = False
    master_note: str = ''

    @property
    def unconditional(self) -> bool:
        return not (self.if_mark or self.if_visited or self.if_flag or self.if_no_marks)


@dataclass(frozen=True)
class VariationBlock:
    name: str
    title: str
    mode: str  # first | all
    rows: tuple[VariationRow, ...]


@dataclass(frozen=True)
class Node:
    id: str
    act: int
    slot: int
    minutes: int
    title: str
    kind: str
    lamp: bool
    summary: str = ''
    master_note: str = ''
    false_flash: bool = False
    slow_forbidden: bool = False
    slow_forbidden_reason: str = ''
    on_enter: MarkEffect = field(default_factory=MarkEffect)
    guest: Guest | None = None
    counter: Counter | None = None
    variations: tuple[VariationBlock, ...] = ()
    choices: tuple[Choice, ...] = ()
    next: str | None = None
    final_choice: bool = False

    @property
    def is_dead_end(self) -> bool:
        return self.kind == KIND_DEAD_END

    def choice(self, key: str) -> Choice:
        for choice in self.choices:
            if choice.key == key:
                return choice
        raise TreeError(f'узел {self.id}: нет варианта {key!r}')


@dataclass(frozen=True)
class Final:
    key: str
    title: str
    opened_by: tuple[str, ...]
    summary: str = ''

    @property
    def always_open(self) -> bool:
        return not self.opened_by


@dataclass(frozen=True)
class FloorRule:
    """Шаг правила нижней границы: метка и финал, который она открывает."""

    mark: str
    opens: str


@dataclass(frozen=True)
class Tree:
    # id — человекочитаемый идентификатор из meta, name — имя файла в data/.
    # Загрузчик ищет дерево по name, поэтому формы и ссылки передают именно его.
    id: str
    name: str
    title: str
    source: str
    marks: tuple[str, ...]
    mark_descriptions: dict[str, str]
    roles: dict[str, str]
    acts: dict[int, dict[str, str]]
    finals: dict[str, Final]
    floor: tuple[FloorRule, ...]
    start: str
    nodes: dict[str, Node]

    def node(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise TreeError(f'нет узла {node_id!r}') from None

    def final(self, key: str) -> Final:
        try:
            return self.finals[key]
        except KeyError:
            raise TreeError(f'нет финала {key!r}') from None

    def nodes_of_act(self, act: int) -> list[Node]:
        return [n for n in self.nodes.values() if n.act == act]

    def lamp_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.lamp]

    def successors(self, node: Node) -> list[str]:
        if node.final_choice:
            return []
        if node.next:
            return [node.next]
        return [c.to for c in node.choices if c.to]


# --------------------------------------------------------------------- разбор


def _tuple(raw, name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raise TreeError(f'{name}: ожидался список, получена строка {raw!r}')
    return tuple(str(item) for item in raw)


def _clean(raw) -> str:
    """YAML-блоки приходят с переводами строк — склеиваем в один абзац."""
    if raw is None:
        return ''
    return ' '.join(str(raw).split())


def _parse_mark_effect(raw: dict | None, node_id: str) -> MarkEffect:
    if not raw:
        return MarkEffect()
    grants = []
    for item in raw.get('add') or ():
        grant = _tuple(item.get('grant'), f'{node_id}.on_enter.add.grant')
        if not grant:
            raise TreeError(f'узел {node_id}: правило выдачи без grant')
        grants.append(
            MarkGrant(
                grant=grant,
                else_marks=_tuple(item.get('else'), f'{node_id}.on_enter.add.else'),
                else_reason=_clean(item.get('else_reason')),
            )
        )
    return MarkEffect(
        remove=_tuple(raw.get('remove'), f'{node_id}.on_enter.remove'),
        add=tuple(grants),
    )


def _parse_guest(raw: dict | None, node_id: str) -> Guest | None:
    if not raw:
        return None
    mode = raw.get('mode')
    if mode not in GUEST_MODES:
        raise TreeError(f'узел {node_id}: неизвестный режим гостя {mode!r}')
    return Guest(
        item=_clean(raw.get('item')),
        mode=mode,
        marks_remove=_tuple(raw.get('marks_remove'), f'{node_id}.guest.marks_remove'),
        closes_finals=_tuple(raw.get('closes_finals'), f'{node_id}.guest.closes_finals'),
        disables_choice=raw.get('disables_choice'),
        set_flag=raw.get('set_flag'),
        mark_order=_tuple(raw.get('mark_order'), f'{node_id}.guest.mark_order'),
        blocked_by_mark=raw.get('blocked_by_mark'),
        if_blocked=raw.get('if_blocked'),
        note=_clean(raw.get('note')),
        blocked_note=_clean(raw.get('blocked_note')),
        if_blocked_note=_clean(raw.get('if_blocked_note')),
        master_note=_clean(raw.get('master_note')),
    )


def _parse_variations(raw: dict | None, node_id: str) -> tuple[VariationBlock, ...]:
    if not raw:
        return ()
    blocks = []
    for name, block in raw.items():
        mode = block.get('mode', 'all')
        if mode not in ('first', 'all'):
            raise TreeError(f'узел {node_id}: режим вариаций {mode!r}')
        rows = tuple(
            VariationRow(
                text=_clean(row.get('text')),
                if_mark=row.get('if_mark'),
                if_visited=row.get('if_visited'),
                if_flag=row.get('if_flag'),
                if_no_marks=bool(row.get('if_no_marks')),
                master_note=_clean(row.get('master_note')),
            )
            for row in block.get('rows') or ()
        )
        blocks.append(
            VariationBlock(
                name=name,
                title=_clean(block.get('title')) or name,
                mode=mode,
                rows=rows,
            )
        )
    return tuple(blocks)


def _parse_node(node_id: str, raw: dict) -> Node:
    kind = raw.get('kind', KIND_NORMAL)
    if kind not in KINDS:
        raise TreeError(f'узел {node_id}: неизвестный kind {kind!r}')
    choices = tuple(
        Choice(
            key=str(item['key']),
            text=_clean(item.get('text')),
            roles=_tuple(item.get('roles'), f'{node_id}.choices.roles'),
            development=_clean(item.get('development')),
            to=item.get('to'),
            marks_add=_tuple(item.get('marks_add'), f'{node_id}.choices.marks_add'),
            marks_reason=_clean(item.get('marks_reason')),
            set_flag=item.get('set_flag'),
        )
        for item in raw.get('choices') or ()
    )
    return Node(
        id=node_id,
        act=int(raw['act']),
        slot=int(raw['slot']),
        minutes=int(raw['minutes']),
        title=_clean(raw.get('title')),
        kind=kind,
        lamp=bool(raw.get('lamp')),
        summary=_clean(raw.get('summary')),
        master_note=_clean(raw.get('master_note')),
        false_flash=bool(raw.get('false_flash')),
        slow_forbidden=bool(raw.get('slow_forbidden')),
        slow_forbidden_reason=_clean(raw.get('slow_forbidden_reason')),
        on_enter=_parse_mark_effect(raw.get('on_enter'), node_id),
        guest=_parse_guest(raw.get('guest'), node_id),
        counter=(
            Counter(
                marks_add=_tuple(
                    raw['counter'].get('marks_add'), f'{node_id}.counter.marks_add'
                ),
                note=_clean(raw['counter'].get('note')),
            )
            if raw.get('counter')
            else None
        ),
        variations=_parse_variations(raw.get('variations'), node_id),
        choices=choices,
        next=raw.get('next'),
        final_choice=bool(raw.get('final_choice')),
    )


def parse_tree(raw: dict, name: str = DEFAULT_TREE) -> Tree:
    meta = raw.get('meta') or {}
    marks = raw.get('marks') or {}
    finals = {
        key: Final(
            key=key,
            title=_clean(value.get('title')),
            opened_by=_tuple(value.get('opened_by'), f'finals.{key}.opened_by'),
            summary=_clean(value.get('summary')),
        )
        for key, value in (raw.get('finals') or {}).items()
    }
    tree = Tree(
        id=str(meta.get('id', name)),
        name=name,
        title=_clean(meta.get('title')),
        source=str(meta.get('source', '')),
        marks=tuple(marks),
        mark_descriptions={k: _clean(v) for k, v in marks.items()},
        roles={str(k): _clean(v) for k, v in (raw.get('roles') or {}).items()},
        acts={
            int(k): {ik: _clean(iv) for ik, iv in v.items()}
            for k, v in (raw.get('acts') or {}).items()
        },
        finals=finals,
        floor=tuple(
            FloorRule(mark=str(item['mark']), opens=str(item['opens']))
            for item in raw.get('final_floor') or ()
        ),
        start=str(raw['start']),
        nodes={
            node_id: _parse_node(node_id, node_raw)
            for node_id, node_raw in (raw.get('nodes') or {}).items()
        },
    )
    validate(tree)
    return tree


# ------------------------------------------------------------------- проверки


def validate(tree: Tree) -> None:
    """Проверяет дерево на внутреннюю согласованность.

    Ловит опечатки в названиях меток и узлов, недостижимые карточки, лампы без
    гостя и обратное, узлы одновременно с выбором и с next.
    """
    problems: list[str] = []
    known_marks = set(tree.marks)
    known_finals = set(tree.finals)

    if tree.start not in tree.nodes:
        problems.append(f'start={tree.start!r} — такого узла нет')

    for rule in tree.floor:
        if rule.mark not in known_marks:
            problems.append(f'final_floor: неизвестная метка {rule.mark!r}')
        if rule.opens not in known_finals:
            problems.append(f'final_floor: неизвестный финал {rule.opens!r}')

    for final in tree.finals.values():
        for mark in final.opened_by:
            if mark not in known_marks:
                problems.append(f'финал {final.key}: неизвестная метка {mark!r}')

    for node in tree.nodes.values():
        problems.extend(_validate_node(node, tree, known_marks, known_finals))

    problems.extend(_validate_reachability(tree))
    problems.extend(_validate_marks_reachable(tree, known_marks))

    if problems:
        raise TreeError('дерево некорректно:\n  - ' + '\n  - '.join(problems))


def _validate_node(node: Node, tree: Tree, known_marks, known_finals) -> list[str]:
    problems: list[str] = []
    where = f'узел {node.id}'

    if node.next and node.choices:
        problems.append(f'{where}: одновременно next и choices')
    if not node.next and not node.choices:
        problems.append(f'{where}: ни next, ни choices')
    if node.next and node.next not in tree.nodes:
        problems.append(f'{where}: next={node.next!r} — такого узла нет')

    keys = [c.key for c in node.choices]
    if len(keys) != len(set(keys)):
        problems.append(f'{where}: повторяющиеся ключи вариантов {keys}')

    for choice in node.choices:
        prefix = f'{where}, вариант {choice.key}'
        if node.final_choice:
            if choice.key not in known_finals:
                problems.append(f'{prefix}: неизвестный финал')
            if choice.to:
                problems.append(f'{prefix}: у выбора финала не должно быть to')
        else:
            if not choice.to:
                problems.append(f'{prefix}: нет to')
            elif choice.to not in tree.nodes:
                problems.append(f'{prefix}: to={choice.to!r} — такого узла нет')
        for mark in choice.marks_add:
            if mark not in known_marks:
                problems.append(f'{prefix}: неизвестная метка {mark!r}')
        for role in choice.roles:
            if role not in tree.roles:
                problems.append(f'{prefix}: неизвестная роль {role!r}')

    for mark in node.on_enter.remove:
        if mark not in known_marks:
            problems.append(f'{where}: on_enter.remove — неизвестная метка {mark!r}')
    for grant in node.on_enter.add:
        for mark in grant.grant + grant.else_marks:
            if mark not in known_marks:
                problems.append(f'{where}: on_enter.add — неизвестная метка {mark!r}')

    if node.lamp and node.guest is None:
        problems.append(f'{where}: лампа есть, а гость не описан')
    if node.guest is not None and not node.lamp:
        problems.append(f'{where}: гость описан, а лампы нет')
    if node.lamp and node.counter is None:
        problems.append(f'{where}: лампа есть, а «при противодействии» не описано')

    if node.guest is not None:
        problems.extend(_validate_guest(node, known_marks, known_finals))

    if node.counter is not None:
        for mark in node.counter.marks_add:
            if mark not in known_marks:
                problems.append(f'{where}: counter — неизвестная метка {mark!r}')

    for block in node.variations:
        for row in block.rows:
            if row.if_mark and row.if_mark not in known_marks:
                problems.append(
                    f'{where}: вариация {block.name} — неизвестная метка {row.if_mark!r}'
                )
            if row.if_visited and row.if_visited not in tree.nodes:
                problems.append(
                    f'{where}: вариация {block.name} — нет узла {row.if_visited!r}'
                )
        if block.mode == 'first' and not any(r.unconditional for r in block.rows):
            problems.append(
                f'{where}: вариация {block.name} с mode=first без безусловной строки'
            )

    return problems


def _validate_guest(node: Node, known_marks, known_finals) -> list[str]:
    problems: list[str] = []
    guest = node.guest
    where = f'узел {node.id}, гость'

    for mark in guest.marks_remove + guest.mark_order:
        if mark not in known_marks:
            problems.append(f'{where}: неизвестная метка {mark!r}')
    if guest.blocked_by_mark and guest.blocked_by_mark not in known_marks:
        problems.append(f'{where}: неизвестная метка {guest.blocked_by_mark!r}')
    for key in guest.closes_finals:
        if key not in known_finals:
            problems.append(f'{where}: неизвестный финал {key!r}')
    if guest.disables_choice and guest.disables_choice not in [
        c.key for c in node.choices
    ]:
        problems.append(f'{where}: disables_choice={guest.disables_choice!r} — нет варианта')
    if guest.mode == GUEST_TAKE_FIRST_MARK and not guest.mark_order:
        problems.append(f'{where}: режим take_first_mark без mark_order')
    if guest.if_blocked and guest.if_blocked != IF_BLOCKED_TAKE_PERSON:
        problems.append(f'{where}: неизвестное if_blocked={guest.if_blocked!r}')
    if guest.blocked_by_mark and not guest.if_blocked:
        problems.append(f'{where}: есть blocked_by_mark, но нет if_blocked')
    return problems


def _validate_reachability(tree: Tree) -> list[str]:
    seen = set()
    stack = [tree.start]
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in tree.nodes:
            continue
        seen.add(node_id)
        stack.extend(tree.successors(tree.nodes[node_id]))
    unreachable = sorted(set(tree.nodes) - seen)
    if unreachable:
        return [f'недостижимые узлы: {", ".join(unreachable)}']
    return []


def _validate_marks_reachable(tree: Tree, known_marks) -> list[str]:
    """Каждая метка должна где-то выдаваться, иначе она мертва."""
    granted = set()
    for node in tree.nodes.values():
        for grant in node.on_enter.add:
            granted.update(grant.grant)
            granted.update(grant.else_marks)
        for choice in node.choices:
            granted.update(choice.marks_add)
        if node.counter:
            granted.update(node.counter.marks_add)
    for rule in tree.floor:
        granted.add(rule.mark)
    dead = sorted(set(known_marks) - granted)
    if dead:
        return [f'метки, которые нигде не выдаются: {", ".join(dead)}']
    return []


# --------------------------------------------------------------------- доступ


def load_tree(name: str = DEFAULT_TREE, data_dir: Path | None = None) -> Tree:
    path = (data_dir or DATA_DIR) / f'{name}.yaml'
    if not path.exists():
        raise TreeError(f'нет файла дерева: {path}')
    with path.open(encoding='utf-8') as fh:
        return parse_tree(yaml.safe_load(fh), name=name)


@functools.lru_cache(maxsize=8)
def get_tree(name: str = DEFAULT_TREE) -> Tree:
    """Кешированная загрузка: дерево неизменяемо, читать файл каждый раз незачем."""
    return load_tree(name)


def available_trees(data_dir: Path | None = None) -> list[str]:
    return sorted(p.stem for p in (data_dir or DATA_DIR).glob('*.yaml'))
