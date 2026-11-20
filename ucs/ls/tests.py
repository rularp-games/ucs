"""Тесты приложения ls: правила, генераторы задач, жетоны, карты, страницы."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from . import engine, services
from .loader import (ContentError, MEMORY_THEME_TITLES, get_content, parse_content, render_gender)
from .models import ArchiveOpen, DarkEntry, Game, Player, Purchase, RadioMessage, TaskAttempt, Token


def make_game(flights: int = 1, started_minutes_ago: float | None = None) -> Game:
    game = services.create_game('Тест', 'TEST', 'game', flights)
    if started_minutes_ago is not None:
        game.started_at = timezone.now() - timedelta(minutes=started_minutes_ago)
        game.save()
    return game


def login(client, game: Game, player: Player) -> None:
    response = client.post(reverse('ls:login'), {'code': game.code, 'callsign': player.callsign})
    assert response.status_code == 302, 'вход не удался'


def first_player(game: Game) -> Player:
    """Игрок для общих проверок: Семь-Три (квадрат), второй в первом звене."""
    return game.players.get(callsign='Семь-Три')


def player_by(game: Game, callsign: str) -> Player:
    return game.players.get(callsign=callsign)


def give_token(game: Game, owner: Player, symbol: str, code: str, minutes_ago: float = 0) -> Token:
    """Жетон, выданный игроку `owner` `minutes_ago` минут назад."""
    return Token.objects.create(game=game, code=code, symbol=symbol, issued_to=owner,
                                issued_at=timezone.now() - timedelta(minutes=minutes_ago))


def shown(game: Game, text: str) -> str:
    """Текст контента так, как его видит игрок игры (метки рода подставлены)."""
    return render_gender(text, services.game_genders(game))


LARP = Path('/home/duckhawk/git/larp/lost-satellites')


def at_minute(game: Game, minutes: float) -> Game:
    """Часы игры показывают `minutes` минут от старта (старт нажат только что)."""
    game.started_at = timezone.now()
    game.clock_offset_seconds = int(minutes * 60)
    game.save()
    return game


def load_yaml() -> dict:
    import yaml
    from .loader import DATA_DIR
    return yaml.safe_load((DATA_DIR / 'game.yaml').read_text(encoding='utf-8'))


# --- Движок: цепочка и уровни -----------------------------------------------

class SyncRulesTests(SimpleTestCase):
    def test_peaceful_params_open_from_start(self):
        for param in ('nav', 'move', 'parts'):
            self.assertTrue(engine.is_open({}, param))

    def test_fire_opens_by_sense(self):
        self.assertFalse(engine.is_open({'nav': 100, 'move': 100, 'parts': 100, 'sense': 25}, 'fire', 90))
        self.assertTrue(engine.is_open({'sense': 50}, 'fire', 90))
        self.assertFalse(engine.is_open({'parts': 50}, 'fire', 90))

    def test_sense_opens_by_time(self):
        self.assertFalse(engine.is_open({}, 'sense'))
        self.assertFalse(engine.is_open({}, 'sense', 54.9))
        self.assertTrue(engine.is_open({}, 'sense', 55))
        self.assertTrue(engine.is_open({'sense': 25}, 'sense', 10))   # мастер выставил раньше

    def test_open_param_stays_open_after_previous_drops(self):
        self.assertTrue(engine.is_open({'sense': 25, 'fire': 25}, 'fire'))
        self.assertFalse(engine.is_open({'sense': 25, 'fire': 0}, 'fire'))

    def test_modes(self):
        sync = {'nav': 25}
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_INSTRUCTION)
        sync['nav'] = 50
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_FULL)
        sync['nav'] = 75
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_SHORT)
        sync['nav'] = 100
        self.assertEqual(engine.mode_of(sync, 'nav'), engine.MODE_AUTO)
        self.assertEqual(engine.mode_of({'parts': 25}, 'fire'), engine.MODE_CLOSED)

    def test_steps_are_clamped(self):
        self.assertEqual(engine.after_success(100), 100)
        self.assertEqual(engine.after_success(0), 25)
        self.assertEqual(engine.after_deviation(0), 0)
        self.assertEqual(engine.after_deviation(50), 25)


class StageRulesTests(SimpleTestCase):
    def test_stage_by_minutes(self):
        self.assertEqual(engine.stage(None), 1)
        self.assertEqual(engine.stage(0), 1)
        self.assertEqual(engine.stage(54.9), 1)
        self.assertEqual(engine.stage(55), 2)
        self.assertEqual(engine.stage(84.9), 2)
        self.assertEqual(engine.stage(85), 3)
        self.assertEqual(engine.stage(200), 3)

    def test_cap_by_stage(self):
        self.assertEqual([engine.stage_cap(s) for s in (1, 2, 3)], [50, 75, 100])
        self.assertEqual(engine.after_success(25, 50), 50)
        self.assertEqual(engine.after_success(50, 50), 50)
        self.assertEqual(engine.after_success(50, 75), 75)
        self.assertEqual(engine.after_success(75, 100), 100)
        self.assertEqual(engine.after_success(75, 50), 75)   # потолок не отнимает

    def test_tier(self):
        self.assertEqual([engine.tier(v) for v in (0, 25, 50, 75, 100)], [0, 1, 2, 3, 3])

    def test_pause_after_fail(self):
        self.assertEqual(engine.pause_left(None, 100.0), 0)
        self.assertEqual(engine.pause_left(100.0, 100.0), 30)
        self.assertEqual(engine.pause_left(100.0, 120.5), 10)
        self.assertEqual(engine.pause_left(100.0, 130.0), 0)

    def test_deviation_card_cooldown(self):
        self.assertTrue(engine.deviation_card_allowed(None, 0))
        self.assertFalse(engine.deviation_card_allowed(0, 19 * 60))
        self.assertTrue(engine.deviation_card_allowed(0, 20 * 60))

    def test_hall_seed_shared_in_window(self):
        self.assertEqual(engine.hall_seed(1, 60, 'x'), engine.hall_seed(1, 74.9, 'x'))
        self.assertNotEqual(engine.hall_seed(1, 60, 'x'), engine.hall_seed(1, 75, 'x'))
        self.assertNotEqual(engine.hall_seed(1, 60, 'x'), engine.hall_seed(2, 60, 'x'))
        self.assertNotEqual(engine.hall_seed(1, 60, 'x'), engine.hall_seed(1, 60, 'y'))


# --- Движок: четыре модели --------------------------------------------------

class NavModelTests(SimpleTestCase):
    @staticmethod
    def cell_of(label):
        return ord(label[0]) - ord('A'), int(label[1:]) - 1

    def test_deterministic_by_seed(self):
        for level in (0, 25, 50, 75):
            for stage in (1, 2, 3):
                self.assertEqual(engine.gen_nav(4, level, stage), engine.gen_nav(4, level, stage))

    def test_portrait_field(self):
        self.assertEqual((engine.NAV_COLS, engine.NAV_ROWS), (8, 11))
        self.assertEqual(engine.cell_label(0, 2), 'A3')
        self.assertEqual(engine.cell_label(7, 10), 'H11')

    def test_correct_and_wrong_answers(self):
        for level in (0, 25, 50, 75):
            for stage in (1, 2, 3):
                for seed in range(15):
                    task = engine.gen_nav(seed, level, stage)
                    expected = engine.nav_expected(task)
                    self.assertEqual(len(set(expected)), len(expected), (seed, level, stage))
                    for label in expected:
                        c, r = self.cell_of(label)
                        self.assertTrue(0 <= c < 8 and 0 <= r < 11)
                    self.assertTrue(engine.check_nav(task, expected))
                    self.assertTrue(engine.check_nav(task, ','.join(expected).lower()))
                    self.assertFalse(engine.check_nav(task, expected[:-1]))
                    self.assertFalse(engine.check_nav(task, []))
                    if len(expected) > 1:
                        self.assertFalse(engine.check_nav(task, list(reversed(expected))))

    def test_form_by_level(self):
        t0 = engine.gen_nav(1, 0)
        self.assertEqual(len(t0['beacons']), 3)
        self.assertEqual(len(t0['highlight']), 3)
        self.assertIn(t0['exit'], t0['exit_text'])
        self.assertEqual(len(t0['stars']), 46)
        t1 = engine.gen_nav(1, 25)
        self.assertEqual(t1['highlight'], [])
        self.assertNotIn(t1['exit'], t1['exit_text'])
        self.assertIn('от маяка 3', t1['exit_text'])
        t2 = engine.gen_nav(1, 50)
        self.assertEqual(len(t2['cards']), 2)
        self.assertEqual(t2['beacon_text'], [])
        self.assertIn('от маяка 2', t2['exit_text'])
        self.assertEqual(len(t2['stars']), 70)
        t3 = engine.gen_nav(1, 75)
        self.assertEqual(len(t3['cards']), 1)
        self.assertEqual(engine.nav_expected(t3), [t3['exit']])

    def test_offset_exit(self):
        for seed in range(40):
            task = engine.gen_nav(seed, 25)
            bx, by = self.cell_of(task['beacons'][2])
            ex, ey = self.cell_of(task['exit'])
            self.assertTrue(1 <= abs(ex - bx) <= 3 and 1 <= abs(ey - by) <= 3)
            self.assertIn(engine.offset_text(ex - bx, ey - by), task['exit_text'])
        self.assertEqual(engine.offset_text(2, 3), '2 вправо, 3 вниз')
        self.assertEqual(engine.offset_text(-1, -2), '1 влево, 2 вверх')

    def test_silent_beacon_on_faults_stage(self):
        for seed in range(40):
            task = engine.gen_nav(seed, 0, 2)
            (x1, y1), (x2, y2), (x3, y3) = (self.cell_of(b) for b in task['beacons'])
            self.assertEqual((x2 * 2, y2 * 2), (x1 + x3, y1 + y3))
            self.assertEqual(task['beacon_text'][1], 'посередине между маяком 1 и маяком 3')
            self.assertEqual(len(task['highlight']), 2)
        self.assertNotIn('посередине', ' '.join(engine.gen_nav(1, 0, 1)['beacon_text']))

    def test_constellation_card_matches_field_and_is_unique(self):
        for seed in range(25):
            for stage in (1, 2):
                task = engine.gen_nav(seed, 50, stage)
                stars = [tuple(s) for s in task['stars']]
                for label, card in zip(task['beacons'], task['cards']):
                    if stage == 1:
                        self.assertEqual(card['rot'], 0)
                    else:
                        self.assertIn(card['rot'], (90, 180, 270))
                    c, r = self.cell_of(label)
                    # в клетке маяка есть звезда, вокруг которой рисунок карточки (с обратным поворотом)
                    back = [engine._rotate(x, y, (360 - card['rot']) % 360) for x, y in card['points']]
                    found = [s for s in stars if int(s[0]) == c and int(s[1]) == r and all(
                        any(abs(o[0] - (s[0] + dx)) < 0.02 and abs(o[1] - (s[1] + dy)) < 0.02 for o in stars)
                        for dx, dy in back)]
                    self.assertTrue(found, (seed, stage, label))

    def test_intercept_on_ready_stage(self):
        for seed in range(40):
            for level in (0, 25, 50, 75):
                task = engine.gen_nav(seed, level, 3)
                t = task['target']
                self.assertTrue(3 <= t['n'] <= 5)
                self.assertTrue(-2 <= t['dx'] <= 2 and -2 <= t['dy'] <= 2 and (t['dx'] or t['dy']))
                self.assertEqual(self.cell_of(task['exit']), (t['x'] + t['n'] * t['dx'], t['y'] + t['n'] * t['dy']))
                self.assertIn(f'через {t["n"]} тактов', task['exit_text'])
        short = engine.gen_nav(3, 75, 3)
        self.assertEqual(short['cards'], [])
        self.assertEqual(engine.nav_expected(short), [short['exit']])
        self.assertIsNone(engine.gen_nav(3, 25, 2)['target'])


def solve_move(physics: dict) -> list[int]:
    """Запись нажатий, удерживающая шарик: перебор трёх вариантов с прогнозом на 0,8 с.

    Так тесты получают честную запись, а не подделку «ok»; заодно это проверка, что задача проходима.
    """
    def cost(sim, u):
        twin = sim.copy()
        worst = 0.0
        for k in range(24):
            if not twin.step(u if k < 6 else 0):
                return 10 + 24 - k
            center, half = engine.move_window(physics, twin.n - 1)
            worst = max(worst, abs(twin.x - center) / half)
        return worst + abs(twin.v) * 0.5

    sim = engine.MoveSim(physics)
    inputs = []
    for _ in range(physics['steps']):
        u = min((-1, 0, 1), key=lambda u: cost(sim, u))
        inputs.append(u)
        sim.step(u)
    return inputs


NODE = shutil.which('node')
MOVE_JS = Path(__file__).resolve().parent / 'templates' / 'ls' / '_move_physics.js'


class MoveModelTests(SimpleTestCase):
    def test_mulberry32_matches_js_values(self):
        # Значения получены из lsMulberry32 в _move_physics.js (node).
        expected = {
            1: (0.6270739405881613, 0.002735721180215478, 0.5274470399599522),
            123456789: (0.2577907438389957, 0.9707721115555614, 0.7853280142880976),
            4294967295: (0.8964226141106337, 0.189478256739676, 0.7156526781618595),
        }
        for seed, values in expected.items():
            rand = engine.Mulberry32(seed)
            self.assertEqual((rand(), rand(), rand()), values)

    def test_deterministic_by_seed(self):
        for level in (0, 25, 50, 75):
            for stage in (1, 2, 3):
                self.assertEqual(engine.gen_move(9, level, stage, 5.0, 2), engine.gen_move(9, level, stage, 5.0, 2))

    def test_table_by_level(self):
        rows = {lv: engine.gen_move(1, lv, 1)['physics'] for lv in (0, 25, 50, 75)}
        self.assertEqual([engine.gen_move(1, lv)['seconds'] for lv in (0, 25, 50, 75)], [20, 25, 30, 15])
        self.assertEqual(rows[0]['half'], 0.25)
        self.assertEqual(rows[25]['half'], 0.22)
        self.assertEqual(rows[0]['pushes'], [])
        self.assertTrue(rows[25]['pushes'] and rows[25]['warn'])
        self.assertTrue(rows[50]['pushes'] and not rows[50]['warn'])
        self.assertEqual(rows[25]['amplitude'], 0)
        self.assertEqual(rows[50]['amplitude'], 0.3)
        self.assertTrue(10 <= rows[50]['period'] <= 14)
        for row in rows.values():
            self.assertIsNone(row['glitch'])
            self.assertEqual(row['narrow'], 0)

    def test_pushes_every_8_to_10_seconds_with_warning(self):
        pushes = engine.gen_move(3, 25)['physics']['pushes']
        gaps = [(b[0] - a[0]) / 30 for a, b in zip(pushes, pushes[1:])]
        self.assertTrue(gaps)
        self.assertTrue(all(7.9 <= g <= 10.1 for g in gaps), gaps)

    def test_glitch_on_faults_stage_shared_by_hall(self):
        for seed in range(30):
            for level in (0, 50):
                p = engine.gen_move(seed, level, 2, hall=1)['physics']
                g = p['glitch']
                self.assertEqual(g['type'], engine.MOVE_GLITCH_TYPES[1])
                self.assertEqual(g['steps'], 75)
                self.assertTrue(8 * 30 <= g['start'] <= max(8, p['steps'] / 30 - 5) * 30)
        self.assertEqual(engine.gen_move(1, 0, 2, hall=0)['physics']['glitch']['type'], 'invert')
        self.assertEqual(engine.gen_move(1, 0, 2, hall=1)['physics']['glitch']['type'], 'delay')

    def test_narrow_window_on_ready_stage(self):
        p = engine.gen_move(1, 50, 3)['physics']
        self.assertEqual(p['narrow'], 0.15)
        self.assertEqual(engine.gen_move(1, 75, 3)['physics']['narrow'], 0.17)
        self.assertAlmostEqual(engine.move_window(p, 0)[1], 0.22)
        self.assertAlmostEqual(engine.move_window(p, 19 * 30)[1], 0.22)
        self.assertAlmostEqual(engine.move_window(p, 25 * 30 - 1)[1], 0.185)
        self.assertAlmostEqual(engine.move_window(p, 30 * 30 - 1)[1], 0.15)

    def test_moving_window(self):
        p = engine.gen_move(1, 50)['physics']
        centers = [engine.move_window(p, n)[0] for n in range(p['steps'])]
        self.assertAlmostEqual(max(centers), 0.3, places=2)
        self.assertAlmostEqual(min(centers), -0.3, places=2)

    def test_replay_is_deterministic_and_solvable(self):
        for seed, level, stage in ((1, 0, 1), (2, 25, 2), (3, 50, 3), (4, 75, 2)):
            p = engine.gen_move(seed, level, stage, hall=seed)['physics']
            inputs = solve_move(p)
            first = engine.simulate_move(p, inputs)
            self.assertTrue(first['ok'], (seed, level, stage))
            self.assertEqual(first, engine.simulate_move(p, inputs))

    def test_idle_and_random_records_fail(self):
        for seed in range(20):
            for level in (0, 50):
                p = engine.gen_move(seed, level, 1)['physics']
                self.assertFalse(engine.simulate_move(p, [0] * p['steps'])['ok'])
                self.assertFalse(engine.simulate_move(p, [1] * p['steps'])['ok'])

    def test_check_move_replays_record_on_server(self):
        task = engine.gen_move(1, 0, 1, now=1000.0)
        record = engine.encode_move_record(solve_move(task['physics']))
        self.assertTrue(engine.check_move(task, record, 1021.0))
        self.assertFalse(engine.check_move(task, record, 1005.0))             # слишком быстро по часам сервера
        self.assertFalse(engine.check_move(task, 'ok', 1021.0))               # подделка «ok»
        self.assertFalse(engine.check_move(task, 'N600', 1021.0))             # ничего не делал
        self.assertFalse(engine.check_move(task, record[:len(record) // 2], 1021.0))  # не весь срок
        self.assertFalse(engine.check_move(task, 'X5', 1021.0))
        other = engine.gen_move(2, 0, 1, now=1000.0)
        self.assertFalse(engine.check_move(other, record, 1021.0))            # чужая запись к другой задаче

    def test_record_encoding(self):
        self.assertEqual(engine.parse_move_record('L2N1R3', 100), [-1, -1, 0, 1, 1, 1])
        self.assertEqual(engine.encode_move_record([-1, -1, 0, 1, 1, 1]), 'L2N1R3')
        self.assertIsNone(engine.parse_move_record('L2xN1', 100))
        self.assertIsNone(engine.parse_move_record('N1000', 100))

    @skipUnless(NODE, 'нужен node для сверки JS-физики')
    def test_js_physics_matches_python(self):
        cases = []
        for seed in range(12):
            p = engine.gen_move(seed * 7919, (0, 25, 50, 75)[seed % 4], 1 + seed % 3, hall=seed)['physics']
            rng = random.Random(seed)
            inputs = [rng.choice((-1, 0, 0, 1)) for _ in range(p['steps'])]
            sim = engine.MoveSim(p)
            trace = [[sim.step(u), sim.x] for u in inputs]
            cases.append({'p': p, 'inputs': inputs, 'trace': trace})
        script = (
            "const {LsMoveSim} = require(process.argv[1]);"
            "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "let bad = 0;"
            "for (const c of cases) { const s = new LsMoveSim(c.p);"
            " c.inputs.forEach((u, i) => { const ok = s.step(u);"
            " if (ok !== c.trace[i][0] || s.x !== c.trace[i][1]) bad++; }); }"
            "console.log(bad);"
        )
        result = subprocess.run([NODE, '-e', script, str(MOVE_JS)], input=json.dumps(cases),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), '0', result.stderr)


class PartsModelTests(SimpleTestCase):
    @staticmethod
    def answer(task):
        return engine.parts_target(task), engine.phantom_items(task)

    def test_deterministic_by_seed(self):
        for level in (0, 25, 50, 75):
            for stage in (1, 2, 3):
                self.assertEqual(engine.gen_parts(5, level, stage, 9), engine.gen_parts(5, level, stage, 9))

    def test_panel_grows_with_sync_and_splits_suspension(self):
        self.assertEqual([len(engine.parts_panel(lv)) for lv in (0, 25, 50, 75)], [6, 8, 10, 10])
        self.assertIn('шасси', engine.parts_panel(50))
        self.assertNotIn('шасси', engine.parts_panel(25))
        ready = engine.parts_panel(50, 3)
        self.assertEqual(len(ready), 12)
        self.assertNotIn('подвеска', ready)
        self.assertIn('фиксатор подвесок', ready)
        self.assertEqual(engine.parts_panel(0, 3), engine.parts_panel(0, 1))

    def test_layout_by_level(self):
        for seed in range(60):
            for level, (count, functional, conditional) in ((0, (3, 0, 0)), (25, (4, 1, 0)),
                                                            (50, (5, 2, 1)), (75, (3, 1, 0))):
                task = engine.gen_parts(seed, level, 1)
                types = [i['type'] for i in task['items']]
                self.assertEqual(len(types), count)
                self.assertEqual(types.count('function'), functional, (seed, level))
                self.assertEqual(types.count('condition'), conditional)
                self.assertEqual(len(task['switches']), len(task['initial']))

    def test_reference_only_in_instruction(self):
        self.assertTrue(engine.gen_parts(1, 25)['reference'])
        self.assertFalse(engine.gen_parts(1, 50)['reference'])
        self.assertIn('Стабилизация — ориентация 1: включить, ориентация 2: включить',
                      engine.gen_parts(1, 25)['reference'])

    def test_item_sets_are_disjoint_and_every_real_item_needs_action(self):
        for seed in range(80):
            for level in (0, 25, 50):
                for stage in (1, 2, 3):
                    task = engine.gen_parts(seed, level, stage, seed)
                    seen = set()
                    for item in task['items']:
                        ids = {s for s, _ in item['sets']} | ({item['cond']} if 'cond' in item else set())
                        self.assertFalse(seen & ids)
                        seen |= ids
                        if not item['phantom']:
                            self.assertTrue(any(task['initial'][s] != v for s, v in item['sets']), item)

    def test_correct_answer_passes_and_mistakes_fail(self):
        for seed in range(60):
            task = engine.gen_parts(seed, 50, 2, seed)
            state, marked = self.answer(task)
            self.assertTrue(engine.check_parts(task, state, marked))
            untouched = [i for i in range(len(state)) if state[i] == task['initial'][i]
                         and all(i not in {s for s, _ in it['sets']} for it in task['items'])]
            wrong = list(state)
            wrong[untouched[0]] = not wrong[untouched[0]]
            self.assertFalse(engine.check_parts(task, wrong, marked))
            self.assertFalse(engine.check_parts(task, task['initial'], marked))

    def test_conditional_item_follows_condition(self):
        for seed in range(40):
            task = engine.gen_parts(seed, 50, 1)
            item = next(i for i in task['items'] if i['type'] == 'condition')
            self.assertTrue(item['label'].startswith('если '))
            self.assertIn(', иначе ', item['label'])
            cname = task['switches'][item['cond']]
            self.assertIn(cname, item['label'])

    def test_no_phantoms_on_first_stage_or_at_zero(self):
        for seed in range(80):
            self.assertFalse(engine.gen_parts(seed, 50, 1)['phantom'])
            self.assertFalse(engine.gen_parts(seed, 0, 3)['phantom'])
            self.assertFalse(engine.gen_parts(seed, 50, 1)['marks'])

    def test_phantom_share_by_stage_and_must_be_marked(self):
        two = [engine.gen_parts(s, 25, 2, 3) for s in range(300)]
        three = [engine.gen_parts(s, 25, 3, 3) for s in range(300)]
        share2 = sum(t['phantom'] for t in two) / 300
        share3 = sum(t['phantom'] for t in three) / 300
        self.assertTrue(0.4 < share2 < 0.6, share2)
        self.assertTrue(0.6 < share3 < 0.8, share3)
        task = next(t for t in two if t['phantom'])
        self.assertTrue(task['marks'])
        state, marked = self.answer(task)
        self.assertEqual(len(marked), 1)
        self.assertTrue(engine.check_parts(task, state, marked))
        self.assertFalse(engine.check_parts(task, state, set()))                  # фантом не отмечен
        real = next(n for n, i in enumerate(task['items']) if not i['phantom'])
        self.assertFalse(engine.check_parts(task, state, marked | {real}))        # отмечен реальный

    def test_phantom_name_is_shared_by_hall(self):
        names = set()
        for seed in range(200):
            task = engine.gen_parts(seed, 25, 2, hall=7)
            names |= {i['label'].split(':')[0] for i in task['items'] if i['phantom']}
        self.assertEqual(names, {engine.PHANTOM_SWITCHES[7 % len(engine.PHANTOM_SWITCHES)]})
        for name in engine.PHANTOM_SWITCHES:
            for level in (0, 25, 50):
                self.assertNotIn(name, engine.parts_panel(level, 3))

    def test_ready_stage_flip_and_suspension_items(self):
        for seed in range(60):
            task = engine.gen_parts(seed, 50, 3, 1)
            flip = task['flip']
            self.assertTrue(6 <= flip['delay'] <= 12)
            touched = {s for i in task['items'] for s, _ in i['sets']} | {
                i['cond'] for i in task['items'] if 'cond' in i}
            self.assertNotIn(flip['switch'], touched)
            state, marked = self.answer(task)
            flipped = list(state)
            flipped[flip['switch']] = not flipped[flip['switch']]
            self.assertFalse(engine.check_parts(task, flipped, marked))  # неисправленное переключение
            labels = ' '.join(i['label'] for i in task['items'])
            self.assertIn('подвес', labels)
        self.assertIsNone(engine.gen_parts(1, 50, 2)['flip'])

    def test_short_variant_half_already_set(self):
        for seed in range(30):
            task = engine.gen_parts(seed, 75, 1)
            done = [i for i in task['items'] if all(task['initial'][s] == v for s, v in i['sets'])]
            self.assertEqual(len(done), 1)
            self.assertTrue(engine.check_parts(task, *self.answer(task)))

    def test_answer_parsing(self):
        self.assertEqual(engine.parse_parts_answer('101010', 6)[0], True)
        self.assertIsNone(engine.parse_parts_answer('1010', 6))
        self.assertIsNone(engine.parse_parts_answer('10101x', 6))
        self.assertEqual(engine.parse_marked('0, 3,x'), {0, 3})
        self.assertEqual(engine.parse_marked(''), set())


class SenseModelTests(SimpleTestCase):
    def test_deterministic_by_seed(self):
        for level in (0, 25, 50, 75):
            for stage in (1, 2, 3):
                self.assertEqual(engine.gen_sense(6, level, stage), engine.gen_sense(6, level, stage))

    def test_form_by_level(self):
        rows = {lv: engine.gen_sense(2, lv, 1) for lv in (0, 25, 50, 75)}
        self.assertEqual([rows[lv]['radius'] for lv in (0, 25, 50, 75)], [1.2, 1.0, 0.8, 0.8])
        self.assertEqual(rows[0]['glow'][0]['strength'], 1.0)
        self.assertTrue(rows[25]['glow'][0]['strength'] < 1.0)
        self.assertEqual(rows[50]['glow'], [])
        self.assertEqual([len(rows[lv]['holes']) for lv in (0, 25, 50, 75)], [0, 0, 1, 1])
        self.assertEqual(rows[75]['flash'], 4)
        self.assertEqual(rows[50]['flash'], 0)
        for row in rows.values():
            self.assertTrue(100 <= len(row['frames'][0]) <= 200)

    def test_lensing_leaves_void_with_ring(self):
        ring, far = [], []
        for seed in range(30):
            task = engine.gen_sense(seed, 0, 1)
            mx, my = task['masses'][0]
            radius = task['radius']
            stars = [(((x - mx) ** 2 + (y - my) ** 2) ** 0.5, k) for x, y, k in task['frames'][0]]
            self.assertTrue(all(d >= radius - 0.02 for d, _ in stars))
            ring += [k for d, k in stars if d < radius * 1.4]
            far += [k for d, k in stars if d > radius * 3]
        self.assertGreater(sum(ring) / len(ring), 1.4)   # кольцо — крупные звёзды
        self.assertLess(max(far), 1.1)

    def test_two_masses_on_faults_stage_and_moving_on_ready(self):
        for level in (0, 25, 50):
            task = engine.gen_sense(1, level, 2)
            self.assertEqual(task['need'], 2)
            (ax, ay), (bx, by) = task['targets']
            self.assertGreaterEqual(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5, 2.0)
        self.assertEqual(engine.gen_sense(1, 75, 2)['need'], 1)
        for seed in range(20):
            task = engine.gen_sense(seed, 25, 3)
            self.assertEqual(len(task['frames']), 2)
            (sx, sy), (vx, vy) = task['masses'][0], task['motion']['velocity']
            tx, ty = task['targets'][0]
            self.assertAlmostEqual(tx, sx + 3 * vx, places=1)
            self.assertAlmostEqual(ty, sy + 3 * vy, places=1)
            self.assertTrue(0 < tx < 8 and 0 < ty < 11)

    def test_check_points(self):
        task = engine.gen_sense(3, 25, 2)
        (ax, ay), (bx, by) = task['targets']
        self.assertTrue(engine.check_sense(task, f'{ax + 0.5}:{ay};{bx}:{by - 0.4}'))
        self.assertTrue(engine.check_sense(task, f'{bx}:{by};{ax}:{ay}'))        # любой порядок
        self.assertFalse(engine.check_sense(task, f'{ax + 0.8}:{ay};{bx}:{by}'))  # дальше 0,7
        self.assertFalse(engine.check_sense(task, f'{ax}:{ay};{ax}:{ay}'))        # одна масса дважды
        self.assertFalse(engine.check_sense(task, f'{ax}:{ay}'))
        self.assertFalse(engine.check_sense(task, 'x:y'))
        self.assertFalse(engine.check_sense(task, 'nan:1;1:1'))

    def test_answer_not_in_stars(self):
        task = engine.gen_sense(5, 50, 1)
        mx, my = task['masses'][0]
        for x, y, _ in task['frames'][0]:
            self.assertGreater(((x - mx) ** 2 + (y - my) ** 2) ** 0.5, 0.5)

    def test_notes(self):
        task = engine.gen_sense(3, 25, 2)
        note = engine.sense_note(task, True, 2)
        self.assertTrue(note.endswith('Корпус знал раньше глаз.'))
        self.assertEqual(engine.sense_note(task, False, 2), '')
        self.assertEqual(engine.sense_note(engine.gen_sense(3, 25, 1), True, 1), '')
        moving = engine.gen_sense(3, 25, 3)
        self.assertEqual(engine.sense_note(moving, True, 3),
                         'Объект без сигнатуры. Координаты переданы в модель «Опознание».')
        self.assertEqual(engine.place_text((7, 10)), 'Справа внизу')
        self.assertEqual(engine.place_text((1, 5)), 'Слева')
        self.assertEqual(engine.place_text((4, 5)), 'В центре')


class FireModelTests(SimpleTestCase):
    def test_deterministic_by_seed(self):
        for level in (0, 25, 50, 75):
            for stage in (1, 2, 3):
                self.assertEqual(engine.gen_fire(7, level, stage), engine.gen_fire(7, level, stage))
        self.assertNotEqual(engine.gen_fire(7, 25, 1), engine.gen_fire(8, 25, 1))

    def test_form_by_level(self):
        self.assertEqual([len(engine.gen_fire(3, lv)['targets']) for lv in (0, 25, 50, 75)], [4, 6, 6, 1])
        self.assertEqual([engine.gen_fire(3, lv)['reference'] for lv in (0, 25, 50, 75)],
                         [True, True, False, False])
        self.assertTrue(engine.gen_fire(3, 75)['short'])

    def test_exactly_one_target_matches(self):
        for level in (0, 25, 50):
            for stage in (1, 2, 3):
                for seed in range(80):
                    task = engine.gen_fire(seed, level, stage)
                    matches = [t for t in task['targets'] if engine.target_matches(task['criteria'], t)]
                    self.assertEqual(len(matches), 1, (seed, level, stage))

    def test_class_comes_from_silhouette(self):
        self.assertEqual(engine.SHAPE_CLASS['transport'], 'B')
        self.assertEqual(engine.SHAPE_CLASS['container'], 'C')
        for seed in range(50):
            task = engine.gen_fire(seed, 25, 2)
            for target in task['targets']:
                self.assertNotIn('klass', target)
                self.assertIn(target['shape'], engine.SHAPES)

    def test_sector_and_rotation_by_level(self):
        def share(level, key):
            tasks = [engine.gen_fire(s, level) for s in range(300)]
            return sum(1 for t in tasks if key(t)) / len(tasks)
        self.assertEqual(share(0, lambda t: t['criteria']['sector']), 0)
        self.assertTrue(0.35 < share(25, lambda t: t['criteria']['sector']) < 0.65)
        self.assertTrue(share(50, lambda t: t['criteria']['sector']) > 0.7)
        self.assertEqual(share(25, lambda t: any(x['rot'] for x in t['targets'])), 0)
        self.assertTrue(share(50, lambda t: any(x['rot'] for x in t['targets'])) > 0.9)
        self.assertEqual(share(0, lambda t: any(x['code'] is None for x in t['targets'])), 0)
        self.assertTrue(share(50, lambda t: any(x['code'] is None for x in t['targets'])) > 0.5)

    def test_civilian_share_and_verb_by_stage(self):
        shares = []
        for stage in (1, 2, 3):
            tasks = [engine.gen_fire(s, 25, stage) for s in range(400)]
            hits = [t['targets'][int(engine.fire_expected(t))] for t in tasks]
            civ = [h for h in hits if engine.is_civilian(h)]
            shares.append(len(civ) / len(hits))
            self.assertTrue(all(engine.SHAPE_CLASS[h['shape']] != 'A' for h in civ))
            self.assertEqual(tasks[0]['verb'], {1: 'Отметить', 2: 'Захват', 3: 'Пуск'}[stage])
        self.assertTrue(0.05 < shares[0] < 0.25, shares)
        self.assertTrue(0.3 < shares[1] < 0.5, shares)
        self.assertTrue(0.5 < shares[2] < 0.7, shares)

    def test_right_and_wrong_choice(self):
        task = engine.gen_fire(11, 50)
        right = engine.fire_expected(task)
        self.assertTrue(engine.check_fire(task, right))
        self.assertFalse(engine.check_fire(task, str((int(right) + 1) % 6)))
        self.assertFalse(engine.check_fire(task, 'timeout'))

    def test_timer_on_ready_stage(self):
        self.assertIsNone(engine.gen_fire(1, 25, 2)['time_limit'])
        self.assertEqual(engine.gen_fire(1, 25, 3)['time_limit'], 60)
        self.assertEqual(engine.gen_fire(1, 50, 3)['time_limit'], 45)
        self.assertEqual(engine.gen_fire(1, 75, 1)['time_limit'], 15)
        task = engine.gen_fire(1, 50, 3)
        right = engine.fire_expected(task)
        self.assertTrue(engine.check_fire(task, right, elapsed=40))
        self.assertFalse(engine.check_fire(task, right, elapsed=60))

    def test_short_variant_confirm_and_reject_with_reason(self):
        seen = set()
        for seed in range(80):
            task = engine.gen_fire(seed, 75, 2)
            self.assertEqual(len(task['targets']), 1)
            bad = engine.violations(task['criteria'], task['targets'][0])
            if not bad:
                seen.add('confirm')
                self.assertTrue(engine.check_fire(task, 'confirm'))
                for reason in engine.FIRE_REASONS:
                    self.assertFalse(engine.check_fire(task, f'reject:{reason}'))
            else:
                seen.add('reject')
                self.assertFalse(engine.check_fire(task, 'confirm'))
                self.assertFalse(engine.check_fire(task, 'reject'))
                for reason in engine.FIRE_REASONS:
                    self.assertEqual(engine.check_fire(task, f'reject:{reason}'), reason in bad)
                self.assertFalse(engine.check_fire(task, f'reject:{next(iter(bad))}', elapsed=30))
        self.assertEqual(seen, {'confirm', 'reject'})

    def test_note_on_civilian_from_second_stage(self):
        for seed in range(300):
            task = engine.gen_fire(seed, 25, 2)
            right = engine.fire_expected(task)
            if engine.is_civilian(task['targets'][int(right)]):
                break
        title = engine.SHAPE_TITLES[task['targets'][int(right)]['shape']]
        self.assertEqual(engine.fire_note(task, right, True, 2), f'Учебная цель: {title}. Засчитано.')
        self.assertEqual(engine.fire_note(task, right, True, 1), '')
        self.assertEqual(engine.fire_note(task, right, False, 2), '')

    def test_unsigned_target_on_ready_stage(self):
        tasks = [engine.gen_fire(s, 25, 3) for s in range(400)]
        with_unsigned = [t for t in tasks if t['unsigned_rule']]
        self.assertTrue(with_unsigned)
        self.assertFalse(any(t['unsigned_rule'] for t in (engine.gen_fire(s, 25, 2) for s in range(200))))
        winners = 0
        for task in with_unsigned:
            self.assertTrue(task['criteria']['sector'])
            matches = [t for t in task['targets'] if engine.target_matches(task['criteria'], t)]
            self.assertEqual(len(matches), 1)
            unsigned = next(t for t in task['targets'] if t.get('unsigned'))
            self.assertIsNone(unsigned['shape'])
            winners += matches[0] is unsigned
        self.assertTrue(0 < winners < len(with_unsigned))

    def test_sector(self):
        self.assertTrue(engine.in_sector(350, [330, 90]))
        self.assertTrue(engine.in_sector(10, [330, 90]))
        self.assertFalse(engine.in_sector(200, [330, 90]))


class SummaryTests(SimpleTestCase):
    def civilian_task(self, level=25, stage=2):
        for seed in range(400):
            task = engine.gen_fire(seed, level, stage)
            task['stage'] = stage
            if any(engine.is_civilian(t) for t in task['targets']
                   if engine.target_matches(task['criteria'], t)):
                return task
        raise AssertionError('нет задачи с гражданской целью')

    def test_fire_summary(self):
        task = self.civilian_task()
        right = engine.fire_expected(task)
        summary = engine.attempt_summary(task, {'answer': right})
        title = engine.SHAPE_TITLES[task['targets'][int(right)]['shape']]
        self.assertEqual(summary['civilian'], title)
        self.assertEqual(summary['fired_civilian'], title)
        self.assertEqual(summary['stage'], 2)
        self.assertIn('нажато по гражданскому силуэту', engine.summary_text(summary))
        refused = engine.attempt_summary(task)
        self.assertNotIn('fired_civilian', refused)
        self.assertIn('подходящая цель — гражданский силуэт', engine.summary_text(refused))
        self.assertTrue(engine.attempt_summary(task, {'answer': 'timeout'})['timeout'])

    def test_parts_summary(self):
        task = next(t for t in (engine.gen_parts(s, 25, 3, 2) for s in range(100)) if t['phantom'])
        n = next(iter(engine.phantom_items(task)))
        marked = engine.attempt_summary(task, {'missing': str(n)})
        self.assertTrue(marked['phantom_marked'])
        self.assertTrue(marked['flip'])
        missed = engine.attempt_summary(task, {'missing': ''})
        self.assertFalse(missed['phantom_marked'])
        self.assertIn(': пропущен', engine.summary_text(missed))
        self.assertIn(': отмечен', engine.summary_text(marked))

    def test_move_sense_nav_summary(self):
        self.assertEqual(engine.attempt_summary(engine.gen_move(1, 0, 2, hall=0))['glitch'], 'invert')
        self.assertIn('сбой тяги: инверсия', engine.summary_text({'glitch': 'invert'}))
        self.assertTrue(engine.attempt_summary(engine.gen_sense(1, 0, 3))['moving'])
        self.assertEqual(engine.attempt_summary(engine.gen_sense(1, 0, 2))['masses'], 2)
        self.assertTrue(engine.attempt_summary(engine.gen_nav(1, 0, 3))['intercept'])
        self.assertEqual(engine.summary_text({'stage': 1}), '')


class DossierEngineTests(SimpleTestCase):
    def test_case_labels(self):
        self.assertEqual([engine.case_label(i) for i in range(3)], ['Дело А', 'Дело Б', 'Дело В'])

    def test_cases_grouped_in_order_of_first_life(self):
        cases = engine.dossier_cases([('b', 'life'), ('a', 'life'), ('b', 'death'), ('a', 'death'), ('b', 'arrival')])
        self.assertEqual([(c['label'], c['character_id'], c['parts']) for c in cases], [
            ('Дело А', 'b', ['life', 'death', 'arrival']), ('Дело Б', 'a', ['life', 'death'])])
        self.assertEqual(engine.dossier_cases([]), [])

    def test_next_part(self):
        self.assertEqual(engine.next_dossier_part(['life']), 'death')
        self.assertEqual(engine.next_dossier_part(['life', 'death']), 'arrival')
        self.assertIsNone(engine.next_dossier_part(['life', 'death', 'arrival']))

    def test_pick_new_case_skips_opened_and_ends(self):
        ids = ['a', 'b', 'c']
        cases = engine.dossier_cases([('a', 'life'), ('c', 'life')])
        for seed in range(10):
            self.assertEqual(engine.pick_new_case(random.Random(seed), ids, cases), 'b')
        cases = engine.dossier_cases([(i, 'life') for i in ids])
        self.assertIsNone(engine.pick_new_case(random.Random(0), ids, cases))


class TokenCodeTests(SimpleTestCase):
    def test_codes_are_unique_four_digits(self):
        codes = engine.generate_codes(random.Random(2), 500)
        self.assertEqual(len(set(codes)), 500)
        for code in codes:
            self.assertRegex(code, r'^\d{4}$')

    def test_taken_codes_are_skipped(self):
        taken = {f'{n:04d}' for n in range(9990)}
        codes = engine.generate_codes(random.Random(3), 10, taken)
        self.assertEqual(sorted(codes), [f'{n:04d}' for n in range(9990, 10000)])
        with self.assertRaises(ValueError):
            engine.generate_codes(random.Random(3), 1, taken | set(codes))

    def test_parse_codes(self):
        self.assertEqual(engine.parse_codes('1407, 0312  5521,'), ['1407', '0312', '5521'])
        self.assertEqual(engine.parse_codes('  '), [])


# --- Контент ----------------------------------------------------------------

class ContentTests(SimpleTestCase):
    def test_default_content_loads(self):
        content = get_content('game')
        self.assertEqual(len(content.program), 8)
        self.assertEqual(len(content.flights), 3)
        self.assertEqual(len(content.characters), 15)
        self.assertEqual([len(f.characters) for f in content.flights], [5, 5, 5])
        self.assertEqual([f.name for f in content.flights], ['Звено 1', 'Звено 2', 'Звено 3'])
        for flight in content.flights:
            self.assertEqual(len({c.symbol for c in flight.characters}), 5)
        self.assertEqual(len({c.callsign for c in content.characters}), 15)
        self.assertEqual([p.not_before for p in content.program], [25, 35, 45, 55, 60, 60, 85, 80])
        self.assertEqual(content.dossier_locks, {'life': 25, 'death': 65, 'arrival': 80})

    def test_no_imps_anywhere(self):
        content = get_content('game')
        self.assertFalse(any(hasattr(c, 'is_imp') for c in content.characters))
        for flight in load_yaml()['flights']:
            for c in flight['characters']:
                self.assertNotIn('is_imp', c)

    def test_memory_structure_is_kept(self):
        for c in get_content('game').characters:
            self.assertEqual([p.type for p in c.memory], ['name', 'memory', 'link', 'body', 'death', 'arrival'])
            self.assertEqual([p.not_before for p in c.memory], [None, None, 40, 55, 65, 80])

    def test_every_character_has_three_dossier_cards(self):
        for c in get_content('game').characters:
            self.assertEqual(set(c.dossier), {'life', 'death', 'arrival'})

    def test_program_free_summaries_and_questions(self):
        content = get_content('game')
        self.assertEqual([p.id for p in content.free_program], ['p1', 'p2', 'p4', 'p7'])
        self.assertEqual([(q.title, q.packs, q.not_before) for q in content.program_questions], [
            ('Что даёт переход?', ('p3', 'p5'), 45),
            ('Что со мной сейчас?', ('p6',), 60),
            ('Как набирали программу?', ('p8',), 80),
        ])

    def test_program_texts_match_information(self):
        source = LARP / 'rules/information.md'
        if not source.is_file():
            self.skipTest('нет репозитория игры')
        md = source.read_text(encoding='utf-8').replace('**', '')
        for p in get_content('game').program:
            self.assertIn(f'| {p.text} |', md, p.id)

    def test_personal_codes_in_memory(self):
        content = get_content('game')
        expected = {
            'Один-Один': ['4102', '4200'], 'Семь-Три': ['1407'], 'Два-Два': ['0312'],
            'Девять-Один': ['1455'], 'Четыре-Ноль': ['2263'],
            'Три-Три': ['3108', '3300'], 'Шесть-Пять': ['0312'], 'Восемь-Восемь': ['5521'],
            'Ноль-Семь': ['3108'], 'Пять-Один': ['0904'],
            'Девять-Девять': ['4511'], 'Два-Семь': ['1407'], 'Шесть-Ноль': ['7712'],
            'Один-Четыре': ['0417'], 'Восемь-Три': ['1455', '7203'],
        }
        for flight_index, flight in enumerate(content.flights):
            for c in flight.characters:
                codes = expected[c.callsign]
                self.assertIn(codes[0], c.memory_pack('memory').text, c.callsign)
                self.assertNotIn(codes[0], c.memory_pack('name').text, c.callsign)
                if flight_index > 0:        # заглушки: все коды в пакете № 2 и в «жизни»
                    for code in codes:
                        self.assertIn(code, c.memory_pack('memory').text, c.callsign)
                        self.assertIn(code, c.dossier['life'], c.callsign)

    def test_stub_arrival_matches_role_basis(self):
        content = get_content('game')
        basis = {
            'Три-Три': 'доброволец', 'Девять-Девять': 'доброволец',
            'Шесть-Пять': 'исследование сохранности речи', 'Один-Четыре': 'исследование памяти',
            'Пять-Один': 'мать сама вписала', 'Шесть-Ноль': 'полевой врач',
            'Ноль-Семь': 'колония передала', 'Два-Семь': 'договор с городским моргом',
            'Восемь-Восемь': 'перепутали бирки', 'Восемь-Три': 'страховой документ',
        }
        by_callsign = {c.callsign: c for c in content.characters}
        for callsign, word in basis.items():
            c = by_callsign[callsign]
            self.assertIn(word, c.dossier['arrival'], callsign)
            self.assertIn(word, c.memory_pack('arrival').text, callsign)

    def test_flight_one_texts_match_files(self):
        folder = LARP / 'characters/flight-1'
        if not folder.is_dir():
            self.skipTest('нет репозитория игры')
        files = {'Один-Один': 'one-one', 'Семь-Три': 'seven-three', 'Два-Два': 'two-two',
                 'Девять-Один': 'nine-one', 'Четыре-Ноль': 'four-zero'}
        by_callsign = {c.callsign: c for c in get_content('game').flights[0].characters}
        self.assertEqual(set(files), set(by_callsign))
        for callsign, name in files.items():
            md = (folder / f'{name}.md').read_text(encoding='utf-8')
            c = by_callsign[callsign]
            quotes = re.findall(r'^> (.+)$', md, re.M)
            self.assertEqual(quotes, [p.text for p in c.memory], callsign)
            rows = re.findall(r'^\| (Жизнь|Смерть|Попадание), \d+ \| (.*) \|$', md, re.M)
            self.assertEqual([text for _, text in rows],
                             [c.dossier['life'], c.dossier['death'], c.dossier['arrival']], callsign)
            self.assertNotIn('заглушка', ' '.join(p.text for p in c.memory) + ' '.join(c.dossier.values()))
        seven = by_callsign['Семь-Три']
        self.assertIn('Четырнадцатый, седьмой борт, ответьте', seven.memory_pack('memory').text)
        self.assertTrue(seven.memory_pack('arrival').text.startswith('Это не воспоминание глазами.'))

    def test_dossier_has_no_callsigns(self):
        content = get_content('game')
        callsigns = [c.callsign.casefold() for c in content.characters]
        for c in content.characters:
            for text in c.dossier.values():
                text = re.sub(r'\{[^{}]*\}', '', text.casefold())     # метки рода — не текст игрока
                for callsign in callsigns:
                    self.assertNotIn(callsign, text)

    def test_archive_has_25_entries_verbatim(self):
        content = get_content('game')
        self.assertEqual(len(content.archive), 25)
        self.assertEqual(len({e.code for e in content.archive}), 25)
        self.assertEqual(sorted(e.queue for e in content.archive if e.queue), list(range(1, 11)))
        self.assertEqual([e.code for e in content.archive_queue],
                         ['6060', '3108', '7203', '2207', '4102', '9001', '1919', '8500', '1456', '7204'])
        locks = {e.code: e.not_before for e in content.archive}
        self.assertEqual(locks['6060'], 25)
        self.assertEqual(locks['2207'], 55)
        self.assertEqual(locks['4102'], 60)
        self.assertEqual(locks['1407'], 65)
        self.assertEqual(locks['3300'], 75)
        self.assertEqual(locks['7205'], 80)
        self.assertEqual(content.archive_entry('1407').kind, 'История')
        self.assertEqual(content.archive_entry('7205').kind, 'Дело')
        self.assertIsNone(content.archive_entry('0000'))
        source = LARP / 'lore/archive.md'
        if source.is_file():
            md = source.read_text(encoding='utf-8')
            for e in content.archive:
                self.assertIn(f'### {e.code} · {e.title}\n\n{e.text}', md)
                queue = '—' if e.queue is None else str(e.queue)
                self.assertIn(f'| {e.code} | {e.title} | {e.kind} | {e.not_before} | {queue} |', md)
        long = content.archive_entry('2207').text
        self.assertIn('\n— Читала.', long)

    def test_loader_rejects_short_program(self):
        data = load_yaml()
        data['program'] = data['program'][:7]
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_unassigned_paid_program_pack(self):
        data = load_yaml()
        data['program_questions'][0]['packs'] = ['p3']      # p5 никому не принадлежит
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_question_with_free_or_unknown_pack(self):
        for bad in ('p1', 'p9'):
            data = load_yaml()
            data['program_questions'][2]['packs'] = ['p8', bad]
            with self.assertRaises(ContentError, msg=bad):
                parse_content(data)

    def test_loader_rejects_wrong_memory_layout(self):
        data = load_yaml()
        memory = data['flights'][0]['characters'][0]['memory']
        memory[2], memory[3] = memory[3], memory[2]
        with self.assertRaises(ContentError):
            parse_content(data)
        data = load_yaml()
        data['flights'][0]['characters'][0]['memory'][2]['not_before'] = None
        with self.assertRaises(ContentError):
            parse_content(data)
        data = load_yaml()
        data['flights'][0]['characters'][0]['memory'][1]['not_before'] = 25
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_missing_dossier(self):
        data = load_yaml()
        del data['flights'][0]['characters'][0]['dossier']['death']
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_duplicate_callsign(self):
        data = load_yaml()
        data['flights'][1]['characters'][0]['callsign'] = data['flights'][0]['characters'][0]['callsign']
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_repeated_symbol_in_flight(self):
        data = load_yaml()
        chars = data['flights'][0]['characters']
        chars[1]['symbol'] = chars[0]['symbol']
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_requires_five_per_flight_and_no_imp(self):
        data = load_yaml()
        del data['flights'][2]['characters'][4]
        with self.assertRaises(ContentError):
            parse_content(data)
        data = load_yaml()
        data['flights'][2]['characters'][1]['is_imp'] = True     # лишнее поле ни на что не влияет
        parse_content(data)

    def test_loader_rejects_bad_archive_codes(self):
        for bad in ('140', '14070', '14a7', 1407):
            data = load_yaml()
            data['archive'][0]['code'] = bad
            with self.assertRaises(ContentError, msg=bad):
                parse_content(data)

    def test_loader_rejects_duplicate_archive_code_and_queue(self):
        data = load_yaml()
        data['archive'][1]['code'] = data['archive'][0]['code']
        with self.assertRaises(ContentError):
            parse_content(data)
        data = load_yaml()
        data['archive'][1]['queue'] = data['archive'][0]['queue']
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_bad_archive_kind(self):
        data = load_yaml()
        data['archive'][0]['kind'] = 'Сказка'
        with self.assertRaises(ContentError):
            parse_content(data)


# --- Пол ролей: метки рода -------------------------------------------------

class RenderGenderTests(SimpleTestCase):
    def test_male_and_female_variants(self):
        text = '{Семь-Три:погиб|погибла}'
        self.assertEqual(render_gender(text, {'Семь-Три': 'm'}), 'погиб')
        self.assertEqual(render_gender(text, {'Семь-Три': 'f'}), 'погибла')

    def test_several_marks_with_different_callsigns(self):
        text = 'Ему {Семь-Три:повезло|повезло}, {Два-Два:он|она} {Семь-Три:ушёл|ушла}.'
        self.assertEqual(render_gender(text, {'Семь-Три': 'f', 'Два-Два': 'm'}), 'Ему повезло, он ушла.')

    def test_empty_variant(self):
        self.assertEqual(render_gender('Это {Семь-Три:|не }так', {'Семь-Три': 'm'}), 'Это так')
        self.assertEqual(render_gender('Это {Семь-Три:|не }так', {'Семь-Три': 'f'}), 'Это не так')

    def test_text_without_marks_is_unchanged(self):
        self.assertEqual(render_gender('Просто текст: 1407.', {}), 'Просто текст: 1407.')

    def test_unknown_callsign_is_an_error(self):
        with self.assertRaises(KeyError):
            render_gender('{Семь-Три:а|б}', {'Два-Два': 'm'})


class GenderContentTests(SimpleTestCase):
    def test_characters_have_gender_and_names(self):
        content = get_content('game')
        for c in content.characters:
            self.assertIn(c.gender, ('m', 'f'))
            self.assertEqual(set(c.names), {'m', 'f'})
        self.assertEqual(content.character('f1c2').gender, 'm')
        self.assertEqual(content.character('f1c2').names, {'m': 'Илья Смолин', 'f': 'Инна Смолина'})
        by_callsign = {c.callsign: c for c in content.characters}
        self.assertEqual(by_callsign['Один-Один'].gender, 'f')
        self.assertEqual(by_callsign['Девять-Один'].names['f'], 'Соня Гордеева')
        self.assertEqual(by_callsign['Пять-Один'].names['m'], 'Константин Остроумов')

    def test_no_braces_left_after_rendering_any_gender(self):
        content = get_content('game')
        for gender in ('m', 'f'):
            genders = {c.callsign: gender for c in content.characters}
            texts = [p.text for p in content.program] + [e.text for e in content.archive]
            texts += [q.title for q in content.program_questions]
            for c in content.characters:
                texts += [p.text for p in c.memory] + list(c.dossier.values())
            for text in texts:
                rendered = render_gender(text, genders)
                self.assertNotIn('{', rendered)
                self.assertNotIn('}', rendered)
                self.assertNotIn('|', rendered)

    def test_archive_variants_differ_by_gender(self):
        text = get_content('game').archive_entry('1455').text
        self.assertIn('Корреспондент — Савва.', render_gender(text, {'Девять-Один': 'm'}))
        self.assertIn('Корреспондент — Соня.', render_gender(text, {'Девять-Один': 'f'}))

    def test_loader_rejects_bad_gender_and_names(self):
        for bad in ('x', '', None):
            data = load_yaml()
            data['flights'][0]['characters'][0]['gender'] = bad
            with self.assertRaises(ContentError, msg=bad):
                parse_content(data)
        data = load_yaml()
        del data['flights'][0]['characters'][0]['names']['f']
        with self.assertRaises(ContentError):
            parse_content(data)
        data = load_yaml()
        del data['flights'][0]['characters'][0]['gender']
        with self.assertRaises(ContentError):
            parse_content(data)

    def test_loader_rejects_broken_marks(self):
        def memory_text(data, text):
            data['flights'][0]['characters'][1]['memory'][0]['text'] = text

        def dossier_text(data, text):
            data['flights'][0]['characters'][1]['dossier']['life'] = text

        def program_text(data, text):
            data['program'][0]['text'] = text

        def archive_text(data, text):
            data['archive'][0]['text'] = text

        broken = (
            '{Семь-Три:погиб}',                 # нет женского варианта
            '{Семь-Три:а|б|в}',                 # лишний разделитель
            '{Семь-Три погиб|погибла}',         # нет двоеточия
            '{Сорок-Два:погиб|погибла}',        # неизвестный позывной
            'лишняя } скобка',
            'открытая { скобка',
            '{Семь-Три:а|{Два-Два:б|в}}',       # вложенность
        )
        for setter in (memory_text, dossier_text, program_text, archive_text):
            for text in broken:
                data = load_yaml()
                setter(data, f'[заглушка] {text}')
                with self.assertRaises(ContentError, msg=f'{setter.__name__}: {text}'):
                    parse_content(data)
            data = load_yaml()
            setter(data, '[заглушка] {Семь-Три:погиб|погибла}, {Девять-Один:|}')
            parse_content(data)                 # корректные метки проходят


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

    def test_peaceful_models_open_at_start(self):
        for param in ('nav', 'move', 'parts'):
            self.assertEqual(self.client.get(reverse('ls:model', args=[param])).status_code, 200)

    def test_sense_opens_at_minute_55(self):
        url = reverse('ls:model', args=['sense'])
        self.assertRedirects(self.client.get(url), reverse('ls:post'))
        at_minute(self.game, 55)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_fire_opens_by_sense_not_parts(self):
        at_minute(self.game, 60)
        self.set_sync(parts=100, sense=25)
        response = self.client.get(reverse('ls:model', args=['fire']))
        self.assertRedirects(response, reverse('ls:post'))
        self.set_sync(sense=50)
        self.assertEqual(self.client.get(reverse('ls:model', args=['fire'])).status_code, 200)

    def test_deviation_keeps_fire_open_if_it_has_sync(self):
        at_minute(self.game, 60)
        self.set_sync(sense=50, fire=25)
        self.client.post(reverse('ls:model', args=['sense']), {'action': 'refuse'})
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_sense, 25)
        self.assertEqual(self.client.get(reverse('ls:model', args=['fire'])).status_code, 200)

    def test_fire_closes_again_if_it_never_started(self):
        at_minute(self.game, 60)
        self.set_sync(sense=50, fire=0)
        self.client.post(reverse('ls:model', args=['sense']), {'action': 'refuse'})
        response = self.client.get(reverse('ls:model', args=['fire']))
        self.assertRedirects(response, reverse('ls:post'))

    def test_post_shows_five_params(self):
        html = self.client.get(reverse('ls:post')).content.decode()
        self.assertIn('Восприятие', html)
        self.assertEqual(html.count('class="param"'), 5)

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
        self.player.refresh_from_db()
        return self.player.tasks[param]

    def patch_task(self, param, **changes):
        self.player.refresh_from_db()
        self.player.tasks[param].update(changes)
        self.player.save()

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
        task = self.task('move')
        record = engine.encode_move_record(solve_move(task['physics']))
        response = self.client.post(reverse('ls:model', args=['move']), {'record': record})
        self.assertContains(response, 'Провал')

    def test_move_forged_ok_fails(self):
        task = self.task('move')
        self.patch_task('move', issued_at=task['issued_at'] - 40)
        response = self.client.post(reverse('ls:model', args=['move']), {'result': 'ok', 'elapsed': '30.1'})
        self.assertContains(response, 'Провал')

    def test_move_success_when_record_holds(self):
        task = self.task('move')
        self.patch_task('move', issued_at=task['issued_at'] - 40)
        record = engine.encode_move_record(solve_move(task['physics']))
        response = self.client.post(reverse('ls:model', args=['move']), {'record': record})
        self.assertContains(response, 'Выполнено')

    def test_parts_flow(self):
        task = self.task('parts')
        answer = ''.join('1' if s else '0' for s in engine.parts_target(task))
        missing = ','.join(str(n) for n in engine.phantom_items(task))
        response = self.client.post(reverse('ls:model', args=['parts']), {'answer': answer, 'missing': missing})
        self.assertContains(response, 'Выполнено')

    def test_parts_phantom_must_be_marked_on_server(self):
        at_minute(self.game, 60)
        for _ in range(100):
            self.player.tasks = {}
            self.player.save()
            task = self.task('parts')
            if task['phantom']:
                break
        self.assertContains(self.client.get(reverse('ls:model', args=['parts'])), 'Нет на панели')
        answer = ''.join('1' if s else '0' for s in engine.parts_target(task))
        response = self.client.post(reverse('ls:model', args=['parts']), {'answer': answer, 'missing': ''})
        self.assertContains(response, 'Провал')

    def test_parts_phantom_shared_by_hall(self):
        at_minute(self.game, 60)
        other = player_by(self.game, 'Два-Два')
        other.sync_parts = 50
        other.save()
        names = set()
        for player in (self.player, other):
            client = self.client_class()
            login(client, self.game, player)
            for _ in range(40):
                player.tasks = {}
                player.save()
                client.get(reverse('ls:model', args=['parts']))
                player.refresh_from_db()
                names |= {i['label'].split(':')[0] for i in player.tasks['parts']['items'] if i['phantom']}
        self.assertEqual(len(names), 1)

    def test_fire_flow(self):
        self.player.sync_fire = 25
        self.player.sync_sense = 50
        self.player.save()
        task = self.task('fire')
        response = self.client.post(reverse('ls:model', args=['fire']),
                                    {'answer': engine.fire_expected(task)})
        self.assertContains(response, 'Выполнено')

    def test_pages_render_for_every_model_level_and_stage(self):
        self.player.sync_nav = self.player.sync_move = self.player.sync_parts = 50
        for minute in (1, 60, 90):
            at_minute(self.game, minute)
            for param in engine.PARAMS:
                for value in (0, 25, 50, 75, 100):
                    self.player.refresh_from_db()
                    setattr(self.player, f'sync_{param}', value)
                    if param == 'fire':
                        self.player.sync_sense = 50
                    if param == 'sense' and minute < 55 and value == 0:
                        continue
                    self.player.save()
                    response = self.client.get(reverse('ls:model', args=[param]))
                    self.assertEqual(response.status_code, 200, (param, value, minute))

    def test_nav_exit_cell_hidden_from_offset_and_target_shown(self):
        self.player.sync_nav = 25
        self.player.save()
        task = self.task('nav')
        html = self.client.get(reverse('ls:model', args=['nav'])).content.decode()
        self.assertIn('от маяка 3', html)
        self.assertNotIn(f'<b>{task["exit"]}</b>', html)
        self.player.tasks = {}
        self.player.save()
        at_minute(self.game, 90)
        html = self.client.get(reverse('ls:model', args=['nav'])).content.decode()
        self.assertIn('учебная цель', html)
        self.assertIn('точку встречи', html)
        self.player.sync_nav = 50
        self.player.tasks = {}
        self.player.save()
        html = self.client.get(reverse('ls:model', args=['nav'])).content.decode()
        self.assertIn('Карточка созвездия 1', html)

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
        at_minute(self.game, 60)
        self.player.sync_sense = 25
        self.player.sync_fire = 0
        self.player.save()
        task = self.task('sense')
        answer = ';'.join(f'{x}:{y}' for x, y in task['targets'])
        response = self.client.post(reverse('ls:model', args=['sense']), {'answer': answer})
        self.assertContains(response, 'Доступно: Огонь')


class TrainingRulesTests(TestCase):
    """Общие правила тренировок: одна задача до ответа, пауза после провала, потолок, карта за отказ."""

    def setUp(self):
        self.game = make_game(started_minutes_ago=1)
        self.player = first_player(self.game)
        self.player.sync_nav = 25
        self.player.save()
        login(self.client, self.game, self.player)
        self.url = reverse('ls:model', args=['nav'])

    def task(self):
        self.client.get(self.url)
        self.player.refresh_from_db()
        return self.player.tasks['nav']

    def right(self, task):
        return ','.join(engine.nav_expected(task))

    def shift_attempts(self, seconds):
        for attempt in TaskAttempt.objects.all():
            attempt.created_at -= timedelta(seconds=seconds)
            attempt.save()

    def test_new_does_not_replace_unanswered_task(self):
        first = self.task()
        self.client.get(self.url + '?new=1')
        self.player.refresh_from_db()
        self.assertEqual(self.player.tasks['nav']['seed'], first['seed'])

    def test_relogin_and_other_browser_get_same_task(self):
        first = self.task()
        other = self.client_class()
        login(other, self.game, self.player)
        other.get(self.url)
        self.player.refresh_from_db()
        self.assertEqual(self.player.tasks['nav']['seed'], first['seed'])

    def test_answer_gives_new_task_after_success(self):
        first = self.task()
        self.client.post(self.url, {'answer': self.right(first)})
        second = self.task()
        self.assertNotEqual(first['issued_at'], second['issued_at'])

    def test_fail_pauses_for_30_seconds(self):
        self.task()
        response = self.client.post(self.url, {'answer': 'A1'})
        self.assertContains(response, 'Провал')
        self.assertContains(response, 'Перекалибровка')
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'ls/model_pause.html')
        self.assertNotContains(response, 'Подтвердить')
        self.player.refresh_from_db()
        self.assertNotIn('nav', self.player.tasks)
        self.assertEqual(services.pause_seconds(self.player, 'nav'), 30)
        self.shift_attempts(31)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'ls/model_nav.html')
        self.player.refresh_from_db()
        self.assertIn('nav', self.player.tasks)

    def test_pause_is_per_parameter(self):
        self.task()
        self.client.post(self.url, {'answer': 'A1'})
        response = self.client.get(reverse('ls:model', args=['parts']))
        self.assertTemplateUsed(response, 'ls/model_parts.html')

    def test_master_change_of_level_gives_new_task(self):
        first = self.task()
        self.player.sync_nav = 50
        self.player.save()
        second = self.task()
        self.assertEqual(second['level'], 50)
        self.assertNotEqual(first['level'], second['level'])

    def test_cap_on_first_stage(self):
        self.player.sync_nav = 50
        self.player.save()
        response = self.client.post(self.url, {'answer': self.right(self.task())})
        self.assertContains(response, 'Выполнено')
        self.assertContains(response, 'Предел калибровки на этом этапе')
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 50)

    def test_cap_rises_with_stage(self):
        at_minute(self.game, 60)
        self.player.sync_nav = 50
        self.player.save()
        self.client.post(self.url, {'answer': self.right(self.task())})
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 75)
        response = self.client.post(self.url, {'answer': self.right(self.task())})
        self.assertContains(response, 'Предел калибровки')
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 75)
        at_minute(self.game, 90)
        self.client.post(self.url, {'answer': self.right(self.task())})
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 100)

    def test_before_start_is_first_stage(self):
        self.game.started_at = None
        self.game.save()
        self.assertEqual(services.stage_of(self.game), 1)
        self.player.sync_nav = 50
        self.player.save()
        self.client.post(self.url, {'answer': self.right(self.task())})
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_nav, 50)

    def test_deviation_card_once_per_20_minutes_per_param(self):
        self.player.sync_nav = 50
        self.player.sync_move = 50
        self.player.save()
        response = self.client.post(self.url, {'action': 'refuse'})
        self.assertContains(response, 'Получена карта')
        response = self.client.post(self.url, {'action': 'refuse'})
        self.assertContains(response, 'Карты за этот отказ нет')
        self.player.refresh_from_db()
        self.assertEqual((self.player.sync_nav, self.player.cards), (0, 1))
        self.client.post(reverse('ls:model', args=['move']), {'action': 'refuse'})
        self.player.refresh_from_db()
        self.assertEqual(self.player.cards, 2)   # другой параметр — своё окно
        self.shift_attempts(20 * 60)
        self.player.sync_nav = 25
        self.player.save()
        self.client.post(self.url, {'action': 'refuse'})
        self.player.refresh_from_db()
        self.assertEqual(self.player.cards, 3)
        self.assertEqual(TaskAttempt.objects.filter(deviation=True, card=True).count(), 3)

    def test_refusal_removes_task_without_pause(self):
        first = self.task()
        self.client.post(self.url, {'action': 'refuse'})
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'ls/model_nav.html')
        self.player.refresh_from_db()
        self.assertNotEqual(self.player.tasks['nav']['issued_at'], first['issued_at'])

    def test_master_page_shows_stage(self):
        staff = get_user_model().objects.create_user('m', password='x', is_staff=True)
        self.client.force_login(staff)
        at_minute(self.game, 60)
        response = self.client.get(reverse('ls:master_game', args=[self.game.pk]))
        self.assertContains(response, 'Этап 2 «Сбои»')
        login(self.client, self.game, self.player)
        self.assertNotContains(self.client.get(reverse('ls:post')), 'Сбои')


class SenseViewTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=1)
        self.player = first_player(self.game)
        login(self.client, self.game, self.player)
        self.url = reverse('ls:model', args=['sense'])

    def task(self):
        response = self.client.get(self.url)
        self.player.refresh_from_db()
        return response, self.player.tasks['sense']

    def answer(self, task):
        return ';'.join(f'{x}:{y}' for x, y in task['targets'])

    def test_title_and_hidden_mass(self):
        at_minute(self.game, 60)
        response, task = self.task()
        self.assertContains(response, 'Учебное наблюдение.')
        html = response.content.decode()
        for x, y in task['targets']:
            self.assertNotIn(f'cx="{round(x * 50, 1)}" cy="{round(y * 50, 1)}"', html)

    def test_success_note_on_faults_stage(self):
        at_minute(self.game, 60)
        _, task = self.task()
        response = self.client.post(self.url, {'answer': self.answer(task)})
        self.assertContains(response, 'Выполнено')
        self.assertContains(response, 'Корпус знал раньше глаз.')
        self.player.refresh_from_db()
        self.assertEqual(self.player.sync_sense, 25)

    def test_coordinates_line_on_ready_stage(self):
        at_minute(self.game, 90)
        response, task = self.task()
        self.assertContains(response, 'Два кадра')
        response = self.client.post(self.url, {'answer': self.answer(task)})
        self.assertContains(response, 'Координаты переданы в модель «Опознание».')

    def test_miss_fails(self):
        at_minute(self.game, 60)
        self.task()
        response = self.client.post(self.url, {'answer': '0.1:0.1;7.9:10.9'})
        self.assertContains(response, 'Провал')


class FireViewTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=1)
        self.player = first_player(self.game)
        self.player.sync_sense = 50
        self.player.sync_fire = 25
        self.player.save()
        login(self.client, self.game, self.player)
        self.url = reverse('ls:model', args=['fire'])

    def task(self):
        response = self.client.get(self.url)
        self.player.refresh_from_db()
        return response, self.player.tasks['fire']

    def test_reference_with_sticker_only_in_instruction(self):
        response, _ = self.task()
        self.assertContains(response, 'Справочник силуэтов')
        self.assertContains(response, 'форма 19-К')
        self.assertContains(response, 'Отметить')
        self.player.sync_fire = 50
        self.player.save()
        response, _ = self.task()
        self.assertNotContains(response, 'форма 19-К')

    def test_answer_not_in_html(self):
        response, task = self.task()
        html = response.content.decode()
        self.assertNotIn('klass', html)
        self.assertNotIn('data-match', html)

    def test_verb_by_stage(self):
        at_minute(self.game, 60)
        response, _ = self.task()
        self.assertContains(response, '>Захват<')
        self.player.tasks = {}
        self.player.save()
        at_minute(self.game, 90)
        response, task = self.task()
        self.assertContains(response, '>Пуск<')
        self.assertContains(response, 'id="timer"')
        self.assertEqual(task['time_limit'], 60)

    def test_timeout_on_server(self):
        at_minute(self.game, 90)
        _, task = self.task()
        self.player.tasks['fire']['issued_at'] -= 120
        self.player.save()
        response = self.client.post(self.url, {'answer': engine.fire_expected(task)})
        self.assertContains(response, 'Провал')

    def test_short_reject_needs_reason(self):
        self.player.sync_fire = 75
        self.player.save()
        at_minute(self.game, 60)
        response, task = self.task()
        self.assertContains(response, 'Отклонить: код')
        bad = engine.violations(task['criteria'], task['targets'][0])
        answer = f'reject:{sorted(bad)[0]}' if bad else 'confirm'
        self.assertContains(self.client.post(self.url, {'answer': answer}), 'Выполнено')

    def test_civilian_line_on_result(self):
        at_minute(self.game, 60)
        for _ in range(200):
            self.player.tasks = {}
            self.player.save()
            _, task = self.task()
            right = engine.fire_expected(task)
            if engine.is_civilian(task['targets'][int(right)]):
                break
        response = self.client.post(self.url, {'answer': right})
        self.assertContains(response, 'Учебная цель:')
        self.assertContains(response, 'Засчитано.')


class DebriefTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=1)
        at_minute(self.game, 60)
        self.player = first_player(self.game)
        self.player.sync_fire = 25
        self.player.sync_parts = 25
        self.player.save()
        login(self.client, self.game, self.player)
        self.staff = get_user_model().objects.create_user('m', password='x', is_staff=True)

    def fresh_task(self, param, want):
        for _ in range(300):
            self.player.tasks = {}
            self.player.save()
            self.client.get(reverse('ls:model', args=[param]))
            self.player.refresh_from_db()
            task = self.player.tasks[param]
            if want(task):
                return task
        raise AssertionError('задача не нашлась')

    def test_journal_and_debrief(self):
        civilian = lambda t: engine.is_civilian(t['targets'][int(engine.fire_expected(t))])
        task = self.fresh_task('fire', civilian)
        self.client.post(reverse('ls:model', args=['fire']), {'answer': engine.fire_expected(task)})
        self.player.refresh_from_db()
        self.player.sync_fire = 25
        self.player.save()
        self.fresh_task('fire', civilian)
        self.client.post(reverse('ls:model', args=['fire']), {'action': 'refuse'})
        phantom = self.fresh_task('parts', lambda t: t['phantom'])
        answer = ''.join('1' if v else '0' for v in engine.parts_target(phantom))
        self.client.post(reverse('ls:model', args=['parts']), {'answer': answer, 'missing': ''})
        TaskAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=1))
        phantom = self.fresh_task('parts', lambda t: t['phantom'])
        answer = ''.join('1' if v else '0' for v in engine.parts_target(phantom))
        missing = ','.join(str(n) for n in engine.phantom_items(phantom))
        self.client.post(reverse('ls:model', args=['parts']), {'answer': answer, 'missing': missing})

        row = services.debrief(self.game)[self.player.pk]
        self.assertEqual(row, {'civilian_hits': 1, 'civilian_refused': 1, 'phantom_marked': 1,
                               'phantom_missed': 1, 'refusals': 1, 'cards': 1})
        self.client.force_login(self.staff)
        html = self.client.get(reverse('ls:master_game', args=[self.game.pk])).content.decode()
        self.assertIn('Дебриф тренировок', html)
        html = self.client.get(reverse('ls:master_player', args=[self.game.pk, self.player.pk])).content.decode()
        self.assertIn('нажато по гражданскому силуэту', html)
        self.assertIn('подходящая цель — гражданский силуэт', html)
        self.assertIn(': пропущен', html)
        self.assertIn(': отмечен', html)
        self.assertIn('отказ, карта', html)


# --- Жетоны -----------------------------------------------------------------

class DarkTests(TestCase):
    def setUp(self):
        self.game = make_game(flights=2)

    def test_issue_tokens_to_group(self):
        group = list(self.game.players.all()[:3]) + [self.game.players.order_by('-id').first()]
        entry = services.issue_tokens(self.game, group, rng=random.Random(5))
        self.assertEqual(entry.players.count(), 4)
        self.assertEqual(entry.tokens.count(), 8)
        archive_codes = {e.code for e in get_content('game').archive}
        symbols = set(get_content('game').symbols)
        for player in group:
            tokens = list(player.tokens_issued.all())
            self.assertEqual(len(tokens), 2)
            for t in tokens:
                self.assertRegex(t.code, r'^\d{4}$')
                self.assertNotIn(t.code, archive_codes)
                self.assertIn(t.symbol, symbols)
                self.assertEqual(t.dark_entry, entry)
        self.assertEqual(len({t.code for t in self.game.tokens.all()}), 8)

    def test_second_group_gets_new_unique_codes(self):
        players = list(self.game.players.all())
        services.issue_tokens(self.game, players[:5])
        services.issue_tokens(self.game, players[5:])
        self.assertEqual(self.game.tokens.count(), 20)
        self.assertEqual(len({t.code for t in self.game.tokens.all()}), 20)
        self.assertEqual(self.game.dark_entries.count(), 2)

    def test_game_has_no_pregenerated_tokens(self):
        self.assertEqual(self.game.tokens.count(), 0)

    def test_master_sends_group_to_dark(self):
        staff = get_user_model().objects.create_user('master', password='x', is_staff=True)
        self.client.force_login(staff)
        ids = [p.pk for p in self.game.players.all()[:3]]
        response = self.client.post(reverse('ls:master_action', args=[self.game.pk]), {'op': 'dark', 'players': ids})
        self.assertRedirects(response, reverse('ls:master_game', args=[self.game.pk]))
        entry = DarkEntry.objects.get()
        self.assertEqual(sorted(entry.players.values_list('pk', flat=True)), sorted(ids))
        self.assertEqual(self.game.tokens.count(), 6)
        page = self.client.get(reverse('ls:master_game', args=[self.game.pk]))
        for p in entry.players.all():
            self.assertContains(page, p.callsign)

    def test_master_dark_needs_players(self):
        staff = get_user_model().objects.create_user('master', password='x', is_staff=True)
        self.client.force_login(staff)
        self.client.post(reverse('ls:master_action', args=[self.game.pk]), {'op': 'dark'})
        self.assertFalse(DarkEntry.objects.exists())

    def test_master_cannot_send_player_of_another_game(self):
        staff = get_user_model().objects.create_user('master', password='x', is_staff=True)
        self.client.force_login(staff)
        other = services.create_game('Другая', 'OTHER', 'game', 1)
        self.client.post(reverse('ls:master_action', args=[self.game.pk]),
                         {'op': 'dark', 'players': [first_player(other).pk]})
        self.assertFalse(DarkEntry.objects.exists())

    def test_player_sees_fresh_tokens_on_post_and_codes(self):
        player = first_player(self.game)
        token = give_token(self.game, player, 'cross', '4321', minutes_ago=5)
        old = give_token(self.game, player, 'star', '8765', minutes_ago=21)
        login(self.client, self.game, player)
        for name in ('ls:post', 'ls:codes'):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'Потеряно соединение со спутниками')
            self.assertContains(response, token.code)
            self.assertNotContains(response, old.code)
        self.assertContains(self.client.get(reverse('ls:post')), 'location.reload')

    def test_banner_shows_minutes_left_not_clock(self):
        player = first_player(self.game)
        give_token(self.game, player, 'cross', '4321', minutes_ago=5)
        login(self.client, self.game, player)
        response = self.client.get(reverse('ls:post'))
        self.assertContains(response, 'ещё 15 мин')
        self.assertNotContains(response, ' · до ')

    def test_banner_disappears_when_token_used_or_expired(self):
        player = first_player(self.game)
        other = self.game.players.exclude(pk=player.pk).first()
        give_token(self.game, player, 'cross', '4321', minutes_ago=5)
        login(self.client, self.game, player)
        services.redeem_codes(other, '4321')
        self.assertNotContains(self.client.get(reverse('ls:post')), 'Потеряно соединение')
        Token.objects.all().delete()
        give_token(self.game, player, 'cross', '4322', minutes_ago=20)
        self.assertNotContains(self.client.get(reverse('ls:post')), '4322')

    def test_banner_absent_without_tokens(self):
        login(self.client, self.game, first_player(self.game))
        self.assertNotContains(self.client.get(reverse('ls:post')), 'Потеряно соединение')


class TokenTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.player = player_by(self.game, 'Семь-Три')         # квадрат
        self.giver = player_by(self.game, 'Два-Два')           # треугольник
        self.other_symbol = 'cross'

    def redeem(self, code, player=None):
        return services.redeem_codes(player or self.player, code)[0]

    def test_match_gives_card(self):
        token = give_token(self.game, self.giver, 'square', '4321')
        outcome = self.redeem('4321')
        self.assertTrue(outcome.ok)
        self.assertIn('совпадение', outcome.message)
        self.player.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.player.cards, 1)
        self.assertEqual(token.used_by, self.player)
        self.assertIsNotNone(token.used_at)
        self.assertFalse(token.burned)
        self.assertEqual(token.state(), 'matched')

    def test_mismatch_burns_token(self):
        token = give_token(self.game, self.giver, self.other_symbol, '4321')
        outcome = self.redeem('4321')
        self.assertIn('Жетон сгорел', outcome.message)
        self.player.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.player.cards, 0)
        self.assertEqual(token.used_by, self.player)
        self.assertTrue(token.burned)
        self.assertEqual(token.state(), 'burned')

    def test_own_token_is_not_counted(self):
        token = give_token(self.game, self.player, 'square', '4321')
        outcome = self.redeem('4321')
        self.assertEqual(outcome.message, '4321: это ваш жетон. Его надо отдать.')
        self.player.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.player.cards, 0)
        self.assertIsNone(token.used_by)
        self.assertEqual(self.player.wrong_codes, 0)
        # жетон по-прежнему можно отдать другому
        other = player_by(self.game, 'Девять-Один')
        give_token(self.game, self.giver, 'star', '4322')
        self.assertTrue(self.redeem('4322', other).ok)

    def test_own_token_does_not_reset_wrong_counter(self):
        give_token(self.game, self.player, 'square', '4321')
        for code in ('1111', '2222'):
            self.redeem(code)
        self.redeem('4321')
        self.player.refresh_from_db()
        self.assertEqual(self.player.wrong_codes, 2)

    def test_repeat_is_invalid(self):
        give_token(self.game, self.giver, 'square', '4321')
        self.redeem('4321')
        outcome = self.redeem('4321')
        self.assertIn('уже недействителен', outcome.message)
        self.player.refresh_from_db()
        self.assertEqual(self.player.cards, 1)
        self.assertEqual(self.player.wrong_codes, 0)

    def test_other_player_cannot_reuse(self):
        give_token(self.game, self.giver, 'cross', '4321')
        self.redeem('4321')
        other = player_by(self.game, 'Четыре-Ноль')
        self.assertIn('уже недействителен', self.redeem('4321', other).message)
        other.refresh_from_db()
        self.assertEqual(other.cards, 0)

    def test_expired_token_is_invalid(self):
        token = give_token(self.game, self.giver, 'square', '4321', minutes_ago=20.5)
        outcome = self.redeem('4321')
        self.assertIn('уже недействителен', outcome.message)
        self.player.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(self.player.cards, 0)
        self.assertIsNone(token.used_by)
        self.assertEqual(token.state(), 'expired')

    def test_token_just_inside_ttl_is_valid(self):
        give_token(self.game, self.giver, 'square', '4321', minutes_ago=19.5)
        self.assertTrue(self.redeem('4321').ok)

    def test_unknown_code(self):
        outcome = self.redeem('9999')
        self.assertIn('неверный код', outcome.message)
        self.player.refresh_from_db()
        self.assertEqual(self.player.wrong_codes, 1)

    def test_malformed_code_is_wrong(self):
        self.assertIn('неверный код', self.redeem('ZZZZ').message)

    def test_token_from_another_game_is_wrong(self):
        other_game = services.create_game('Другая', 'OTHER', 'game', 1)
        give_token(other_game, first_player(other_game), 'square', '4321')
        self.assertIn('неверный код', self.redeem('4321').message)

    def test_several_codes_in_one_input(self):
        give_token(self.game, self.giver, 'square', '4321')
        give_token(self.game, self.giver, 'square', '4322')
        outcomes = services.redeem_codes(self.player, '4321, 4322 9999')
        self.assertEqual([o.level for o in outcomes], ['ok', 'ok', 'bad'])
        self.player.refresh_from_db()
        self.assertEqual(self.player.cards, 2)

    def test_codes_page(self):
        login(self.client, self.game, self.player)
        give_token(self.game, self.giver, 'square', '4321')
        response = self.client.post(reverse('ls:codes'), {'codes': '4321'}, follow=True)
        self.assertContains(response, 'совпадение')
        self.assertContains(response, 'Ваш символ')

    def test_old_tokens_url_is_gone(self):
        login(self.client, self.game, self.player)
        self.assertEqual(self.client.get('/ls/post/tokens/').status_code, 404)
        self.assertContains(self.client.get(reverse('ls:post')), reverse('ls:codes'))


class ArchiveCodeTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=30)
        self.player = player_by(self.game, 'Семь-Три')

    def test_open_code_opens_entry(self):
        outcome = services.redeem_codes(self.player, '6060')[0]
        entry = get_content('game').archive_entry('6060')
        self.assertTrue(outcome.ok)
        self.assertIn(entry.text, outcome.payload)
        self.assertIn(entry.title, outcome.payload)
        opened = ArchiveOpen.objects.get()
        self.assertEqual((opened.player, opened.code, opened.via), (self.player, '6060', 'code'))

    def test_repeat_shows_text_again(self):
        services.redeem_codes(self.player, '6060')
        outcome = services.redeem_codes(self.player, '6060')[0]
        self.assertTrue(outcome.ok)
        self.assertIn(get_content('game').archive_entry('6060').text, outcome.payload)
        self.assertEqual(ArchiveOpen.objects.count(), 1)

    def test_locked_entry_is_unavailable(self):
        outcome = services.redeem_codes(self.player, '1407')[0]      # замок 70
        self.assertEqual(outcome.message, '1407: запись недоступна. Повторите позже.')
        self.assertEqual(outcome.payload, '')
        self.assertFalse(ArchiveOpen.objects.exists())
        self.player.refresh_from_db()
        self.assertEqual(self.player.wrong_codes, 0)

    def test_entry_opens_after_clock_shift(self):
        services.redeem_codes(self.player, '1407')
        self.game.clock_offset_seconds = 40 * 60
        self.game.save()
        self.player.refresh_from_db()
        self.assertTrue(services.redeem_codes(self.player, '1407')[0].ok)
        self.assertEqual(ArchiveOpen.objects.get().code, '1407')

    def test_before_start_all_locked(self):
        self.game.started_at = None
        self.game.save()
        self.player.refresh_from_db()
        self.assertIn('недоступна', services.redeem_codes(self.player, '6060')[0].message)

    def test_code_is_shared_between_players(self):
        other = player_by(self.game, 'Девять-Один')
        services.redeem_codes(self.player, '6060')
        self.assertTrue(services.redeem_codes(other, '6060')[0].ok)
        self.assertEqual(ArchiveOpen.objects.count(), 2)

    def test_payload_page_shows_text_with_line_breaks(self):
        login(self.client, self.game, self.player)
        response = self.client.post(reverse('ls:codes'), {'codes': '6060'}, follow=True)
        self.assertContains(response, 'Институт дальних экспедиций')
        self.assertContains(response, '<li class="payload')
        # поле без автофокуса, иначе страница уезжает к нему и текст записи остаётся за экраном
        self.assertNotContains(response, 'autofocus')

    def test_code_field_has_autofocus_without_messages(self):
        login(self.client, self.game, self.player)
        self.assertContains(self.client.get(reverse('ls:codes')), 'autofocus')

    def test_known_page_lists_archive_in_opening_order(self):
        login(self.client, self.game, self.player)
        ArchiveOpen.objects.create(player=self.player, code='3108', via='code',
                                   created_at=timezone.now() - timedelta(minutes=2))
        ArchiveOpen.objects.create(player=self.player, code='6060', via='card',
                                   created_at=timezone.now() - timedelta(minutes=1))
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertLess(html.index('3108 · Транспорт'), html.index('6060 · Основание'))
        self.assertIn('Из сводки потерь войны за пояс', html)
        ArchiveOpen.objects.create(player=self.player, code='2207', via='code')
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertIn('<br>— Читала.', html)


class GenderTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=120)
        self.staff = get_user_model().objects.create_user('master', password='x', is_staff=True)
        self.url = reverse('ls:master_action', args=[self.game.pk])

    def set_gender(self, callsign: str, gender: str) -> Player:
        player = player_by(self.game, callsign)
        player.gender = gender
        player.save()
        return player

    def test_create_game_takes_gender_from_content(self):
        game = services.create_game('Все', 'ALL', 'game', 3)
        for c in get_content('game').characters:
            self.assertEqual(game.players.get(callsign=c.callsign).gender, c.gender, c.callsign)
        self.assertEqual(player_by(game, 'Семь-Три').gender, 'm')
        self.assertEqual(player_by(game, 'Два-Два').gender, 'f')

    def test_game_genders_cover_every_character(self):
        self.set_gender('Семь-Три', 'f')
        genders = services.game_genders(self.game)                 # в игре одно звено из трёх
        self.assertEqual(len(genders), 15)
        self.assertEqual(genders['Семь-Три'], 'f')
        self.assertEqual(genders['Три-Три'], 'm')                  # звена нет в игре: пол из контента

    def test_master_changes_gender(self):
        player = player_by(self.game, 'Девять-Один')
        self.client.force_login(self.staff)
        self.client.post(self.url, {'op': 'gender:f', 'player': player.pk})
        player.refresh_from_db()
        self.assertEqual(player.gender, 'f')
        self.client.post(self.url, {'op': 'gender:m', 'player': player.pk})
        player.refresh_from_db()
        self.assertEqual(player.gender, 'm')
        self.client.post(self.url, {'op': 'gender:x', 'player': player.pk})
        player.refresh_from_db()
        self.assertEqual(player.gender, 'm')

    def test_master_page_shows_gender_and_current_name(self):
        player = player_by(self.game, 'Девять-Один')
        self.client.force_login(self.staff)
        url = reverse('ls:master_game', args=[self.game.pk])
        html = self.client.get(url).content.decode()
        self.assertIn('<th>Пол</th>', html)
        self.assertIn('Савва Гордеев', html)
        self.assertNotIn('Соня Гордеева', html)
        self.assertIn('не перерисовываются', html)
        self.client.post(self.url, {'op': 'gender:f', 'player': player.pk})
        html = self.client.get(url).content.decode()
        self.assertIn('Соня Гордеева', html)
        self.assertNotIn('Савва Гордеев', html)
        journal = self.client.get(reverse('ls:master_player', args=[self.game.pk, player.pk])).content.decode()
        self.assertIn('Соня Гордеева', journal)

    def test_archive_by_code_follows_gender(self):
        player = player_by(self.game, 'Девять-Один')
        self.assertIn('Корреспондент — Савва.', services.redeem_codes(player, '1455')[0].payload)
        self.set_gender('Девять-Один', 'f')
        player.refresh_from_db()
        payload = services.redeem_codes(player, '1455')[0].payload
        self.assertIn('Корреспондент — Соня.', payload)
        self.assertIn('«Она в списке.', payload)
        self.assertNotIn('{', payload)

    def test_archive_by_card_follows_gender(self):
        player = self.set_gender('Девять-Один', 'f')
        player.cards = 1
        player.save()
        for entry in get_content('game').archive_queue[:8]:
            ArchiveOpen.objects.create(player=player, code=entry.code, via='code')
        outcome = services.buy(player, 'archive')
        self.assertTrue(outcome.ok)
        self.assertIn('1456 · Мой сын', outcome.payload)
        self.assertIn('моя дочь', outcome.payload)
        self.assertNotIn('мой сын в списке', outcome.payload)
        self.assertNotIn('{', outcome.payload)
        self.assertEqual(Purchase.objects.get().payload, '1456 · Мой сын')

    def test_known_page_renders_archive_at_view_time(self):
        player = player_by(self.game, 'Девять-Один')
        ArchiveOpen.objects.create(player=player, code='1455', via='code')
        login(self.client, self.game, player)
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertIn('Корреспондент — Савва.', html)
        self.set_gender('Девять-Один', 'f')
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertIn('Корреспондент — Соня.', html)
        body = html.split('<h2>Что я знаю</h2>')[1]               # до этого места только CSS страницы
        self.assertNotIn('{', body)
        self.assertNotIn('|', body)

    def test_archive_of_character_outside_game_uses_content_gender(self):
        player = player_by(self.game, 'Семь-Три')
        outcome = services.redeem_codes(player, '3108')[0]       # Три-Три и Ноль-Семь из другого звена
        self.assertIn('Ахмеров.', outcome.payload)
        self.assertIn('вывел из горящего отсека', outcome.payload)

    def test_memory_purchase_stores_rendered_text(self):
        player = self.set_gender('Один-Один', 'f')
        player.cards = 2
        player.save()
        outcome = services.buy(player, 'self', 'arrival')
        self.assertEqual(Purchase.objects.get().payload, outcome.payload)
        self.assertNotIn('{', outcome.payload)
        four = player_by(self.game, 'Четыре-Ноль')
        four.cards = 2
        four.gender = 'f'
        four.save()
        outcome = services.buy(four, 'self', 'arrival')
        self.assertIn('Елена Брагина', outcome.payload)
        self.assertNotIn('Евгений Брагин', outcome.payload)
        four.gender = 'm'                                          # выданное не перерисовывается
        four.save()
        self.assertIn('Елена Брагина', Purchase.objects.get(player=four).payload)
        login(self.client, self.game, four)
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertIn('Елена Брагина', html)
        self.assertNotIn('{', html.split('<h2>Что я знаю</h2>')[1])

    def test_free_memory_follows_gender_at_view_time(self):
        player = player_by(self.game, 'Семь-Три')
        self.assertIn('«Илья»', services.free_memory(player)[0]['text'])
        self.set_gender('Семь-Три', 'f')
        player.refresh_from_db()
        self.assertIn('«Инна»', services.free_memory(player)[0]['text'])

    def test_dossier_purchase_stores_rendered_text(self):
        player = player_by(self.game, 'Семь-Три')
        player.cards = 5
        player.save()
        rng = random.Random(1)
        texts = [services.buy(player, 'flight', 'new', rng=rng).payload for _ in range(5)]
        self.assertTrue(any('учитель физики' in t for t in texts))
        self.assertTrue(all('{' not in t for t in texts))
        self.set_gender('Семь-Три', 'f')
        player.refresh_from_db()
        Purchase.objects.all().delete()
        player.cards = 5
        player.save()
        rng = random.Random(1)
        texts = [services.buy(player, 'flight', 'new', rng=rng).payload for _ in range(5)]
        self.assertTrue(any('учительница физики' in t for t in texts))
        self.assertTrue(all('{' not in t for t in texts))

    def test_no_player_visible_text_has_braces(self):
        game = services.create_game('Все', 'ALL', 'game', 3)
        game.started_at = timezone.now() - timedelta(minutes=120)
        game.save()
        codes = ' '.join(e.code for e in get_content('game').archive)
        purchases = (
            [('self', t) for t in ('link', 'body', 'death', 'arrival')]
            + [('flight', 'new')] * 5 + [('flight', 'case:0'), ('flight', 'case:0')]
            + [('program', q) for q in ('q1', 'q2', 'q3')] + [('archive', '')] * 10
        )
        for gender in ('m', 'f'):
            game.players.update(gender=gender, cards=40)
            shown = []
            for player in game.players.all():
                shown += [o.payload for o in services.redeem_codes(player, codes)]
                shown += [f['text'] for f in services.free_memory(player)]
                shown += [s['text'] for s in services.background_summaries(player)]
                for kind, choice in purchases:
                    player.refresh_from_db()
                    shown.append(services.buy(player, kind, choice).payload)
            shown += Purchase.objects.values_list('payload', flat=True)
            self.assertGreater(len(shown), 400)
            for text in shown:
                self.assertNotIn('{', text)
                self.assertNotIn('}', text)
            player = game.players.first()
            for url in ('ls:known', 'ls:cards', 'ls:post'):
                login(self.client, game, player)
                html = self.client.get(reverse(url)).content.decode()
                self.assertNotIn('{', html.split('<main')[1].split('</main>')[0], url)
            Purchase.objects.all().delete()


class WrongCodeTests(TestCase):
    def setUp(self):
        self.game = make_game(started_minutes_ago=40)
        self.player = player_by(self.game, 'Семь-Три')
        self.now = timezone.now()

    def wrong(self, n, now=None):
        return [services.redeem_codes(self.player, f'80{i:02d}', now=now or self.now)[0] for i in range(n)]

    def test_five_wrong_codes_block_for_two_minutes(self):
        outcomes = self.wrong(5)
        self.assertIn('Поле закрыто на 2 мин', outcomes[-1].message)
        self.player.refresh_from_db()
        self.assertEqual(self.player.codes_blocked_until, self.now + timedelta(minutes=2))
        outcome = services.redeem_codes(self.player, '6060', now=self.now + timedelta(seconds=30))[0]
        self.assertIn('Поле закрыто. Подождите', outcome.message)
        self.assertFalse(ArchiveOpen.objects.exists())

    def test_four_wrong_codes_do_not_block(self):
        self.wrong(4)
        self.assertTrue(services.redeem_codes(self.player, '6060', now=self.now)[0].ok)

    def test_block_ends(self):
        self.wrong(5)
        later = self.now + timedelta(minutes=2, seconds=1)
        self.assertTrue(services.redeem_codes(self.player, '6060', now=later)[0].ok)

    def test_right_code_resets_counter(self):
        self.wrong(4)
        services.redeem_codes(self.player, '6060', now=self.now)
        self.player.refresh_from_db()
        self.assertEqual(self.player.wrong_codes, 0)
        self.wrong(4)
        self.assertTrue(services.redeem_codes(self.player, '3108', now=self.now)[0].ok)

    def test_matching_token_resets_counter(self):
        self.wrong(4)
        give_token(self.game, player_by(self.game, 'Два-Два'), 'square', '4321')
        services.redeem_codes(self.player, '4321')
        self.player.refresh_from_db()
        self.assertEqual(self.player.wrong_codes, 0)

    def test_locked_archive_code_does_not_reset_counter(self):
        self.wrong(4)
        services.redeem_codes(self.player, '1407', now=self.now)
        self.player.refresh_from_db()
        self.assertEqual(self.player.wrong_codes, 4)

    def test_rest_of_list_is_blocked_after_fifth(self):
        outcomes = services.redeem_codes(self.player, '8001 8002 8003 8004 8005 6060', now=self.now)
        self.assertEqual(len(outcomes), 6)
        self.assertIn('Поле закрыто. Подождите', outcomes[-1].message)
        self.assertFalse(ArchiveOpen.objects.exists())

    def test_codes_page_tells_how_long_to_wait(self):
        login(self.client, self.game, self.player)
        for i in range(5):
            self.client.post(reverse('ls:codes'), {'codes': f'80{i:02d}'})
        response = self.client.get(reverse('ls:codes'))
        self.assertContains(response, 'Поле закрыто. Подождите')
        self.assertContains(response, 'disabled')


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
        at_minute(self.game, 120)
        self.player.cards = 0
        self.player.save()
        for kind, choice in (('program', 'q1'), ('self', 'link'), ('flight', 'new'), ('archive', '')):
            outcome = services.buy(self.player, kind, choice)
            self.assertEqual(outcome.level, 'bad', kind)
        self.assertFalse(Purchase.objects.exists())

    def test_locked_by_time_does_not_spend_card(self):
        at_minute(self.game, 0)
        for kind, choice in (('program', 'q1'), ('self', 'link'), ('flight', 'new')):
            self.assertIn('Пока нет', services.buy(self.player, kind, choice).message, kind)
        self.assertEqual(self.reload().cards, 10)
        self.assertFalse(Purchase.objects.exists())

    def test_before_start_all_locks_closed(self):
        for kind, choice in (('program', 'q1'), ('self', 'link'), ('flight', 'new'), ('archive', '')):
            self.assertIn('Пока нет', services.buy(self.player, kind, choice).message, kind)

    def test_choice_is_required_and_validated(self):
        at_minute(self.game, 120)
        for kind, choice in (('self', ''), ('self', 'name'), ('self', 'zzz'), ('flight', ''), ('flight', 'case:0'),
                             ('flight', 'case:x'), ('flight', 'case:-1'), ('program', ''), ('program', 'p3'),
                             ('program', 'q9')):
            self.assertEqual(services.buy(self.player, kind, choice).level, 'warn', (kind, choice))
        self.assertEqual(self.reload().cards, 10)
        self.assertFalse(Purchase.objects.exists())

    # Память: тема на выбор

    def test_memory_themes_are_locked_by_time(self):
        for minute, theme, opens in ((39, 'link', False), (40, 'link', True), (54, 'body', False),
                                     (55, 'body', True), (64, 'death', False), (65, 'death', True),
                                     (79, 'arrival', False), (80, 'arrival', True)):
            Purchase.objects.all().delete()
            self.player.cards = 10
            self.player.save()
            at_minute(self.game, minute)
            outcome = services.buy(self.reload(), 'self', theme)
            self.assertEqual(outcome.ok, opens, (minute, theme))
            self.assertEqual(self.reload().cards, 9 if opens else 10, (minute, theme))

    def test_memory_theme_text_and_independence(self):
        at_minute(self.game, 100)
        character = get_content('game').character(self.player.character_id)
        # без предыдущих покупок: сразу «Попадание», затем «Смерть»
        outcome = services.buy(self.player, 'self', 'arrival')
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.payload, shown(self.game, character.memory_pack('arrival').text))
        self.assertTrue(services.buy(self.reload(), 'self', 'death').ok)
        self.assertEqual(self.reload().cards, 8)
        self.assertEqual(list(self.player.purchases.values_list('key', flat=True)), ['arrival', 'death'])

    def test_memory_theme_only_once(self):
        at_minute(self.game, 100)
        self.assertTrue(services.buy(self.player, 'self', 'link').ok)
        outcome = services.buy(self.reload(), 'self', 'link')
        self.assertEqual(outcome.level, 'warn')
        self.assertIn('уже получена', outcome.message)
        self.assertEqual(self.reload().cards, 9)
        self.assertEqual(self.player.purchases.count(), 1)

    def test_memory_body_does_not_depend_on_training(self):
        at_minute(self.game, 55)
        self.assertEqual(self.player.sync_values(), {'nav': 0, 'move': 0, 'parts': 0, 'sense': 0, 'fire': 0})
        self.assertTrue(services.buy(self.player, 'self', 'body').ok)

    def test_memory_options_show_locked_themes(self):
        at_minute(self.game, 45)
        options = {o.key: o for o in services.memory_options(self.player, self.game.game_minutes())}
        self.assertEqual([o.title for o in options.values()], ['Связь', 'Тело', 'Смерть', 'Попадание'])
        self.assertEqual(options['link'].state, 'open')
        self.assertEqual(options['body'].state, 'locked')
        self.assertEqual(options['body'].note, 'пока недоступно')
        services.buy(self.player, 'self', 'link')
        options = {o.key: o for o in services.memory_options(self.reload(), self.game.game_minutes())}
        self.assertEqual(options['link'].state, 'done')

    def test_cards_page_renders_locked_theme_disabled_without_spoiler(self):
        at_minute(self.game, 45)
        login(self.client, self.game, self.player)
        html = self.client.get(reverse('ls:cards')).content.decode()
        for title in MEMORY_THEME_TITLES.values():
            self.assertIn(f'<span>{title}</span>', html)
        block = html[html.index('<span>Тело</span>') - 400:html.index('<span>Тело</span>') + 200]
        self.assertIn('disabled', block)
        self.assertIn('пока недоступно', block)
        link_block = html[html.index('<span>Связь</span>') - 300:html.index('<span>Связь</span>')]
        self.assertNotIn('disabled', link_block)
        body = get_content('game').character(self.player.character_id).memory_pack('arrival').text
        self.assertNotIn(body[:30], html)

    def test_cards_post_with_choice(self):
        at_minute(self.game, 45)
        login(self.client, self.game, self.player)
        response = self.client.post(reverse('ls:cards'), {'kind': 'self', 'choice': 'link'}, follow=True)
        self.assertContains(response, 'Получена тема «Связь»')
        self.assertEqual(self.reload().cards, 9)
        response = self.client.post(reverse('ls:cards'), {'kind': 'self', 'choice': 'body'}, follow=True)
        self.assertContains(response, 'Пока нет')
        self.assertEqual(self.reload().cards, 9)

    # Память: бесплатные пакеты

    def test_free_memory_packs_cost_nothing(self):
        character = get_content('game').character(self.player.character_id)
        before = services.free_memory(self.player)
        self.assertEqual([f['text'] for f in before], [shown(self.game, character.memory_pack('name').text)])
        at_minute(self.game, 0)
        after = services.free_memory(self.reload())
        self.assertEqual([f['text'] for f in after],
                         [shown(self.game, character.memory_pack(t).text) for t in ('name', 'memory')])
        self.assertEqual(self.reload().cards, 10)
        self.assertFalse(Purchase.objects.exists())

    def test_free_memory_on_known_page_and_post_link(self):
        login(self.client, self.game, self.player)
        character = get_content('game').character(self.player.character_id)
        name = shown(self.game, character.memory_pack('name').text)
        memory = shown(self.game, character.memory_pack('memory').text)
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertIn(name, html)
        self.assertNotIn(memory[:40], html)                    # пакет № 2 только после старта
        at_minute(self.game, 0)
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertIn(memory[:40], html)
        post = self.client.get(reverse('ls:post')).content.decode()
        self.assertIn('Ваши первые воспоминания', post)
        self.assertIn(reverse('ls:known') + '#self', post)

    # Досье: дела

    def test_dossier_first_buy_opens_case_a_without_callsign(self):
        at_minute(self.game, 30)
        content = get_content('game')
        outcome = services.buy(self.player, 'flight', 'new', rng=random.Random(3))
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.payload.startswith('Дело А · Жизнь\n\n'))
        purchase = Purchase.objects.get()
        char_id, part = purchase.key.split(':')
        self.assertEqual(part, 'life')
        self.assertIn(char_id, {c.id for c in content.flight_characters(self.player.character_id)})
        self.assertEqual(purchase.payload, shown(self.game, content.character(char_id).dossier['life']))
        callsigns = [c.callsign.casefold() for c in content.characters]
        self.assertFalse(any(cs in outcome.payload.casefold() for cs in callsigns))

    def test_dossier_new_cases_get_next_labels_and_never_repeat(self):
        at_minute(self.game, 30)
        labels, chars = [], []
        for n in range(5):
            outcome = services.buy(self.reload(), 'flight', 'new', rng=random.Random(n))
            self.assertTrue(outcome.ok)
            labels.append(outcome.payload.split(' · ')[0])
            chars.append(Purchase.objects.last().key.split(':')[0])
        self.assertEqual(labels, ['Дело А', 'Дело Б', 'Дело В', 'Дело Г', 'Дело Д'])
        self.assertEqual(len(set(chars)), 5)
        outcome = services.buy(self.reload(), 'flight', 'new')
        self.assertIn('Все дела уже открыты', outcome.message)
        self.assertEqual(self.reload().cards, 5)

    def test_dossier_continue_case_in_order_with_locks(self):
        at_minute(self.game, 30)
        services.buy(self.player, 'flight', 'new', rng=random.Random(1))       # Дело А
        services.buy(self.reload(), 'flight', 'new', rng=random.Random(2))     # Дело Б
        cards = self.reload().cards
        self.assertIn('Пока нет', services.buy(self.reload(), 'flight', 'case:0').message)
        self.assertEqual(self.reload().cards, cards)
        at_minute(self.game, 64)
        self.assertIn('Пока нет', services.buy(self.reload(), 'flight', 'case:0').message)
        at_minute(self.game, 65)
        content = get_content('game')
        char_a = Purchase.objects.first().key.split(':')[0]
        outcome = services.buy(self.reload(), 'flight', 'case:0')
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.payload.startswith('Дело А · Смерть'))
        self.assertEqual(Purchase.objects.last().payload, shown(self.game, content.character(char_a).dossier['death']))
        self.assertIn('Пока нет', services.buy(self.reload(), 'flight', 'case:0').message)    # попадание с 80
        at_minute(self.game, 80)
        outcome = services.buy(self.reload(), 'flight', 'case:0')
        self.assertTrue(outcome.payload.startswith('Дело А · Попадание'))
        self.assertIn('Дело уже закрыто', services.buy(self.reload(), 'flight', 'case:0').message)
        self.assertEqual(self.reload().cards, cards - 2)

    def test_dossier_case_can_be_continued_without_other_lives(self):
        at_minute(self.game, 90)
        services.buy(self.player, 'flight', 'new')
        self.assertTrue(services.buy(self.reload(), 'flight', 'case:0').ok)
        self.assertTrue(services.buy(self.reload(), 'flight', 'case:0').ok)
        self.assertEqual(self.player.purchases.count(), 3)
        self.assertEqual({k.split(':')[0] for k in self.player.purchases.values_list('key', flat=True)},
                         {Purchase.objects.first().key.split(':')[0]})

    def test_dossier_new_case_is_locked_before_25(self):
        at_minute(self.game, 24)
        self.assertIn('Пока нет', services.buy(self.player, 'flight', 'new').message)
        self.assertEqual(self.reload().cards, 10)
        at_minute(self.game, 25)
        self.assertTrue(services.buy(self.reload(), 'flight', 'new').ok)

    def test_dossier_only_own_flight(self):
        content = get_content('game')
        game = services.create_game('Три звена', 'THREE', 'game', 3)
        at_minute(game, 120)
        for player in game.players.all():
            player.cards = 5
            player.save()
            for _ in range(5):
                self.assertTrue(services.buy(player, 'flight', 'new').ok)
            own = {c.id for c in content.flight_characters(player.character_id)}
            keys = list(player.purchases.values_list('key', flat=True))
            self.assertEqual({k.split(':')[0] for k in keys}, own, player.callsign)
            self.assertEqual(len(keys), 5)

    def test_dossier_labels_are_independent_between_players(self):
        at_minute(self.game, 30)
        seen = set()
        for player in self.game.players.all():
            player.cards = 1
            player.save()
            outcome = services.buy(player, 'flight', 'new')
            self.assertTrue(outcome.payload.startswith('Дело А'))
            seen.add(Purchase.objects.filter(player=player).get().key)
        self.assertGreater(len(seen), 1)            # у одной метки у разных игроков разные персонажи

    def test_dossier_case_label_is_permanent(self):
        at_minute(self.game, 100)
        services.buy(self.player, 'flight', 'new', rng=random.Random(5))
        services.buy(self.reload(), 'flight', 'new', rng=random.Random(6))
        services.buy(self.reload(), 'flight', 'case:1')
        cases = services.dossier_cases_of(self.reload())
        self.assertEqual([(c['label'], c['parts']) for c in cases],
                         [('Дело А', ['life']), ('Дело Б', ['life', 'death'])])

    def test_dossier_options_states(self):
        at_minute(self.game, 30)
        minutes = self.game.game_minutes()
        options = services.flight_options(self.player, minutes)
        self.assertEqual([(o.key, o.state) for o in options], [('new', 'open')])
        services.buy(self.player, 'flight', 'new')
        options = services.flight_options(self.reload(), self.game.game_minutes())
        self.assertEqual([(o.key, o.title, o.state, o.note) for o in options], [
            ('new', 'Новое дело', 'open', ''),
            ('case:0', 'Дело А: продолжить', 'locked', 'пока недоступно'),
        ])

    def test_dossier_known_page_groups_by_case(self):
        at_minute(self.game, 100)
        login(self.client, self.game, self.player)
        content = get_content('game')
        services.buy(self.player, 'flight', 'new', rng=random.Random(8))
        services.buy(self.reload(), 'flight', 'new', rng=random.Random(9))
        services.buy(self.reload(), 'flight', 'case:0')
        html = self.client.get(reverse('ls:known')).content.decode()
        self.assertLess(html.index('Дело А'), html.index('Дело Б'))
        key_a = Purchase.objects.first().key.split(':')[0]
        life, death = (shown(self.game, content.character(key_a).dossier[p]) for p in ('life', 'death'))
        self.assertLess(html.index(life[:30]), html.index(death[:30]))
        self.assertLess(html.index(death[:30]), html.index('Дело Б'))

    # Программа: сводки и вопросы

    def test_background_summaries_by_time(self):
        self.assertEqual(services.background_summaries(self.player), [])          # до старта
        program = {p.id: p.text for p in get_content('game').program}
        for minute, expected in ((0, []), (24, []), (25, ['p1']), (35, ['p1', 'p2']), (54, ['p1', 'p2']),
                                 (55, ['p1', 'p2', 'p4']), (84, ['p1', 'p2', 'p4']),
                                 (85, ['p1', 'p2', 'p4', 'p7'])):
            at_minute(self.game, minute)
            texts = [s['text'] for s in services.background_summaries(self.reload())]
            self.assertEqual(texts, [program[i] for i in expected], minute)
        self.assertEqual(self.reload().cards, 10)
        self.assertFalse(Purchase.objects.exists())

    def test_background_summaries_on_post_and_known_without_sound(self):
        login(self.client, self.game, self.player)
        program = {p.id: p.text for p in get_content('game').program}
        at_minute(self.game, 30)
        post = self.client.get(reverse('ls:post')).content.decode()
        self.assertIn(program['p1'][:40], post)
        self.assertNotIn(program['p2'][:40], post)
        at_minute(self.game, 40)
        for url in ('ls:post', 'ls:known'):
            html = self.client.get(reverse(url)).content.decode()
            self.assertIn(program['p2'][:40], html, url)
            self.assertNotIn('<audio', html)
            self.assertNotIn(program['p3'][:30], html, url)        # платное не просачивается
            self.assertNotIn(program['p6'][:30], html, url)

    def test_program_questions_by_time(self):
        program = {p.id: p.text for p in get_content('game').program}
        for minute, question, opens, packs in ((44, 'q1', False, ()), (45, 'q1', True, ('p3', 'p5')),
                                               (59, 'q2', False, ()), (60, 'q2', True, ('p6',)),
                                               (79, 'q3', False, ()), (80, 'q3', True, ('p8',))):
            Purchase.objects.all().delete()
            self.player.cards = 10
            self.player.save()
            at_minute(self.game, minute)
            outcome = services.buy(self.reload(), 'program', question)
            self.assertEqual(outcome.ok, opens, (minute, question))
            self.assertEqual(self.reload().cards, 9 if opens else 10)
            if opens:
                self.assertEqual(outcome.payload, '\n\n'.join(program[p] for p in packs))

    def test_program_question_once_and_independent(self):
        at_minute(self.game, 100)
        self.assertTrue(services.buy(self.player, 'program', 'q3').ok)          # предыдущие не нужны
        outcome = services.buy(self.reload(), 'program', 'q3')
        self.assertEqual(outcome.level, 'warn')
        self.assertIn('уже задан', outcome.message)
        self.assertTrue(services.buy(self.reload(), 'program', 'q1').ok)
        self.assertTrue(services.buy(self.reload(), 'program', 'q2').ok)
        self.assertEqual(self.reload().cards, 7)
        options = services.program_options(self.player, self.game.game_minutes())
        self.assertEqual([o.state for o in options], ['done', 'done', 'done'])

    def test_program_options_show_locked_questions_inactive(self):
        at_minute(self.game, 50)
        options = services.program_options(self.player, self.game.game_minutes())
        self.assertEqual([(o.title, o.state) for o in options], [
            ('Что даёт переход?', 'open'), ('Что со мной сейчас?', 'locked'), ('Как набирали программу?', 'locked')])
        login(self.client, self.game, self.player)
        html = self.client.get(reverse('ls:cards')).content.decode()
        block = html[html.index('<span>Что со мной сейчас?</span>') - 400:]
        self.assertIn('disabled', block[:420])
        self.assertIn('пока недоступно', block[:600])

    def test_known_page_shows_program_questions_and_summaries(self):
        at_minute(self.game, 100)
        login(self.client, self.game, self.player)
        services.buy(self.player, 'program', 'q1')
        html = self.client.get(reverse('ls:known')).content.decode()
        program = {p.id: p.text for p in get_content('game').program}
        for pack in ('p1', 'p2', 'p4', 'p7', 'p3', 'p5'):
            self.assertIn(program[pack][:30], html, pack)
        self.assertNotIn(program['p8'][:30], html)
        self.assertIn('Что даёт переход?', html)

    def test_radio_levels(self):
        self.assertTrue(services.buy(self.player, 'radio').ok)
        self.assertEqual(self.reload().radio_level, 1)
        self.assertTrue(services.buy(self.player, 'radio').ok)
        self.assertEqual(self.reload().radio_level, 2)
        self.assertEqual(services.buy(self.player, 'radio').level, 'warn')
        self.assertEqual(self.reload().cards, 8)

    def test_known_page_lists_purchases_in_order(self):
        at_minute(self.game, 120)
        login(self.client, self.game, self.player)
        self.client.post(reverse('ls:cards'), {'kind': 'program', 'choice': 'q2'})
        self.client.post(reverse('ls:cards'), {'kind': 'program', 'choice': 'q1'})
        html = self.client.get(reverse('ls:known')).content.decode()
        program = {p.id: p.text for p in get_content('game').program}
        self.assertLess(html.index(program['p6'][:40]), html.index(program['p3'][:40]))

    def test_cards_page_buttons(self):
        login(self.client, self.game, self.player)
        response = self.client.get(reverse('ls:cards'))
        for title in ('О себе', 'О звене', 'О программе', 'Архив', 'Рация'):
            self.assertContains(response, title)
        html = response.content.decode()
        self.assertLess(html.index('О программе'), html.index('Архив'))
        self.assertLess(html.index('Архив'), html.index('Рация'))

    def test_archive_card_follows_queue(self):
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        queue = get_content('game').archive_queue
        for entry in queue[:3]:
            outcome = services.buy(self.player, 'archive')
            self.assertTrue(outcome.ok)
            self.assertIn(shown(self.game, entry.text), outcome.payload)
        self.assertEqual(list(self.player.archive_opens.values_list('code', 'via')),
                         [(e.code, 'card') for e in queue[:3]])
        self.assertEqual(self.reload().cards, 7)

    def test_archive_card_goes_to_journal(self):
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        services.buy(self.player, 'archive')
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.kind, 'archive')
        self.assertIn('6060', purchase.payload)

    def test_archive_card_skips_entry_opened_by_code(self):
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        services.redeem_codes(self.player, '6060')
        outcome = services.buy(self.player, 'archive')
        self.assertIn(shown(self.game, get_content('game').archive_queue[1].text), outcome.payload)
        self.assertEqual(self.player.archive_opens.count(), 2)

    def test_code_after_card_does_not_duplicate(self):
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        services.buy(self.player, 'archive')
        self.assertTrue(services.redeem_codes(self.player, '6060')[0].ok)
        self.assertEqual(self.player.archive_opens.count(), 1)

    def test_archive_card_locked_does_not_spend(self):
        self.game.started_at = timezone.now() - timedelta(minutes=10)
        self.game.save()
        outcome = services.buy(self.player, 'archive')
        self.assertIn('Пока нет', outcome.message)
        self.assertEqual(self.reload().cards, 10)
        self.assertFalse(self.player.archive_opens.exists())

    def test_archive_card_before_start_is_locked(self):
        self.assertIn('Пока нет', services.buy(self.player, 'archive').message)

    def test_archive_card_when_everything_open(self):
        self.game.started_at = timezone.now() - timedelta(minutes=200)
        self.game.save()
        self.player.cards = 11
        self.player.save()
        for _ in range(10):
            self.assertTrue(services.buy(self.player, 'archive').ok)
        outcome = services.buy(self.player, 'archive')
        self.assertIn('Весь архив уже получен', outcome.message)
        self.assertEqual(self.reload().cards, 1)
        # записи вне очереди остаются доступны только кодом
        self.assertEqual(self.player.archive_opens.count(), 10)
        self.assertTrue(services.redeem_codes(self.player, '1407')[0].ok)
        self.assertEqual(self.player.archive_opens.count(), 11)


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

    def test_create_game_makes_players(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('ls:master_index'), {
            'title': 'Игра', 'code': 'abc', 'content': 'game', 'flights': '3',
        })
        game = Game.objects.get(code='abc')
        self.assertRedirects(response, reverse('ls:master_game', args=[game.pk]))
        self.assertEqual([f.name for f in game.flights.all()], ['Звено 1', 'Звено 2', 'Звено 3'])
        self.assertEqual(game.players.count(), 15)
        self.assertEqual(game.tokens.count(), 0)
        self.assertEqual(game.players.filter(is_imp=True).count(), 0)
        callsigns = set(game.players.values_list('callsign', flat=True))
        self.assertEqual(len(callsigns), 15)
        self.assertIn('Семь-Три', callsigns)
        self.assertIn('Восемь-Три', callsigns)

    def test_flights_count_takes_first_flights_from_content(self):
        game = make_game(2)
        self.assertEqual(game.flights.count(), 2)
        self.assertEqual(game.players.count(), 10)
        self.assertFalse(game.players.filter(callsign='Девять-Девять').exists())

    def test_flights_count_is_limited_by_content(self):
        self.client.force_login(self.staff)
        for flights in ('0', '4'):
            self.client.post(reverse('ls:master_index'), {
                'title': 'Игра', 'code': 'abc', 'content': 'game', 'flights': flights,
            })
        self.assertFalse(Game.objects.exists())
        with self.assertRaises(ValueError):
            services.create_game('Игра', 'abc', 'game', 4)

    def test_single_flight_has_plain_callsigns(self):
        game = make_game(1)
        self.assertTrue(game.players.filter(callsign='Семь-Три').exists())
        self.assertEqual(game.players.count(), 5)

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
        services.issue_tokens(game, [player])
        for url in (reverse('ls:master_index'), reverse('ls:master_game', args=[game.pk]),
                    reverse('ls:master_player', args=[game.pk, player.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_no_imp_marks_in_master_pages(self):
        game = make_game(3, started_minutes_ago=3)
        self.assertFalse(game.players.filter(is_imp=True).exists())
        self.client.force_login(self.staff)
        player = first_player(game)
        for url in (reverse('ls:master_game', args=[game.pk]), reverse('ls:master_player', args=[game.pk, player.pk])):
            self.assertNotIn('ИМП', self.client.get(url).content.decode())
        login(self.client, game, player)
        self.assertNotIn('ИМП', self.client.get(reverse('ls:post')).content.decode())

    def test_game_page_has_dark_block_and_archive_column(self):
        game = make_game(2, started_minutes_ago=3)
        player = first_player(game)
        ArchiveOpen.objects.create(player=player, code='6060', via='code')
        self.client.force_login(self.staff)
        html = self.client.get(reverse('ls:master_game', args=[game.pk])).content.decode()
        self.assertIn('В темноту', html)
        self.assertIn('Звено 2', html)
        self.assertIn('<th>Архив</th>', html)
        self.assertEqual(html.count('name="players"'), 10)

    def test_player_journal_lists_tokens_and_archive(self):
        game = make_game(started_minutes_ago=30)
        player = player_by(game, 'Семь-Три')
        giver = player_by(game, 'Два-Два')
        give_token(game, player, 'cross', '4321')
        give_token(game, giver, 'square', '4322')
        give_token(game, giver, 'star', '4323')
        give_token(game, giver, 'star', '4324', minutes_ago=30)
        services.redeem_codes(player, '4322 4323 6060')
        self.client.force_login(self.staff)
        html = self.client.get(reverse('ls:master_player', args=[game.pk, player.pk])).content.decode()
        for text in ('Выданные жетоны', 'Введённые жетоны', '4321', '4322', '4323', 'ждёт', 'Основание', 'код'):
            self.assertIn(text, html)
        giver_html = self.client.get(reverse('ls:master_player', args=[game.pk, giver.pk])).content.decode()
        for text in ('совпал', 'сгорел', 'просрочен', player.callsign):
            self.assertIn(text, giver_html)

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
        other = services.create_game('Другая', 'OTHER', 'game', 1)
        stranger = first_player(other)
        self.client.force_login(self.staff)
        response = self.client.post(reverse('ls:master_action', args=[game.pk]),
                                    {'op': 'cards:1', 'player': stranger.pk})
        self.assertEqual(response.status_code, 404)
