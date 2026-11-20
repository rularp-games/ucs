from __future__ import annotations

from datetime import timedelta

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
    ('archive', 'Архив'),
    ('radio', 'Рация'),
]
ARCHIVE_VIA = [
    ('code', 'код'),
    ('card', 'карта'),
]
TOKEN_TTL = timedelta(minutes=20)  # жетон, который никто не ввёл за этот срок, сгорает


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
    wrong_codes = models.PositiveSmallIntegerField('неверных кодов подряд', default=0)
    codes_blocked_until = models.DateTimeField('ввод кодов закрыт до', null=True, blank=True)

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


class DarkEntry(models.Model):
    """Группа, которую мастер отправил в темноту: каждому в ней выданы жетоны."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='dark_entries')
    created_at = models.DateTimeField(default=timezone.now)
    players = models.ManyToManyField(Player, related_name='dark_entries')

    class Meta:
        ordering = ['-created_at', '-id']


class Token(models.Model):
    """Жетон: четырёхзначный код и символ, выданный игроку в темноте.

    Состояния: ждёт (не введён, срок не вышел), совпал (ввёл игрок с тем же символом),
    сгорел (ввёл игрок с другим символом), просрочен (срок вышел, никто не ввёл).
    """

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='tokens')
    code = models.CharField(max_length=4)
    symbol = models.CharField(max_length=16, choices=SYMBOL_CHOICES)
    issued_to = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL, related_name='tokens_issued')
    issued_at = models.DateTimeField(default=timezone.now)
    dark_entry = models.ForeignKey(DarkEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name='tokens')
    used_by = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL, related_name='tokens_used')
    used_at = models.DateTimeField(null=True, blank=True)
    burned = models.BooleanField('сгорел при вводе', default=False)

    class Meta:
        ordering = ['-issued_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['game', 'code'], name='ls_unique_token_code_per_game'),
        ]

    def __str__(self) -> str:
        return f'{self.code} ({self.symbol})'

    @property
    def expires_at(self):
        return self.issued_at + TOKEN_TTL

    def is_expired(self, now=None) -> bool:
        return (now or timezone.now()) >= self.expires_at

    def is_valid(self, now=None) -> bool:
        """Можно ли ещё ввести жетон: никто не вводил и срок не вышел."""
        return self.used_by_id is None and not self.burned and not self.is_expired(now)

    def state(self, now=None) -> str:
        if self.used_by_id is not None:
            return 'burned' if self.burned else 'matched'
        return 'expired' if self.is_expired(now) else 'waiting'


class ArchiveOpen(models.Model):
    """Запись архива, открытая игроком: кодом или картой."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='archive_opens')
    code = models.CharField(max_length=4)
    via = models.CharField(max_length=8, choices=ARCHIVE_VIA)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['player', 'code'], name='ls_unique_archive_open_per_player'),
        ]


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
