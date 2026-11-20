"""Отрисовка схемы дерева.

Два представления одной и той же структуры, оба строятся из дерева, а не из
отдельного описания, — разойтись с YAML они не могут.

* `render_svg` — inline SVG для страницы. Без внешних библиотек: в кластере нет
  интернета, а вендорить в образ мегабайты JS ради одной картинки не нужно.
  Умеет подсвечивать состояние прогона: пройденный путь и текущую карточку.
* `render_mermaid` — исходник Mermaid, чтобы ту же схему можно было вставить в
  документ модели.

Раскладка тривиальна, потому что дерево слоистое: узлы сгруппированы по парам
(акт, позиция), позиция задаёт ряд, узлы внутри позиции расходятся по столбцам.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from .loader import KIND_DEAD_END, KIND_FALSE_EXIT, Tree

# Ширина подобрана так, чтобы схема одного акта (максимум 4 столбца) влезала в
# основную колонку страницы без горизонтальной прокрутки.
NODE_W = 172
NODE_H = 48
COL_GAP = 14
ROW_GAP = 54
BAND_GAP = 34
MARGIN_X = 18
MARGIN_TOP = 16
MARGIN_BOTTOM = 18
BAND_LABEL_H = 20

FINALS_RANK = 'finals'


@dataclass
class Box:
    """Прямоугольник карточки на схеме."""

    key: str
    line1: str
    line2: str
    x: float
    y: float
    kind: str = ''
    lamp: bool = False
    is_final: bool = False
    visited: bool = False
    current: bool = False
    chosen: bool = False
    unavailable: bool = False
    title: str = ''

    @property
    def cx(self) -> float:
        return self.x + NODE_W / 2

    @property
    def bottom(self) -> float:
        return self.y + NODE_H


@dataclass
class Edge:
    src: str
    dst: str
    active: bool = False


@dataclass
class Band:
    label: str
    y: float


@dataclass
class Layout:
    width: float
    height: float
    boxes: dict[str, Box] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)


# ------------------------------------------------------------------ раскладка


def _ranks(tree: Tree, acts: tuple[int, ...]) -> list[tuple[int | None, int, list[str]]]:
    """Ряды схемы: (акт, позиция, узлы). Последний ряд — финалы."""
    groups: dict[tuple[int, int], list[str]] = {}
    for node in tree.nodes.values():
        if node.act in acts:
            groups.setdefault((node.act, node.slot), []).append(node.id)
    out = [
        (act, slot, sorted(groups[(act, slot)]))
        for act, slot in sorted(groups)
    ]
    if max(acts) == max(n.act for n in tree.nodes.values()):
        out.append((None, 0, list(tree.finals)))
    return out


def build_layout(
    tree: Tree,
    acts: tuple[int, ...] = (1, 2, 3),
    state=None,
    available_finals: tuple[str, ...] | None = None,
) -> Layout:
    ranks = _ranks(tree, acts)
    columns = max(len(ids) for _, _, ids in ranks)
    width = MARGIN_X * 2 + columns * NODE_W + (columns - 1) * COL_GAP

    visited = list(getattr(state, 'visited', []) or [])
    current = getattr(state, 'node', None)
    chosen_final = getattr(state, 'final', None)
    visited_set = set(visited)
    # Рёбра пройденного пути — только между подряд идущими узлами прогона
    walked = set(zip(visited, visited[1:]))
    if chosen_final and visited:
        walked.add((visited[-1], chosen_final))

    layout = Layout(width=width, height=0)
    y = MARGIN_TOP
    last_act = None

    for act, slot, ids in ranks:
        if act != last_act:
            label = (
                f'Акт {act} — {tree.acts.get(act, {}).get("title", "")}'
                if act is not None
                else 'Финалы'
            )
            if last_act is not None:
                y += BAND_GAP
            layout.bands.append(Band(label=label, y=y))
            y += BAND_LABEL_H
            last_act = act

        span = len(ids) * NODE_W + (len(ids) - 1) * COL_GAP
        x = (width - span) / 2
        for node_id in ids:
            layout.boxes[node_id] = _make_box(
                tree, node_id, x, y,
                is_final=act is None,
                visited=node_id in visited_set,
                current=node_id == current,
                chosen=node_id == chosen_final,
                available_finals=available_finals,
            )
            x += NODE_W + COL_GAP
        y += NODE_H + ROW_GAP

    layout.height = y - ROW_GAP + MARGIN_BOTTOM

    for node_id, box in layout.boxes.items():
        if box.is_final:
            continue
        node = tree.node(node_id)
        targets = (
            [c.key for c in node.choices]
            if node.final_choice
            else tree.successors(node)
        )
        for target in targets:
            if target in layout.boxes:
                layout.edges.append(
                    Edge(
                        src=node_id,
                        dst=target,
                        active=(node_id, target) in walked,
                    )
                )
    return layout


def _make_box(
    tree: Tree,
    node_id: str,
    x: float,
    y: float,
    is_final: bool,
    visited: bool,
    current: bool,
    chosen: bool,
    available_finals,
) -> Box:
    if is_final:
        final = tree.final(node_id)
        unavailable = (
            available_finals is not None and node_id not in available_finals
        )
        return Box(
            key=node_id,
            line1=node_id,
            line2=final.title,
            x=x,
            y=y,
            is_final=True,
            chosen=chosen,
            unavailable=unavailable,
            title=(
                'открыт всегда'
                if final.always_open
                else 'метка ' + ' или '.join(final.opened_by)
            ),
        )
    node = tree.node(node_id)
    return Box(
        key=node_id,
        line1=node_id,
        line2=node.title,
        x=x,
        y=y,
        kind=node.kind,
        lamp=node.lamp,
        visited=visited,
        current=current,
        title=f'{node.minutes} мин. {node.summary[:160]}',
    )


# ----------------------------------------------------------------------- SVG


def _edge_path(src: Box, dst: Box) -> str:
    x1, y1 = src.cx, src.bottom
    x2, y2 = dst.cx, dst.y
    mid = (y1 + y2) / 2
    return f'M {x1:.0f} {y1:.0f} C {x1:.0f} {mid:.0f}, {x2:.0f} {mid:.0f}, {x2:.0f} {y2:.0f}'


def _box_class(box: Box) -> str:
    parts = ['n']
    if box.is_final:
        parts.append('fin')
        if box.chosen:
            parts.append('chosen')
        if box.unavailable:
            parts.append('off')
    else:
        if box.kind == KIND_DEAD_END:
            parts.append('dead')
        elif box.kind == KIND_FALSE_EXIT:
            parts.append('false')
        if box.visited:
            parts.append('seen')
        if box.current:
            parts.append('now')
    return ' '.join(parts)


def render_svg(layout: Layout, title: str = 'Схема дерева') -> str:
    """Собрать SVG. Стили внутри документа, внешних файлов не требуется."""
    out: list[str] = [
        f'<svg class="tree" viewBox="0 0 {layout.width:.0f} {layout.height:.0f}" '
        f'width="{layout.width:.0f}" height="{layout.height:.0f}" '
        f'role="img" aria-label={quoteattr(title)} '
        'xmlns="http://www.w3.org/2000/svg">',
        '<defs>'
        '<marker id="ah" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 6 4 L 0 7 z" class="ahead"/></marker>'
        '<marker id="ah-on" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 6 4 L 0 7 z" class="ahead on"/></marker>'
        '</defs>',
    ]

    for band in layout.bands:
        out.append(
            f'<text class="band" x="{MARGIN_X}" y="{band.y + 13:.0f}">'
            f'{escape(band.label)}</text>'
        )

    # Сначала обычные рёбра, потом активные — чтобы путь читался поверх
    for edge in sorted(layout.edges, key=lambda e: e.active):
        src, dst = layout.boxes[edge.src], layout.boxes[edge.dst]
        marker = 'ah-on' if edge.active else 'ah'
        cls = 'e on' if edge.active else 'e'
        # fill="none" презентационным атрибутом, а не только в CSS: если стили
        # не применятся, кривые иначе зальются сплошным цветом
        out.append(
            f'<path class="{cls}" fill="none" d="{_edge_path(src, dst)}" '
            f'marker-end="url(#{marker})"/>'
        )

    for box in layout.boxes.values():
        out.append(f'<g class={quoteattr(_box_class(box))}>')
        if box.title:
            out.append(f'<title>{escape(box.title)}</title>')
        out.append(
            f'<rect x="{box.x:.0f}" y="{box.y:.0f}" width="{NODE_W}" '
            f'height="{NODE_H}" rx="5"/>'
        )
        out.append(
            f'<text class="id" x="{box.x + 9:.0f}" y="{box.y + 19:.0f}">'
            f'{escape(box.line1)}</text>'
        )
        if box.lamp:
            out.append(
                f'<circle class="lamp" cx="{box.x + NODE_W - 11:.0f}" '
                f'cy="{box.y + 14:.0f}" r="4"/>'
            )
        out.append(
            f'<text class="ttl" x="{box.x + 9:.0f}" y="{box.y + 36:.0f}">'
            f'{escape(_clip(box.line2))}</text>'
        )
        out.append('</g>')

    out.append('</svg>')
    return ''.join(out)


def _clip(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + '…'


# ------------------------------------------------------------------- Mermaid


_MERMAID_KIND = {
    KIND_DEAD_END: 'ТУПИК',
    KIND_FALSE_EXIT: 'ложный выход',
}


def render_mermaid(tree: Tree, act: int) -> str:
    """Исходник Mermaid для одного акта — для вставки в документ модели."""
    lines = ['flowchart TD']
    nodes = sorted(tree.nodes_of_act(act), key=lambda n: (n.slot, n.id))

    for node in nodes:
        tags = []
        if _MERMAID_KIND.get(node.kind):
            tags.append(_MERMAID_KIND[node.kind])
        if node.lamp:
            tags.append('ЛАМПА')
        label = f'{node.id} {node.title}'
        if tags:
            label += '<br>' + ', '.join(tags)
        lines.append(f'    {_mid(node.id)}["{label}"]')

    last_act = max(n.act for n in tree.nodes.values())
    if act == last_act:
        for key, final in tree.finals.items():
            lines.append(f'    {_mid(key)}["{key} {final.title}"]')
    else:
        nxt = f'{act + 1}.1'
        lines.append(f'    NEXT["к {nxt}"]')

    lines.append('')
    for node in nodes:
        targets = (
            [c.key for c in node.choices]
            if node.final_choice
            else tree.successors(node)
        )
        for target in targets:
            if target in tree.nodes and tree.node(target).act != act:
                lines.append(f'    {_mid(node.id)} --> NEXT')
            else:
                lines.append(f'    {_mid(node.id)} --> {_mid(target)}')
    return '\n'.join(lines)


def _mid(node_id: str) -> str:
    """Идентификатор для Mermaid: точки в именах узлов парсер не любит."""
    if '.' not in node_id:
        return node_id
    return 'N' + node_id.replace('.', '_')
