"""Значки символов и силуэтов целей: простой inline SVG без внешних файлов."""

from __future__ import annotations

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from ..loader import get_content

register = template.Library()

SYMBOL_SHAPES = {
    'circle': '<circle cx="12" cy="12" r="8"/>',
    'square': '<rect x="4" y="4" width="16" height="16"/>',
    'triangle': '<polygon points="12,4 21,20 3,20"/>',
    'cross': '<path d="M12 3v18M3 12h18"/>',
    'star': '<polygon points="12,2.5 14.9,9 22,9.6 16.6,14.2 18.3,21 12,17.4 5.7,21 7.4,14.2 2,9.6 9.1,9"/>',
}

# Силуэты рисуются в рамке 60×40, центр — (30, 20).
SILHOUETTES = {
    'fighter': '<polygon points="30,3 36,24 56,32 36,30 30,37 24,30 4,32 24,24"/>',
    'transport': '<rect x="8" y="12" width="40" height="16" rx="4"/><rect x="48" y="16" width="8" height="8"/>'
                 '<path d="M14 12v-5M24 12v-5M34 12v-5"/>',
    'module': '<rect x="22" y="10" width="16" height="20"/><path d="M6 14h16M6 26h16M38 14h16M38 26h16"/>'
              '<path d="M6 10v8M6 22v8M54 10v8M54 22v8"/>',
    'tug': '<rect x="14" y="14" width="22" height="14"/><path d="M36 21h18M54 16v10M8 18l6 3-6 3"/>',
    'probe': '<circle cx="24" cy="20" r="9"/><path d="M33 20h22M50 14l6 6-6 6M24 11V4M24 29v7"/>',
    'container': '<rect x="10" y="9" width="40" height="22"/><path d="M18 9v22M26 9v22M34 9v22M42 9v22"/>'
                 '<path d="M10 4h40"/>',
}


@register.simple_tag
def symbol_icon(key: str, size: int = 28):
    """Значок символа: круг, квадрат, треугольник, крест или звезда."""
    shape = SYMBOL_SHAPES.get(key, '')
    return format_html(
        '<svg class="sym" width="{0}" height="{0}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">{1}</svg>',
        size, mark_safe(shape),
    )


@register.simple_tag
def silhouette(key: str, rot: int = 0, css: str = 'sil'):
    """Силуэт цели в режиме «Опознание», повёрнутый на `rot` градусов вокруг центра.

    Рамка квадратная (60×60 вокруг центра силуэта), поэтому поворот на 90° не обрезает рисунок.
    """
    shape = SILHOUETTES.get(key, '')
    return format_html(
        '<svg class="{}" viewBox="0 -10 60 60" fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">'
        '<g transform="rotate({} 30 20)">{}</g></svg>',
        css, int(rot or 0), mark_safe(shape),
    )


@register.filter
def symbol_name(key: str) -> str:
    """Название символа по-русски из контента игры."""
    return get_content().symbols.get(key, key)
