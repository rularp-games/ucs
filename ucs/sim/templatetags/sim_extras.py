"""Мелкие фильтры для шаблонов сводки: доли в проценты и в ширину полосы."""

from django import template

register = template.Library()

BAR_WIDTH_PX = 190


@register.filter
def percent(share) -> str:
    """0.4267 -> «42.7%»."""
    try:
        return f'{float(share) * 100:.1f}%'
    except (TypeError, ValueError):
        return '—'


@register.filter
def dictkey(mapping, key):
    """Взять значение словаря по ключу-переменной: шаблоны сами так не умеют."""
    try:
        return mapping.get(key, '')
    except AttributeError:
        return ''


@register.filter
def barpx(share) -> int:
    """Ширина полосы в пикселях; ненулевая доля всегда видна."""
    try:
        value = float(share)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return max(1, round(value * BAR_WIDTH_PX))
