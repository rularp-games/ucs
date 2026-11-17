from __future__ import annotations

from django.db import models
from django.utils import timezone

SYMBOL_CHOICES = [
    ('circle', 'круг'),
    ('square', 'квадрат'),
    ('triangle', 'треугольник'),
    ('cross', 'крест'),
    ('star', 'звезда'),
]
PARAM_CHOICES = [
    ('nav', 'Навигация'),
    ('move', 'Движение'),
    ('parts', 'Части'),
    ('fire', 'Огонь'),
]
PURCHASE_KINDS = [
    ('self', 'О себе'),
    ('flight', 'О звене'),
    ('program', 'О программе'),
    ('radio', 'Рация'),
]


class Game(models.Model):
    """Игровая сессия: одна на сеанс, у неё свой код входа и свои часы."""

    code = models.CharField('код входа', max_length=32, unique=True)
    title = models.CharField('название', max_length=200)
    content_name = models.CharField('контент', max_length=64, default='game')
    started_at = models.DateTimeField('старт', null=True, blank=True)
    clock_offset_seconds = models.IntegerField('сдвиг часов, с', default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.title} ({self.code})'

    @property
    def is_started(self) -> bool:
        return self.started_at is not None

    def game_minutes(self, now=None) -> float | None:
        """Игровое время в минутах от старта: now - started_at + сдвиг. None до старта."""
        if self.started_at is None:
            return None
        now = now or timezone.now()
        return (now - self.started_at).total_seconds() / 60 + self.clock_offset_seconds / 60


class Flight(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='flights')
    name = models.CharField('название', max_length=64)

    class Meta:
        ordering = ['id']

    def __str__(self) -> str:
        return self.name


class Player(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='players')
    flight = models.ForeignKey(Flight, on_delete=models.CASCADE, related_name='players')
    callsign = models.CharField('позывной', max_length=64)
    symbol = models.CharField('символ', max_length=16, choices=SYMBOL_CHOICES)
    is_imp = models.BooleanField('ИМП', default=False)
    character_id = models.CharField('персонаж', max_length=32)

    sync_nav = models.PositiveSmallIntegerField(default=0)
    sync_move = models.PositiveSmallIntegerField(default=0)
    sync_parts = models.PositiveSmallIntegerField(default=0)
    sync_fire = models.PositiveSmallIntegerField(default=0)

    cards = models.PositiveIntegerField('карты', default=0)
    radio_level = models.PositiveSmallIntegerField('рация', default=0)
    dossier_order = models.JSONField('порядок досье', default=list, blank=True)
    memory_index = models.PositiveSmallIntegerField(default=0)
    program_index = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['flight_id', 'id']
        constraints = [
            models.UniqueConstraint(fields=['game', 'callsign'], name='ls_unique_callsign_per_game'),
        ]

    def __str__(self) -> str:
        return self.callsign

    def sync_values(self) -> dict[str, int]:
        return {
            'nav': self.sync_nav,
            'move': self.sync_move,
            'parts': self.sync_parts,
            'fire': self.sync_fire,
        }

    def get_sync(self, param: str) -> int:
        return getattr(self, f'sync_{param}')

    def set_sync(self, param: str, value: int) -> None:
        setattr(self, f'sync_{param}', value)


class Token(models.Model):
    """Жетон: код с бумажки мастера, символ жетона и кто его ввёл."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='tokens')
    code = models.CharField(max_length=4)
    symbol = models.CharField(max_length=16, choices=SYMBOL_CHOICES)
    used_by = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL, related_name='tokens_used')
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['symbol', 'code']
        constraints = [
            models.UniqueConstraint(fields=['game', 'code'], name='ls_unique_token_code_per_game'),
        ]

    def __str__(self) -> str:
        return f'{self.code} ({self.symbol})'


class Purchase(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='purchases')
    kind = models.CharField(max_length=16, choices=PURCHASE_KINDS)
    payload = models.TextField('выдано', blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']


class RadioMessage(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='radio_messages')
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='radio_messages')
    text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']


class TaskAttempt(models.Model):
    """Журнал попыток для мастера: и задачи, и намеренные отказы."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='attempts')
    param = models.CharField(max_length=16, choices=PARAM_CHOICES)
    level = models.PositiveSmallIntegerField('sync на момент попытки', default=0)
    success = models.BooleanField(default=False)
    deviation = models.BooleanField('отказ', default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
