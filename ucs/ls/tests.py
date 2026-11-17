"""Тесты приложения ls: правила, генераторы задач, жетоны, карты, страницы."""

from __future__ import annotations

import random
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from . import engine, services
from .loader import ContentError, get_content, parse_content
from .models import Game, Player, Purchase, RadioMessage, TaskAttempt, Token


def make_game(flights: int = 1, started_minutes_ago: float | None = None) -> Game:
    game = services.create_game('Тест', 'TEST', 'game', flights, rng=random.Random(1))
    if started_minutes_ago is not None:
        game.started_at = timezone.now() - timedelta(minutes=started_minutes_ago)
        game.save()
    return game


def login(client, game: Game, player: Player) -> None:
    response = client.post(reverse('ls:login'), {'code': game.code, 'callsign': player.callsign})
    assert response.status_code == 302, 'вход не удался'


def first_player(game: Game, is_imp: bool = False) -> Player:
    return game.players.filter(is_imp=is_imp).order_by('id').first()


# --- Движок: цепочка и уровни -----------------------------------------------

class SyncRulesTests(SimpleTestCase):
    def test_chain_opening(self):
        self.assertTrue(engine.is_open({'nav': 0}, 'nav'))
        self.assertFalse(engine.is_open({'nav': 25}, 'move'))
        self.assertTrue(engine.is_open({'nav': 50}, 'move'))
        self.assertFalse(engine.is_open({'nav': 100, 'move': 25}, 'fire'))

    def test_open_param_stays_open_after_previous_drops(self):
        self.assertTrue(engine.is_open({'nav': 25, 'move': 25}, 'move'))
        self.assertFalse(engine.is_open({'nav': 25, 'move': 0}, 'move'))

    def test_modes(self):
        sync = {'nav': 25}
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_INSTRUCTION)
        sync['nav'] = 50
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_FULL)
        sync['nav'] = 75
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_SHORT)
        sync['nav'] = 100
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_AUTO)
        self.assertEqual(engine.mode_of({'nav': 25}, 'move'), engine.MODE_CLOSED)

    def test_steps_are_clamped(self):
        self.assertEqual(engine.after_success(100), 100)
        self.assertEqual(engine.after_success(0), 25)
        self.assertEqual(engine.after_deviation(0), 0)
        self.assertEqual(engine.after_deviation(50), 25)


# --- Движок: четыре модели --------------------------------------------------

class NavModelTests(SimpleTestCase):
    def test_correct_and_wrong_answers(self):
        for seed in range(30):
            task = engine.gen_nav(seed, engine.MODE_INSTRUCTION)
            expected = engine.nav_expected(task)
            self.assertEqual(len(expected), 4)
            self.assertEqual(len(set(expected)), 4)
            self.assertTrue(engine.check_nav(task, expected))
            self.assertTrue(engine.check_nav(task, ','.join(expected).lower()))
            self.assertFalse(engine.check_nav(task, list(reversed(expected))))
            self.assertFalse(engine.check_nav(task, expected[:-1]))
            self.assertFalse(engine.check_nav(task, []))

    def test_short_variant_needs_only_exit(self):
        task = engine.gen_nav(5, engine.MODE_SHORT)
        self.assertEqual(task['beacons'], [])
        self.assertTrue(engine.check_nav(task, [task['exit']]))
        self.assertFalse(engine.check_nav(task, ['A1', 'A2']))

    def test_cell_label(self):
        self.assertEqual(engine.cell_label(0, 2), 'A3')
        self.assertEqual(engine.cell_label(10, 1), 'K2')


class MoveModelTests(SimpleTestCase):
    def test_success_requires_ok_and_elapsed_time(self):
        task = engine.gen_move(1, engine.MODE_FULL, now=1000.0)
        self.assertEqual(task['seconds'], 30)
        self.assertTrue(engine.check_move(task, 'ok', 30.2, 1031.0))
        self.assertFalse(engine.check_move(task, 'fail', 30.2, 1031.0))

    def test_too_fast_is_a_failure(self):
        task = engine.gen_move(1, engine.MODE_FULL, now=1000.0)
        self.assertFalse(engine.check_move(task, 'ok', 30.0, 1005.0))  # сервер: прошло 5 с
        self.assertFalse(engine.check_move(task, 'ok', 5.0, 1031.0))   # клиент: слишком мало

    def test_short_variant_is_half(self):
        task = engine.gen_move(1, engine.MODE_SHORT, now=0.0)
        self.assertEqual(task['seconds'], 15)
        self.assertTrue(engine.check_move(task, 'ok', 15.0, 16.0))


class PartsModelTests(SimpleTestCase):
    @staticmethod
    def solve(task: dict) -> list[bool]:
        state = list(task['initial'])
        for item in task['items']:
            if item['switch'] is not None:
                state[item['switch']] = item['value']
        return state

    def test_correct_state_passes(self):
        for seed in range(40):
            task = engine.gen_parts(seed, engine.MODE_INSTRUCTION, 25)
            self.assertEqual(len(task['items']), 5)
            self.assertTrue(engine.check_parts(task, self.solve(task)))

    def test_touching_other_switch_fails(self):
        task = engine.gen_parts(3, engine.MODE_INSTRUCTION, 25)
        touched = {item['switch'] for item in task['items']}
        other = next(i for i in range(len(engine.SWITCHES)) if i not in touched)
        state = self.solve(task)
        state[other] = not state[other]
        self.assertFalse(engine.check_parts(task, state))

    def test_unchecked_item_fails(self):
        task = engine.gen_parts(4, engine.MODE_INSTRUCTION, 25)
        state = self.solve(task)
        index = task['items'][0]['switch']
        state[index] = not state[index]
        self.assertFalse(engine.check_parts(task, state))

    def test_phantom_only_from_sync_50_and_is_ignored(self):
        self.assertFalse(any(engine.gen_parts(s, engine.MODE_INSTRUCTION, 25)['phantom'] for s in range(60)))
        tasks = [engine.gen_parts(s, engine.MODE_FULL, 50) for s in range(60)]
        phantoms = [t for t in tasks if t['phantom']]
        self.assertTrue(phantoms)
        self.assertTrue(len(phantoms) < len(tasks))
        for task in phantoms:
            self.assertEqual(len(task['items']), 5)
            self.assertEqual(sum(1 for i in task['items'] if i['switch'] is None), 1)
            self.assertTrue(engine.check_parts(task, self.solve(task)))

    def test_short_variant(self):
        task = engine.gen_parts(8, engine.MODE_SHORT, 75)
        self.assertEqual(len(task['items']), 3)
        real = [i for i in task['items'] if i['switch'] is not None]
        already = [i for i in real if task['initial'][i['switch']] == i['value']]
        self.assertTrue(already)
        self.assertTrue(engine.check_parts(task, self.solve(task)))

    def test_answer_parsing(self):
        self.assertEqual(engine.parse_parts_answer('10101010')[0], True)
        self.assertIsNone(engine.parse_parts_answer('1010'))
        self.assertIsNone(engine.parse_parts_answer('1010101x'))


class FireModelTests(SimpleTestCase):
    def test_exactly_one_target_matches(self):
        for seed in range(200):
            task = engine.gen_fire(seed, engine.MODE_INSTRUCTION)
            self.assertEqual(len(task['targets']), 6)
            matches = [t for t in task['targets'] if engine.target_matches(task['criteria'], t)]
            self.assertEqual(len(matches), 1, seed)

    def test_right_and_wrong_choice(self):
        task = engine.gen_fire(11, engine.MODE_FULL)
        right = engine.fire_expected(task)
        self.assertTrue(engine.check_fire(task, right))
        wrong = str((int(right) + 1) % 6)
        self.assertFalse(engine.check_fire(task, wrong))

    def test_civilian_silhouette_can_match(self):
        found = False
        for seed in range(100):
            task = engine.gen_fire(seed, engine.MODE_FULL)
            hit = task['targets'][int(engine.fire_expected(task))]
            if hit['shape'] in engine.CIVILIAN_SHAPES:
                found = True
        self.assertTrue(found)

    def test_short_variant_confirm_reject(self):
        seen = set()
        for seed in range(60):
            task = engine.gen_fire(seed, engine.MODE_SHORT)
            self.assertEqual(len(task['targets']), 1)
            expected = engine.fire_expected(task)
            seen.add(expected)
            other = 'reject' if expected == 'confirm' else 'confirm'
            self.assertTrue(engine.check_fire(task, expected))
            self.assertFalse(engine.check_fire(task, other))
        self.assertEqual(seen, {'confirm', 'reject'})

    def test_sector(self):
        self.assertTrue(engine.in_sector(350, [330, 90]))
        self.assertTrue(engine.in_sector(10, [330, 90]))
        self.assertFalse(engine.in_sector(200, [330, 90]))


class TokenCodeTests(SimpleTestCase):
    def test_codes_are_unique_and_unambiguous(self):
        codes = engine.generate_codes(random.Random(2), 500)
        self.assertEqual(len(set(codes)), 500)
        for code in codes:
            self.assertEqual(len(code), 4)
            self.assertFalse(set(code) & set('01OIl'))


# --- Контент ----------------------------------------------------------------

class ContentTests(SimpleTestCase):
    def test_default_content_loads(self):
        content = get_content('game')
        self.assertEqual(len(content.program), 8)
        self.assertEqual(len(content.characters), 6)
        self.assertEqual(sum(c.is_imp for c in content.characters), 1)
        self.assertEqual([p.not_before for p in content.program], [25, 35, 45, 55, 60, 70, 85, 100])

    def test_every_character_has_three_dossier_cards(self):
        for c in get_content('game').characters:
            self.assertEqual(set(c.dossier), {'life', 'death', 'arrival'})

    def test_dossier_has_no_callsigns(self):
        content = get_content('game')
        callsigns = [c.callsign.casefold() for c in content.characters]
        for c in content.characters:
            for text in c.dossier.values():
                for callsign in callsigns:
                    self.assertNotIn(callsign, text.casefold())

    def test_loader_rejects_short_program(self):
        import yaml
        from .loader import DATA_DIR
        data = yaml.safe_load((DATA_DIR / 'game.yaml').read_text(encoding='utf-8'))
        data['program'] = data['program'][:7]
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_decreasing_times(self):
        import yaml
        from .loader import DATA_DIR
        data = yaml.safe_load((DATA_DIR / 'game.yaml').read_text(encoding='utf-8'))
        data['program'][3]['not_before'] = 1
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_missing_dossier(self):
        import yaml
        from .loader import DATA_DIR
        data = yaml.safe_load((DATA_DIR / 'game.yaml').read_text(encoding='utf-8'))
        del data['flight_template']['characters'][0]['dossier']['death']
        with self.assertRaises(ContentError):
            parse_content(data)


# --- Страницы: цепочка и уровни ---------------------------------------------

class ChainViewTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=1)
        self.player = first_player(self.game)
        login(self.client, self.game, self.player)

    def set_sync(self, **values):
        for key, value in values.items():
            setattr(self.player, f'sync_{key}', value)
        self.player.save()

    def test_move_closed_at_nav_25_open_at_50(self):
        self.set_sync(nav=25)
        response = self.client.get(reverse('ls:model', args=['move']))
        self.assertRedirects(response, reverse('ls:post'))
        self.set_sync(nav=50)
        self.assertEqual(self.client.get(reverse('ls:model', args=['move'])).status_code, 200)

    def test_deviation_keeps_move_open_if_it_has_sync(self):
        self.set_sync(nav=50, move=25)
        self.client.post(reverse('ls:model', args=['nav']), {'action': 'refuse'})
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 25)
        self.assertEqual(self.client.get(reverse('ls:model', args=['move'])).status_code, 200)

    def test_move_closes_again_if_it_never_started(self):
        self.set_sync(nav=50, move=0)
        self.client.post(reverse('ls:model', args=['nav']), {'action': 'refuse'})
        response = self.client.get(reverse('ls:model', args=['move']))
        self.assertRedirects(response, reverse('ls:post'))

    def test_instruction_levels(self):
        url = reverse('ls:model', args=['nav'])
        self.set_sync(nav=25)
        ctx = self.client.get(url).context
        self.assertTrue(ctx['instruction'])
        self.assertFalse(ctx['short'])
        self.set_sync(nav=50)
        self.assertFalse(self.client.get(url).context['instruction'])
        self.set_sync(nav=75)
        ctx = self.client.get(url).context
        self.assertTrue(ctx['short'])
        self.assertFalse(ctx['instruction'])
        self.set_sync(nav=100)
        response = self.client.get(url)
        self.assertContains(response, 'Автоматика')
        self.assertNotContains(response, 'Подтвердить')

    def test_post_page_shows_waiting_before_start(self):
        self.game.started_at = None
        self.game.save()
        self.assertContains(self.client.get(reverse('ls:post')), 'Ожидание сеанса')

    def test_post_page_hides_session_time(self):
        response = self.client.get(reverse('ls:post'))
        self.assertNotContains(response, 'Игровое время')

    def test_unknown_param_is_404(self):
        self.assertEqual(self.client.get(reverse('ls:model', args=['zzz'])).status_code, 404)


class ModelFlowTests(TestCase):
    """Сквозные прогоны каждой модели через клиент: выдача задачи и POST ответа."""

    def setUp(self):
        self.game = make_game(started_minutes_ago=1)
        self.player = first_player(self.game)
        self.player.sync_nav = self.player.sync_move = self.player.sync_parts = 25
        self.player.save()
        login(self.client, self.game, self.player)

    def task(self, param):
        self.client.get(reverse('ls:model', args=[param]))
        return self.client.session[f'ls_task_{param}']

    def test_nav_success_adds_25(self):
        task = self.task('nav')
        response = self.client.post(reverse('ls:model', args=['nav']),
                                    {'answer': ','.join(engine.nav_expected(task))})
        self.assertContains(response, 'Выполнено')
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 50)
        self.assertTrue(TaskAttempt.objects.get().success)

    def test_nav_failure_changes_nothing(self):
        self.task('nav')
        response = self.client.post(reverse('ls:model', args=['nav']), {'answer': 'A1'})
        self.assertContains(response, 'Провал')
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 25)

    def test_task_is_consumed_by_post(self):
        task = self.task('nav')
        answer = ','.join(engine.nav_expected(task))
        self.client.post(reverse('ls:model', args=['nav']), {'answer': answer})
        again = self.client.post(reverse('ls:model', args=['nav']), {'answer': answer})
        self.assertEqual(again.status_code, 302)
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 50)

    def test_move_too_fast_fails_on_server(self):
        self.task('move')
        response = self.client.post(reverse('ls:model', args=['move']), {'result': 'ok', 'elapsed': '30'})
        self.assertContains(response, 'Провал')

    def test_move_success_when_time_passed(self):
        self.task('move')
        session = self.client.session
        task = session['ls_task_move']
        task['issued_at'] -= 40
        session['ls_task_move'] = task
        session.save()
        response = self.client.post(reverse('ls:model', args=['move']), {'result': 'ok', 'elapsed': '30.1'})
        self.assertContains(response, 'Выполнено')

    def test_parts_flow(self):
        task = self.task('parts')
        state = list(task['initial'])
        for item in task['items']:
            if item['switch'] is not None:
                state[item['switch']] = item['value']
        answer = ''.join('1' if s else '0' for s in state)
        response = self.client.post(reverse('ls:model', args=['parts']), {'answer': answer})
        self.assertContains(response, 'Выполнено')

    def test_fire_flow(self):
        self.player.sync_fire = 25
        self.player.sync_parts = 50
        self.player.save()
        task = self.task('fire')
        response = self.client.post(reverse('ls:model', args=['fire']),
                                    {'answer': engine.fire_expected(task)})
        self.assertContains(response, 'Выполнено')

    def test_pages_render_for_every_model_and_level(self):
        self.player.sync_nav = self.player.sync_move = self.player.sync_parts = 50
        for param in engine.PARAMS:
            for value in (25, 50, 75, 100):
                setattr(self.player, f'sync_{param}', value)
                self.player.save()
                response = self.client.get(reverse('ls:model', args=[param]))
                self.assertEqual(response.status_code, 200, (param, value))

    def test_deviation_gives_card_and_logs(self):
        self.player.sync_nav = 50
        self.player.save()
        response = self.client.post(reverse('ls:model', args=['nav']), {'action': 'refuse'})
        self.assertContains(response, 'Отказ принят')
        self.player.refresh_from_db()
        self.assertEqual((self.player.sync_nav, self.player.cards), (25, 1))
        attempt = TaskAttempt.objects.get()
        self.assertTrue(attempt.deviation)
        self.assertFalse(attempt.success)

    def test_deviation_at_zero_gives_no_card(self):
        self.player.sync_nav = 0
        self.player.save()
        self.client.post(reverse('ls:model', args=['nav']), {'action': 'refuse'})
        self.player.refresh_from_db()
        self.assertEqual((self.player.sync_nav, self.player.cards), (0, 0))

    def test_opening_announcement(self):
        self.player.sync_nav = 25
        self.player.sync_move = 0
        self.player.save()
        task = self.task('nav')
        response = self.client.post(reverse('ls:model', args=['nav']),
                                    {'answer': ','.join(engine.nav_expected(task))})
        self.assertContains(response, 'Доступно: Движение')


# --- Жетоны -----------------------------------------------------------------

class TokenTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.player = first_player(self.game)
        self.other_symbol = next(s for s, _ in Token._meta.get_field('symbol').choices if s != self.player.symbol)

    def token(self, symbol):
        return Token.objects.filter(game=self.game, symbol=symbol, used_by=None).first()

    def test_match_gives_card(self):
        token = self.token(self.player.symbol)
        outcome = services.redeem_codes(self.player, token.code)[0]
        self.assertTrue(outcome.ok)
        self.player.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.player.cards, 1)
        self.assertEqual(token.used_by, self.player)

    def test_mismatch_burns_code(self):
        token = self.token(self.other_symbol)
        outcome = services.redeem_codes(self.player, token.code)[0]
        self.assertIn('не ваш символ', outcome.message)
        self.player.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.player.cards, 0)
        self.assertEqual(token.used_by, self.player)

    def test_repeat_is_rejected(self):
        token = self.token(self.player.symbol)
        services.redeem_codes(self.player, token.code)
        outcome = services.redeem_codes(self.player, token.code.lower())[0]
        self.assertIn('уже использован', outcome.message)
        self.player.refresh_from_db()
        self.assertEqual(self.player.cards, 1)

    def test_other_player_cannot_reuse(self):
        token = self.token(self.player.symbol)
        services.redeem_codes(self.player, token.code)
        other = self.game.players.exclude(pk=self.player.pk).get(symbol=self.player.symbol, is_imp=True)
        outcome = services.redeem_codes(other, token.code)[0]
        self.assertIn('уже использован', outcome.message)

    def test_unknown_code(self):
        outcome = services.redeem_codes(self.player, 'ZZZZ')[0]
        self.assertIn('неизвестный код', outcome.message)

    def test_token_from_another_game_is_unknown(self):
        other_game = services.create_game('Другая', 'OTHER', 'game', 1, rng=random.Random(99))
        foreign = Token.objects.filter(game=other_game).exclude(code__in=self.game.tokens.values('code')).first()
        outcome = services.redeem_codes(self.player, foreign.code)[0]
        self.assertIn('неизвестный код', outcome.message)

    def test_several_codes_in_one_input(self):
        own = self.game.tokens.filter(symbol=self.player.symbol, used_by=None)[:2]
        outcomes = services.redeem_codes(self.player, f'{own[0].code} {own[1].code} ZZZZ')
        self.assertEqual([o.level for o in outcomes], ['ok', 'ok', 'bad'])
        self.player.refresh_from_db()
        self.assertEqual(self.player.cards, 2)

    def test_tokens_page(self):
        login(self.client, self.game, self.player)
        token = self.token(self.player.symbol)
        response = self.client.post(reverse('ls:tokens'), {'codes': token.code}, follow=True)
        self.assertContains(response, 'совпадение')
        self.assertContains(response, 'Ваш символ')


# --- Карты ------------------------------------------------------------------

class CardTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.player = first_player(self.game)
        self.player.cards = 10
        self.player.save()

    def reload(self):
        self.player.refresh_from_db()
        return self.player

    def test_no_cards_no_purchase(self):
        self.player.cards = 0
        self.player.save()
        outcome = services.buy(self.player, 'program')
        self.assertEqual(outcome.level, 'bad')
        self.assertFalse(Purchase.objects.exists())

    def test_locked_by_time_does_not_spend_card(self):
        self.game.started_at = timezone.now()
        self.game.save()
        outcome = services.buy(self.player, 'program')
        self.assertIn('Пока нет', outcome.message)
        self.assertEqual(self.reload().cards, 10)
        self.assertFalse(Purchase.objects.exists())

    def test_before_start_all_locks_closed(self):
        self.assertIn('Пока нет', services.buy(self.player, 'program').message)
        self.assertIn('Пока нет', services.buy(self.player, 'flight').message)

    def test_clock_shift_opens_program_package(self):
        self.game.started_at = timezone.now()
        self.game.save()
        self.assertIn('Пока нет', services.buy(self.player, 'program').message)
        self.game.clock_offset_seconds = 25 * 60
        self.game.save()
        self.player.refresh_from_db()
        outcome = services.buy(self.player, 'program')
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.payload, get_content('game').program[0].text)
        self.assertEqual(self.reload().cards, 9)
        self.assertEqual(self.player.program_index, 1)
        # следующий пакет закрыт до 35-й минуты
        self.assertIn('Пока нет', services.buy(self.player, 'program').message)
        self.assertEqual(self.reload().cards, 9)

    def test_program_in_order_with_late_clock(self):
        self.game.started_at = timezone.now() - timedelta(minutes=120)
        self.game.save()
        texts = [services.buy(self.player, 'program').payload for _ in range(8)]
        self.assertEqual(texts, [p.text for p in get_content('game').program])
        self.assertIn('Вся программа', services.buy(self.player, 'program').message)
        self.assertEqual(self.reload().cards, 2)

    def test_self_stream_order_and_body_lock(self):
        self.game.started_at = timezone.now() - timedelta(minutes=30)
        self.game.save()
        packs = get_content('game').character(self.player.character_id).memory
        self.assertEqual(services.buy(self.player, 'self').payload, packs[0].text)   # без замка
        self.assertEqual(services.buy(self.player, 'self').payload, packs[1].text)   # 25
        self.assertEqual(services.buy(self.player, 'self').payload, packs[2].text)   # 25
        # телесный пакет ждёт открытия параметра parts
        self.assertIn('Пока нет', services.buy(self.player, 'self').message)
        self.player.refresh_from_db()
        self.player.sync_nav = self.player.sync_move = 50
        self.player.sync_parts = 25
        self.player.save()
        self.assertEqual(services.buy(self.player, 'self').payload, packs[3].text)
        # смерть не раньше 70-й минуты
        self.assertIn('Пока нет', services.buy(self.player, 'self').message)

    def test_dossier_order_life_before_death_before_arrival(self):
        self.player.cards = 30
        self.player.save()
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        for _ in range(18):
            self.assertTrue(services.buy(self.player, 'flight').ok)
        order = self.reload().dossier_order
        self.assertEqual(len(order), 18)
        parts = [part for _, part in order]
        self.assertEqual(parts, ['life'] * 6 + ['death'] * 6 + ['arrival'] * 6)
        for part in ('life', 'death', 'arrival'):
            self.assertEqual(len({cid for cid, p in order if p == part}), 6)
        self.assertIn('Все карточки', services.buy(self.player, 'flight').message)
        self.assertEqual(self.reload().cards, 30 - 18)

    def test_dossier_locks_death_and_arrival(self):
        self.player.cards = 30
        self.player.save()
        self.game.started_at = timezone.now() - timedelta(minutes=60)
        self.game.save()
        for _ in range(6):
            self.assertTrue(services.buy(self.player, 'flight').ok)
        self.assertIn('Пока нет', services.buy(self.player, 'flight').message)
        self.game.clock_offset_seconds = 10 * 60
        self.game.save()
        self.player.refresh_from_db()
        for _ in range(6):
            self.assertTrue(services.buy(self.player, 'flight').ok)
        self.assertIn('Пока нет', services.buy(self.player, 'flight').message)
        self.assertEqual(self.reload().cards, 30 - 12)

    def test_dossier_cards_do_not_name_callsigns(self):
        self.player.cards = 30
        self.player.save()
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        callsigns = [c.callsign.casefold() for c in get_content('game').characters]
        for _ in range(18):
            payload = services.buy(self.player, 'flight').payload.casefold()
            self.assertFalse(any(cs in payload for cs in callsigns))

    def test_orders_differ_between_players(self):
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        orders = set()
        for player in self.game.players.all():
            player.cards = 1
            player.save()
            services.buy(player, 'flight')
            player.refresh_from_db()
            orders.add(tuple(map(tuple, player.dossier_order)))
        self.assertGreater(len(orders), 1)

    def test_radio_levels(self):
        self.assertTrue(services.buy(self.player, 'radio').ok)
        self.assertEqual(self.reload().radio_level, 1)
        self.assertTrue(services.buy(self.player, 'radio').ok)
        self.assertEqual(self.reload().radio_level, 2)
        self.assertEqual(services.buy(self.player, 'radio').level, 'warn')
        self.assertEqual(self.reload().cards, 8)

    def test_known_page_lists_purchases_in_order(self):
        self.game.started_at = timezone.now() - timedelta(minutes=120)
        self.game.save()
        login(self.client, self.game, self.player)
        self.client.post(reverse('ls:cards'), {'kind': 'program'})
        self.client.post(reverse('ls:cards'), {'kind': 'program'})
        response = self.client.get(reverse('ls:known'))
        program = get_content('game').program
        html = response.content.decode()
        self.assertLess(html.index(program[0].text[:40]), html.index(program[1].text[:40]))

    def test_cards_page_buttons(self):
        login(self.client, self.game, self.player)
        response = self.client.get(reverse('ls:cards'))
        for title in ('О себе', 'О звене', 'О программе', 'Рация'):
            self.assertContains(response, title)


# --- Рация ------------------------------------------------------------------

class RadioTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.player = first_player(self.game)
        login(self.client, self.game, self.player)

    def test_radio_page_needs_radio(self):
        self.assertRedirects(self.client.get(reverse('ls:radio')), reverse('ls:post'))

    def test_radio_page_with_level_1(self):
        self.player.radio_level = 1
        self.player.save()
        self.assertEqual(self.client.get(reverse('ls:radio')).status_code, 200)

    def test_message_goes_to_whole_game(self):
        self.player.radio_level = 1
        self.player.save()
        self.client.post(reverse('ls:radio'), {'text': 'Слышу вас'})
        other = self.game.players.exclude(pk=self.player.pk).first()
        other.radio_level = 1
        other.save()
        other_client = self.client_class()
        login(other_client, self.game, other)
        self.assertContains(other_client.get(reverse('ls:radio')), 'Слышу вас')
        self.assertEqual(RadioMessage.objects.count(), 1)

    def test_post_shows_radio_link_only_with_radio(self):
        self.assertNotContains(self.client.get(reverse('ls:post')), reverse('ls:radio'))
        self.player.radio_level = 2
        self.player.save()
        response = self.client.get(reverse('ls:post'))
        self.assertContains(response, reverse('ls:radio'))
        self.assertContains(response, 'рация работает и на посту')


# --- Вход -------------------------------------------------------------------

class LoginTests(TestCase):
    def setUp(self):
        self.game = make_game()

    def test_post_requires_login(self):
        self.assertRedirects(self.client.get(reverse('ls:post')), reverse('ls:login'))

    def test_login_and_logout(self):
        player = first_player(self.game)
        response = self.client.post(reverse('ls:login'), {'code': 'test', 'callsign': player.callsign.lower()})
        self.assertRedirects(response, reverse('ls:post'))
        self.assertContains(self.client.get(reverse('ls:post')), player.callsign)
        self.client.post(reverse('ls:logout'))
        self.assertRedirects(self.client.get(reverse('ls:post')), reverse('ls:login'))

    def test_wrong_callsign(self):
        response = self.client.post(reverse('ls:login'), {'code': 'TEST', 'callsign': 'Нет-Такого'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Нет такого сеанса')

    def test_post_shows_symbol_in_words(self):
        player = first_player(self.game)
        login(self.client, self.game, player)
        response = self.client.get(reverse('ls:post'))
        self.assertContains(response, '<svg class="sym"')
        self.assertContains(response, get_content('game').symbols[player.symbol])


# --- Мастер -----------------------------------------------------------------

class MasterTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('master', password='x', is_staff=True)

    def test_create_game_makes_players_and_tokens(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('ls:master_index'), {
            'title': 'Игра', 'code': 'abc', 'content': 'game', 'flights': '3',
        })
        game = Game.objects.get(code='abc')
        self.assertRedirects(response, reverse('ls:master_game', args=[game.pk]))
        self.assertEqual(game.flights.count(), 3)
        self.assertEqual(game.players.count(), 18)
        self.assertEqual(game.tokens.count(), 100)
        for symbol, _ in Token._meta.get_field('symbol').choices:
            self.assertEqual(game.tokens.filter(symbol=symbol).count(), 20)
        self.assertEqual(game.players.filter(is_imp=True).count(), 3)
        callsigns = set(game.players.values_list('callsign', flat=True))
        self.assertEqual(len(callsigns), 18)
        self.assertIn('Семь-Три-Б', callsigns)

    def test_single_flight_has_plain_callsigns(self):
        game = make_game(1)
        self.assertTrue(game.players.filter(callsign='Семь-Три').exists())
        self.assertEqual(game.players.count(), 6)

    def test_duplicate_code_is_rejected(self):
        make_game()
        self.client.force_login(self.staff)
        self.client.post(reverse('ls:master_index'), {
            'title': 'Ещё', 'code': 'test', 'content': 'game', 'flights': '1',
        })
        self.assertEqual(Game.objects.count(), 1)

    def test_staff_required(self):
        game = make_game()
        user = get_user_model().objects.create_user('plain', password='x')
        urls = [
            reverse('ls:master_index'),
            reverse('ls:master_game', args=[game.pk]),
            reverse('ls:master_tokens', args=[game.pk]),
            reverse('ls:master_player', args=[game.pk, first_player(game).pk]),
        ]
        for client_user in (None, user):
            if client_user:
                self.client.force_login(client_user)
            for url in urls:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302, url)
                self.assertIn('/admin/login/', response['Location'])
        response = self.client.post(reverse('ls:master_action', args=[game.pk]), {'op': 'start'})
        self.assertEqual(response.status_code, 302)
        game.refresh_from_db()
        self.assertIsNone(game.started_at)

    def test_pages_render_for_staff(self):
        game = make_game(started_minutes_ago=3)
        self.client.force_login(self.staff)
        player = first_player(game)
        for url in (reverse('ls:master_index'), reverse('ls:master_game', args=[game.pk]),
                    reverse('ls:master_tokens', args=[game.pk]),
                    reverse('ls:master_player', args=[game.pk, player.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_start_and_clock(self):
        game = make_game()
        self.client.force_login(self.staff)
        url = reverse('ls:master_action', args=[game.pk])
        self.client.post(url, {'op': 'start'})
        game.refresh_from_db()
        self.assertIsNotNone(game.started_at)
        started = game.started_at
        self.client.post(url, {'op': 'start'})
        game.refresh_from_db()
        self.assertEqual(game.started_at, started)
        self.client.post(url, {'op': 'clock', 'delta': '300'})
        self.client.post(url, {'op': 'clock', 'delta': '300'})
        self.client.post(url, {'op': 'clock', 'delta': '-300'})
        game.refresh_from_db()
        self.assertEqual(game.clock_offset_seconds, 300)
        self.assertGreaterEqual(game.game_minutes(), 5)

    def test_player_adjustments(self):
        game = make_game()
        player = first_player(game)
        self.client.force_login(self.staff)
        url = reverse('ls:master_action', args=[game.pk])
        for op in ('sync:nav:25', 'sync:nav:25', 'sync:move:-25', 'cards:1', 'cards:1', 'cards:-1',
                   'radio:1', 'radio:1', 'radio:1'):
            self.client.post(url, {'op': op, 'player': player.pk})
        player.refresh_from_db()
        self.assertEqual((player.sync_nav, player.sync_move), (50, 0))
        self.assertEqual((player.cards, player.radio_level), (1, 2))

    def test_cannot_touch_player_of_another_game(self):
        game = make_game()
        other = services.create_game('Другая', 'OTHER', 'game', 1, rng=random.Random(7))
        stranger = first_player(other)
        self.client.force_login(self.staff)
        response = self.client.post(reverse('ls:master_action', args=[game.pk]),
                                    {'op': 'cards:1', 'player': stranger.pk})
        self.assertEqual(response.status_code, 404)

    def test_token_sheet_marks_used(self):
        game = make_game()
        player = first_player(game)
        token = game.tokens.first()
        services.redeem_codes(player, token.code)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('ls:master_tokens', args=[game.pk]))
        self.assertContains(response, token.code)
        self.assertContains(response, 'использовано 1 из 100')
        self.assertTrue(re.search(r'tcode used', response.content.decode()))
