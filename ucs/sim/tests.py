"""Тесты движка: каждое правило модели проверяется отдельно.

Модуль автономный, поэтому тесты запускаются и так:

    python3 -m unittest sim.tests -v
"""

from __future__ import annotations

import random
import unittest

from .engine import (
    SUBSTITUTE_ANY_MARK,
    SUBSTITUTE_RETURN_MOVE,
    SUBSTITUTE_SKIP_GUEST,
    GroupState,
    Phase,
    RuleError,
    Simulation,
)
from .loader import TreeError, get_tree, load_tree, validate
from .montecarlo import run_batch, run_once
from .strategies import get_strategy


def make_sim(guest_probability: float = 0.0, seed: int = 0) -> Simulation:
    return Simulation(
        tree=get_tree(),
        rng=random.Random(seed),
        guest_probability=guest_probability,
    )


def place(
    sim: Simulation,
    node_id: str,
    marks=(),
    nodes_before: int = 8,
    guest: bool = True,
) -> Simulation:
    """Поставить группу на карточку с заданными метками, минуя предыдущие узлы.

    on_enter карточки не применяется: тестам нужен точный контроль над метками.
    """
    st = sim.state
    st.node = node_id
    st.visited = ['x'] * nodes_before + [node_id]
    st.marks = set(marks)
    st.marks_on_entry = set(marks)
    st.guest_present = guest
    st.guest_resolved = not guest
    st.phase = Phase.GUEST if guest else Phase.CHOICE
    return sim


def walk(sim: Simulation, *keys: str) -> Simulation:
    """Провести группу по цепочке выборов, разрешая сцены гостей без хода Джона."""
    for key in keys:
        if sim.state.phase is Phase.GUEST:
            sim.resolve_guest()
        if sim.state.phase is Phase.FINAL:
            sim.choose_final(key)
            continue
        if not sim.node.choices:
            sim.advance()
            if sim.state.phase is Phase.GUEST:
                sim.resolve_guest()
        sim.apply_choice(key)
    if sim.state.phase is Phase.GUEST:
        sim.resolve_guest()
    return sim


class TreeTests(unittest.TestCase):
    def test_loads_and_validates(self):
        tree = get_tree()
        self.assertEqual(tree.id, 'false-vacuum')
        self.assertEqual(len(tree.nodes), 21)
        self.assertEqual(len(tree.finals), 4)
        self.assertEqual(len(tree.marks), 6)

    def test_card_count_matches_model(self):
        """Модель обещает 25 карточек: 21 узел плюс 4 финала."""
        tree = get_tree()
        self.assertEqual(len(tree.nodes) + len(tree.finals), 25)

    def test_nine_cards_per_run(self):
        """Группа за игру проходит 8 узлов плюс карточку финала."""
        tree = get_tree()
        slots = {(n.act, n.slot) for n in tree.nodes.values()}
        self.assertEqual(len(slots), 8)

    def test_lamps_per_act(self):
        """Лампы: 1 в первом акте, по 2 во втором и третьем."""
        tree = get_tree()
        per_act = {}
        for node in tree.lamp_nodes():
            per_act.setdefault(node.act, set()).add(node.slot)
        self.assertEqual({a: len(s) for a, s in per_act.items()}, {1: 1, 2: 2, 3: 2})

    def test_act_fits_forty_minutes(self):
        tree = get_tree()
        for act in (1, 2, 3):
            slots = {}
            for node in tree.nodes_of_act(act):
                slots.setdefault(node.slot, node.minutes)
            self.assertLessEqual(sum(slots.values()), 40, f'акт {act}')

    def test_validation_catches_unknown_mark(self):
        raw = {
            'meta': {'id': 'broken'},
            'marks': {'ШКАЛА': 'x'},
            'finals': {'F3': {'title': 'Молчание', 'opened_by': []}},
            'start': '1.1',
            'nodes': {
                '1.1': {
                    'act': 1,
                    'slot': 1,
                    'minutes': 1,
                    'title': 'x',
                    'final_choice': True,
                    'choices': [{'key': 'F3'}],
                    'on_enter': {'add': [{'grant': ['НЕТТАКОЙ']}]},
                }
            },
        }
        from .loader import parse_tree

        with self.assertRaises(TreeError) as ctx:
            parse_tree(raw)
        self.assertIn('НЕТТАКОЙ', str(ctx.exception))


class MarkTests(unittest.TestCase):
    def test_marks_do_not_stack(self):
        """Карточка, выдающая метку, которая уже есть, не выдаёт ничего."""
        sim = make_sim()
        sim.state.marks.add('ШКАЛА')
        before = set(sim.state.marks)
        sim._grant(['ШКАЛА'])
        self.assertEqual(sim.state.marks, before)

    def test_fallback_mark_on_2_3b(self):
        """2.3b: если МЕХАНИЗМ уже есть, вместо него идёт РАСЧЁТ."""
        # 1.1 A -> 1.2a C -> 1.3c -> 2.1 C -> 2.2c (даёт МЕХАНИЗМ) -> 2.3b
        sim = walk(make_sim(), 'A', 'C', 'C', 'B')
        self.assertEqual(sim.state.node, '2.3b')
        self.assertIn('ТРИГГЕР', sim.state.marks)
        self.assertIn('МЕХАНИЗМ', sim.state.marks)
        self.assertIn('РАСЧЁТ', sim.state.marks)

    def test_no_fallback_when_mark_is_new(self):
        """2.3b без МЕХАНИЗМА выдаёт ТРИГГЕР и МЕХАНИЗМ, но не РАСЧЁТ."""
        # 1.1 A -> 1.2a B -> 1.3b (РАСЧЁТ) -> 2.1 A -> 2.2a C -> 2.3c
        sim = walk(make_sim(), 'A', 'C', 'A', 'B')
        self.assertEqual(sim.state.node, '2.3b')
        self.assertIn('ТРИГГЕР', sim.state.marks)
        self.assertIn('МЕХАНИЗМ', sim.state.marks)

    def test_2_1_removes_scale(self):
        """2.1 аннулирует число: метка ШКАЛА снимается."""
        sim = walk(make_sim(), 'A', 'A')  # 1.1 A -> 1.2a A -> 1.3a
        self.assertEqual(sim.state.node, '1.3a')
        self.assertIn('ШКАЛА', sim.state.marks)
        sim.advance()
        self.assertEqual(sim.state.node, '2.1')
        self.assertNotIn('ШКАЛА', sim.state.marks)

    def test_choice_mark_doubt_on_shortcut(self):
        """1.2a вариант A — срезанный угол, даёт СОМНЕНИЕ."""
        sim = walk(make_sim(), 'A', 'A')
        self.assertIn('СОМНЕНИЕ', sim.state.marks)


class GuestTests(unittest.TestCase):
    def test_guest_takes_mark(self):
        """1.3b: гость уносит лист, РАСЧЁТ теряется."""
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A')
        sim.apply_choice('B')  # 1.2a B -> 1.3b
        self.assertEqual(sim.state.node, '1.3b')
        self.assertTrue(sim.state.guest_present)
        self.assertIn('РАСЧЁТ', sim.state.marks)
        sim.resolve_guest()
        self.assertNotIn('РАСЧЁТ', sim.state.marks)

    def test_guest_copies_number_and_mark_stays(self):
        """1.3a — исключение: гость снимает копию, ШКАЛА остаётся."""
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A')
        sim.apply_choice('A')  # -> 1.3a
        self.assertEqual(sim.state.node, '1.3a')
        sim.resolve_guest()
        self.assertIn('ШКАЛА', sim.state.marks)
        self.assertIn('number_left_room', sim.state.flags)

    def test_guest_disables_choice_on_2_2a(self):
        """2.2a: унесённая распечатка снимает вариант B, метки не трогает."""
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A', 'B')  # -> 1.3b, гость разрешён
        sim.advance()  # -> 2.1
        marks_before = set(sim.state.marks)
        sim.apply_choice('A')  # -> 2.2a
        self.assertEqual(sim.state.node, '2.2a')
        sim.resolve_guest()
        self.assertIn('B', sim.state.disabled_choices)
        self.assertEqual(sim.state.marks, marks_before)
        self.assertNotIn('B', [c.key for c in sim.active_choices()])
        with self.assertRaises(RuleError):
            sim.apply_choice('B')

    def test_guest_takes_first_mark_in_order_on_3_1(self):
        """3.1: забирается первая метка по порядку, группа не выбирает."""
        sim = place(
            make_sim(guest_probability=1.0),
            '3.1',
            marks={'МЕХАНИЗМ', 'ШКАЛА', 'ТРИГГЕР'},
            nodes_before=6,
        )
        sim.resolve_guest()
        self.assertNotIn('ТРИГГЕР', sim.state.marks)
        self.assertIn('МЕХАНИЗМ', sim.state.marks)
        self.assertIn('ШКАЛА', sim.state.marks)

    def test_calculation_mark_shields_summary_and_guest_takes_person(self):
        """3.1: с РАСЧЁТОМ сводку унести нельзя — гость уносит человека."""
        sim = place(
            make_sim(guest_probability=1.0),
            '3.1',
            marks={'РАСЧЁТ', 'ТРИГГЕР'},
            nodes_before=6,
        )
        sim.resolve_guest(john_taken=True)
        self.assertIn('ТРИГГЕР', sim.state.marks)
        self.assertIn('РАСЧЁТ', sim.state.marks)
        self.assertEqual(sim.state.absent_from_index, 7)
        self.assertTrue(sim.state.absent_is_john)

    def test_absent_john_cannot_move_next_node(self):
        """Если гость увёл Джона, ход в следующем узле потратить нельзя."""
        sim = place(
            make_sim(guest_probability=1.0), '3.1', marks={'РАСЧЁТ'}, nodes_before=6
        )
        sim.resolve_guest(john_taken=True)
        sim.apply_choice('A')  # -> 3.2a, следующий узел
        self.assertTrue(sim.state.player_absent)
        self.assertFalse(sim.can_john_move())
        self.assertFalse(sim.can_counter())

    def test_guest_without_action_cannot_be_countered(self):
        """3.1 без меток и без РАСЧЁТА: гостю нечего делать, ход не тратится."""
        sim = place(make_sim(guest_probability=1.0), '3.1', nodes_before=6)
        self.assertFalse(sim.can_counter())

    def test_false_flash_still_summons_guest(self):
        """Тупик 2.2b зажигает лампу: мироздание реагирует на попытку."""
        tree = get_tree()
        self.assertTrue(tree.node('2.2b').lamp)
        self.assertTrue(tree.node('2.2b').false_flash)
        self.assertTrue(tree.node('3.2d').lamp)
        self.assertTrue(tree.node('3.2d').false_flash)


class JohnMoveTests(unittest.TestCase):
    def test_three_moves_total(self):
        sim = make_sim()
        self.assertEqual(sim.state.john_moves_left, 3)

    def test_no_move_in_two_consecutive_nodes(self):
        """Между двумя ходами Джона обязан пройти узел без вмешательства."""
        sim = make_sim(guest_probability=1.0)
        sim.apply_choice('A', john_slows=True)  # ход в 1.1
        self.assertEqual(sim.state.john_moves_left, 2)
        self.assertFalse(sim.can_john_move())  # 1.2d — соседний узел
        self.assertFalse(sim.can_slow())

    def test_move_allowed_after_a_gap(self):
        sim = make_sim(guest_probability=1.0)
        sim.apply_choice('A', john_slows=True)  # ход в 1.1 -> 1.2d
        sim.apply_choice('B')  # 1.2d без хода -> 1.3c
        self.assertTrue(sim.can_john_move())

    def test_slow_forbidden_in_2_1(self):
        """В 2.1 «замедлить» нельзя: карточка снимает метку."""
        sim = walk(make_sim(), 'A', 'B')
        sim.advance()
        self.assertEqual(sim.state.node, '2.1')
        self.assertTrue(sim.node.slow_forbidden)
        self.assertFalse(sim.can_slow())
        with self.assertRaises(RuleError):
            sim.apply_choice('A', john_slows=True)

    def test_slow_targets_dead_end_at_1_1(self):
        """В 1.1 «замедлить» уводит в тупик 1.2d."""
        sim = make_sim()
        self.assertEqual(sim.slow_target('A'), 'D')
        sim.apply_choice('A', john_slows=True)
        self.assertEqual(sim.state.node, '1.2d')

    def test_slow_targets_dead_end_at_3_1(self):
        """В 3.1 «замедлить» вычёркивает концовки: группа уходит в 3.2d."""
        sim = place(make_sim(), '3.1', nodes_before=6, guest=False)
        self.assertEqual(sim.slow_target('A'), 'D')

    def test_slow_takes_neighbour_when_no_dead_end(self):
        """В 1.2a тупика нет — берётся соседний вариант по списку."""
        sim = walk(make_sim(), 'A')
        self.assertEqual(sim.state.node, '1.2a')
        self.assertEqual(sim.slow_target('A'), 'B')
        self.assertEqual(sim.slow_target('C'), 'A')

    def test_slow_unavailable_on_nodes_without_choices(self):
        """1.3x и 2.3x ведут в следующий акт безусловно — замедлять нечего."""
        sim = walk(make_sim(), 'A', 'B')
        self.assertEqual(sim.state.node, '1.3b')
        self.assertFalse(sim.can_slow())

    def test_counter_grants_mark(self):
        """Противодействие в 1.3b даёт ГРАВИТАЦИЮ и сохраняет РАСЧЁТ."""
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A')
        sim.apply_choice('B')  # -> 1.3b
        self.assertTrue(sim.can_counter())
        sim.resolve_guest(counter=True)
        self.assertIn('РАСЧЁТ', sim.state.marks)
        self.assertIn('ГРАВИТАЦИЯ', sim.state.marks)
        self.assertEqual(sim.state.john_moves_left, 2)

    def test_counter_needs_substitute_when_mark_held(self):
        """Если метка «при противодействии» уже есть, нужна замена."""
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A')
        sim.apply_choice('B')  # -> 1.3b, counter даёт ГРАВИТАЦИЮ
        sim.state.marks.add('ГРАВИТАЦИЯ')
        self.assertTrue(sim.counter_needs_substitute())
        self.assertEqual(
            set(sim.substitute_options()),
            {SUBSTITUTE_SKIP_GUEST, SUBSTITUTE_RETURN_MOVE},
        )
        with self.assertRaises(RuleError):
            sim.resolve_guest(counter=True)

    def test_returned_move_is_not_spent_but_guest_is_stopped(self):
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A')
        sim.apply_choice('B')  # -> 1.3b
        sim.state.marks.add('ГРАВИТАЦИЯ')
        sim.resolve_guest(counter=True, substitute=SUBSTITUTE_RETURN_MOVE)
        self.assertEqual(sim.state.john_moves_left, 3)
        self.assertIn('РАСЧЁТ', sim.state.marks)  # гость ушёл ни с чем

    def test_skip_next_guest_substitute(self):
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A')
        sim.apply_choice('B')  # -> 1.3b
        sim.state.marks.add('ГРАВИТАЦИЯ')
        sim.resolve_guest(counter=True, substitute=SUBSTITUTE_SKIP_GUEST)
        self.assertEqual(sim.state.john_moves_left, 2)
        self.assertTrue(sim.state.suppress_next_guest)
        sim.advance()  # -> 2.1, лампы нет
        sim.apply_choice('A')  # -> 2.2a, лампа есть
        self.assertFalse(sim.state.guest_present)
        self.assertFalse(sim.state.suppress_next_guest)

    def test_last_lamp_substitute_is_any_mark(self):
        """На последней лампе игры замены пустые — группа берёт любую метку."""
        tree = get_tree()
        sim = make_sim(guest_probability=1.0)
        for node_id in ('3.2a', '3.2b', '3.2c', '3.2d'):
            sim.state.node = node_id
            self.assertTrue(sim.is_last_lamp(), node_id)
        sim.state.node = '3.1'
        self.assertFalse(sim.is_last_lamp())
        self.assertEqual(len(tree.lamp_nodes()), 15)

    def test_any_mark_substitute_grants_chosen_mark(self):
        sim = place(make_sim(guest_probability=1.0), '3.2a', marks={'МЕХАНИЗМ'})
        self.assertEqual(sim.substitute_options(), [SUBSTITUTE_ANY_MARK])
        sim.resolve_guest(
            counter=True, substitute=SUBSTITUTE_ANY_MARK, chosen_mark='ТРИГГЕР'
        )
        self.assertIn('ТРИГГЕР', sim.state.marks)
        self.assertEqual(sim.state.john_moves_left, 2)


class FinalTests(unittest.TestCase):
    def _at_final(self, marks, node_id='3.2c', closed=()):
        sim = make_sim(guest_probability=0.0)
        sim.state.marks = set(marks)
        sim.state.closed_finals = set(closed)
        sim.state.visited = ['x'] * 8
        sim._enter(node_id)
        return sim

    def test_f3_always_open(self):
        sim = self._at_final(set())
        self.assertIn('F3', sim.available_finals())

    def test_f2_requires_trigger(self):
        sim = self._at_final({'МЕХАНИЗМ'})
        self.assertNotIn('F2', sim.available_finals())
        sim = self._at_final({'ТРИГГЕР'})
        self.assertIn('F2', sim.available_finals())

    def test_mark_adds_final_not_listed_on_card(self):
        """3.2d предлагает F3 и F4, но с ТРИГГЕРОМ доступен и F2."""
        sim = self._at_final({'ТРИГГЕР', 'СОМНЕНИЕ'}, node_id='3.2d')
        suggested = {f.key for f in sim.finals_status() if f.suggested}
        self.assertEqual(suggested, {'F3', 'F4'})
        self.assertIn('F2', sim.available_finals())

    def test_mark_also_takes_final_away(self):
        """3.2c предлагает F2, но без ТРИГГЕРА группа его не возьмёт."""
        sim = self._at_final({'ШКАЛА'}, node_id='3.2c')
        suggested = {f.key for f in sim.finals_status() if f.suggested}
        self.assertIn('F2', suggested)
        self.assertNotIn('F2', sim.available_finals())
        with self.assertRaises(RuleError):
            sim.choose_final('F2')

    def test_explicit_closure_beats_mark(self):
        """Унесённый лист с порогом закрывает F2 даже при метке ТРИГГЕР."""
        sim = make_sim(guest_probability=1.0)
        sim.state.visited = ['x'] * 8
        sim.state.marks = {'ТРИГГЕР', 'МЕХАНИЗМ'}
        sim._enter('3.2a')
        sim.resolve_guest()
        self.assertIn('F2', sim.state.closed_finals)
        self.assertNotIn('F2', sim.available_finals())

    def test_floor_rule_grants_doubt(self):
        """Один доступный финал — группа получает СОМНЕНИЕ, открывается F4."""
        sim = self._at_final(set(), node_id='3.2c')
        self.assertTrue(sim.state.floor_rule_fired)
        self.assertIn('СОМНЕНИЕ', sim.state.marks)
        self.assertEqual(set(sim.available_finals()), {'F3', 'F4'})

    def test_floor_rule_skips_closed_final(self):
        """Если F4 закрыт указанием карточки, выдаётся ШКАЛА и открывается F1."""
        sim = make_sim(guest_probability=1.0)
        sim.state.visited = ['x'] * 8
        sim.state.marks = set()
        sim._enter('3.2d')  # выдаст СОМНЕНИЕ
        sim.resolve_guest()  # гость уносит его и закрывает F4
        self.assertIn('F4', sim.state.closed_finals)
        self.assertTrue(sim.state.floor_rule_fired)
        self.assertIn('ШКАЛА', sim.state.marks)
        self.assertEqual(set(sim.available_finals()), {'F3', 'F1'})

    def test_floor_rule_does_not_fire_with_two_finals(self):
        sim = self._at_final({'ТРИГГЕР'}, node_id='3.2c')
        self.assertFalse(sim.state.floor_rule_fired)

    def test_group_always_has_two_finals(self):
        """Нижняя граница: выбор из двух есть при любом наборе меток."""
        for node_id in ('3.2a', '3.2b', '3.2c', '3.2d'):
            for guest in (0.0, 1.0):
                sim = make_sim(guest_probability=guest)
                sim.state.visited = ['x'] * 8
                sim._enter(node_id)
                if sim.state.phase is Phase.GUEST:
                    sim.resolve_guest()
                self.assertGreaterEqual(
                    len(sim.available_finals()), 2, f'{node_id}, гость={guest}'
                )


class VariationTests(unittest.TestCase):
    def test_2_1_how_block_reads_one_line(self):
        """Блок «Как вы это узнали» — ровно одна строка."""
        sim = walk(make_sim(), 'A', 'A')  # даёт СОМНЕНИЕ
        sim.advance()  # -> 2.1
        blocks = dict(sim.variations())
        self.assertEqual(len(blocks['Как вы это узнали']), 1)
        self.assertIn('около двух ночи', blocks['Как вы это узнали'][0])

    def test_2_1_how_block_falls_back_to_reviewer(self):
        sim = walk(make_sim(), 'A', 'B')  # без СОМНЕНИЯ
        sim.advance()
        blocks = dict(sim.variations())
        self.assertIn('рецензента', blocks['Как вы это узнали'][0])

    def test_2_1_meaning_block_reads_all_matching(self):
        sim = walk(make_sim(), 'A', 'A')  # ШКАЛА + СОМНЕНИЕ
        sim.advance()
        blocks = dict(sim.variations())
        self.assertTrue(any('аннулировано' in row for row in blocks['Что это значит для вас']))

    def test_3_1_reads_route_through_2_2d(self):
        """3.1 знает, что группа шла через ложный выход 2.2d."""
        sim = make_sim()
        sim.state.visited = ['2.2d']
        sim.state.flags = {'letter_sent'}
        sim._enter('3.1')
        rows = dict(sim.variations())['Что это значит для вас']
        self.assertTrue(any('преждевременной' in row for row in rows))
        self.assertTrue(any('письмо уже ушло' in row for row in rows))

    def test_3_2c_reads_number_left_room(self):
        sim = make_sim()
        sim.state.visited = ['x'] * 8
        sim.state.flags = {'number_left_room'}
        sim._enter('3.2c')
        rows = dict(sim.variations())['Дополнительно']
        self.assertTrue(any('стёрли с доски' in row for row in rows))

    def test_3_2c_silent_without_flag(self):
        sim = make_sim()
        sim.state.visited = ['x'] * 8
        sim._enter('3.2c')
        self.assertNotIn('Дополнительно', dict(sim.variations()))


class StateTests(unittest.TestCase):
    def test_state_survives_round_trip(self):
        """Состояние сериализуемо — на этом держится веб без базы данных."""
        sim = make_sim(guest_probability=1.0)
        walk(sim, 'A', 'B')
        raw = sim.state.to_dict()
        restored = GroupState.from_dict(raw)
        self.assertEqual(restored.to_dict(), raw)
        resumed = Simulation(state=restored, guest_probability=1.0)
        self.assertEqual(resumed.state.node, sim.state.node)
        self.assertEqual(resumed.state.marks, sim.state.marks)


class BatchTests(unittest.TestCase):
    def test_every_run_reaches_a_final(self):
        tree = get_tree()
        rng = random.Random(7)
        strategy = get_strategy('random', rng)
        for _ in range(200):
            result = run_once(tree, strategy, rng, guest_probability=1.0)
            self.assertIsNotNone(result.final)
            self.assertEqual(len(result.path), 8)
            self.assertGreaterEqual(len(result.available_finals), 2)

    def test_random_strategy_reaches_every_card(self):
        report, tree = run_batch(iterations=3000, strategy_name='random', seed=1)
        self.assertEqual(report.never_visited(tree), [])
        self.assertEqual(report.unreached_finals(tree), [])
        self.assertEqual(report.unreached_marks(tree), [])

    def test_no_run_ends_with_single_final(self):
        report, _ = run_batch(iterations=3000, strategy_name='random', seed=2)
        self.assertEqual(report.available_count[0], 0)
        self.assertEqual(report.available_count[1], 0)

    def test_lazy_path_collects_doubt(self):
        """Ленивый путь набирает СОМНЕНИЕ — это срезанный угол, а не достижение."""
        report, _ = run_batch(iterations=100, strategy_name='lazy', seed=3)
        self.assertEqual(report.share(report.marks_at_final['СОМНЕНИЕ']), 1.0)

    def test_john_can_spend_all_three_moves(self):
        report, _ = run_batch(iterations=2000, strategy_name='counter', seed=4)
        self.assertGreater(report.john_moves_spent[3], 0)

    def test_act_one_allows_two_moves_others_one(self):
        """Из ограничений следует расписание 2 / 1 / 1 хода по актам."""
        tree = get_tree()
        rng = random.Random(11)
        strategy = get_strategy('counter', rng)
        for _ in range(300):
            result = run_once(tree, strategy, rng, guest_probability=1.0)
            self.assertLessEqual(result.john_moves_spent, 3)


if __name__ == '__main__':
    unittest.main()
