"""Тесты веб-интерфейса.

Наследуются от SimpleTestCase намеренно: он запрещает обращения к базе данных,
поэтому тесты доказывают, что симулятор работает без неё.
"""

from __future__ import annotations

import re

from django.test import SimpleTestCase
from django.urls import reverse

from .codec import decode, encode
from .engine import Phase, Simulation
from .loader import get_tree

TOKEN_RE = re.compile(r'name="state" value="([^"]+)"')


def token_of(response) -> str:
    html = response.content.decode('utf-8')
    match = TOKEN_RE.search(html)
    assert match, 'на странице нет скрытого поля состояния'
    return match.group(1)


class IndexTests(SimpleTestCase):
    def test_index_lists_the_tree(self):
        response = self.client.get(reverse('sim:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Теория ложного вакуума')
        self.assertContains(response, 'false_vacuum')


class TableTests(SimpleTestCase):
    def setUp(self):
        self.url = reverse('sim:table')

    def start(self):
        response = self.client.get(self.url, {'guest_probability': '1.0'})
        self.assertEqual(response.status_code, 200)
        return response

    def post(self, response, **data):
        payload = {
            'state': token_of(response),
            'tree': 'false_vacuum',
            'guest_probability': '1.0',
        }
        payload.update(data)
        return self.client.post(self.url, payload)

    def test_first_card_is_the_letter(self):
        response = self.start()
        self.assertContains(response, 'Письмо из Санта-Барбары')
        self.assertContains(response, 'Санта-Барбары')
        state = decode(token_of(response))
        self.assertEqual(state.node, '1.1')
        self.assertEqual(state.phase, Phase.CHOICE)

    def test_choice_moves_to_next_card(self):
        response = self.post(self.start(), action='choice', choice='A')
        self.assertContains(response, 'Баунс')
        self.assertEqual(decode(token_of(response)).node, '1.2a')

    def test_lamp_card_opens_the_guest_scene(self):
        response = self.post(self.start(), action='choice', choice='A')
        response = self.post(response, action='choice', choice='B')
        self.assertContains(response, 'Односторонняя граница')
        self.assertContains(response, 'Лампа зажжена, приходит гость')
        state = decode(token_of(response))
        self.assertEqual(state.node, '1.3b')
        self.assertEqual(state.phase, Phase.GUEST)
        self.assertIn('РАСЧЁТ', state.marks)

    def test_guest_takes_the_mark(self):
        response = self.post(self.start(), action='choice', choice='A')
        response = self.post(response, action='choice', choice='B')
        response = self.post(response, action='guest', counter='0')
        state = decode(token_of(response))
        self.assertNotIn('РАСЧЁТ', state.marks)
        self.assertEqual(state.phase, Phase.CHOICE)

    def test_counter_keeps_the_mark_and_spends_a_move(self):
        response = self.post(self.start(), action='choice', choice='A')
        response = self.post(response, action='choice', choice='B')
        response = self.post(response, action='guest', counter='1')
        state = decode(token_of(response))
        self.assertIn('РАСЧЁТ', state.marks)
        self.assertIn('ГРАВИТАЦИЯ', state.marks)
        self.assertEqual(state.john_moves_left, 2)

    def test_slow_move_sends_group_to_the_dead_end(self):
        response = self.post(
            self.start(), action='choice', choice='A', john_slows='1'
        )
        state = decode(token_of(response))
        self.assertEqual(state.node, '1.2d')
        self.assertEqual(state.john_moves_left, 2)
        self.assertContains(response, 'День святого Валентина')
        self.assertContains(response, 'тупик')

    def test_rule_error_is_shown_and_state_is_kept(self):
        """Ход в соседнем узле недоступен — страница объясняет, а не падает."""
        response = self.post(
            self.start(), action='choice', choice='A', john_slows='1'
        )
        before = decode(token_of(response))
        response = self.post(response, action='choice', choice='B', john_slows='1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'недоступен')
        self.assertEqual(decode(token_of(response)).node, before.node)

    def test_broken_token_is_rejected(self):
        response = self.client.post(
            self.url, {'state': 'не-состояние', 'action': 'advance'}
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'не читается', status_code=400)

    def test_full_walkthrough_reaches_a_final(self):
        """Провести группу от первой карточки до финала через веб-формы."""
        response = self.start()
        guard = 0
        while True:
            guard += 1
            self.assertLess(guard, 40, 'прогон через веб не сходится')
            state = decode(token_of(response))
            if state.phase is Phase.DONE:
                break
            if state.phase is Phase.GUEST:
                response = self.post(response, action='guest', counter='0')
                continue
            if state.phase is Phase.FINAL:
                sim = Simulation(tree=get_tree(), state=state)
                response = self.post(
                    response, action='final', final=sim.available_finals()[0]
                )
                continue
            node = get_tree().node(state.node)
            if node.choices:
                response = self.post(response, action='choice', choice='A')
            else:
                response = self.post(response, action='advance')

        final_state = decode(token_of(response))
        self.assertIsNotNone(final_state.final)
        self.assertEqual(len(final_state.visited), 8)
        self.assertContains(response, 'Пройденный путь')

    def test_restart_clears_the_run(self):
        response = self.post(self.start(), action='choice', choice='A')
        response = self.client.post(reverse('sim:restart'), {'tree': 'false_vacuum'})
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response['Location'])
        self.assertEqual(decode(token_of(response)).node, '1.1')


class CodecTests(SimpleTestCase):
    def test_state_survives_encoding(self):
        sim = Simulation(tree=get_tree())
        sim.apply_choice('A')
        restored = decode(encode(sim.state))
        self.assertEqual(restored.to_dict(), sim.state.to_dict())

    def test_token_stays_short_enough_for_a_form_field(self):
        """Состояние с полным журналом должно влезать в скрытое поле."""
        sim = Simulation(tree=get_tree(), guest_probability=1.0)
        for _ in range(3):
            if sim.state.phase is Phase.GUEST:
                sim.resolve_guest()
            if sim.state.phase is Phase.CHOICE and sim.node.choices:
                sim.apply_choice(sim.active_choices()[0].key)
        self.assertLess(len(encode(sim.state)), 4000)


class ReportTests(SimpleTestCase):
    def test_report_renders(self):
        response = self.client.get(
            reverse('sim:report'), {'iterations': '300', 'seed': '1'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Метки к моменту финала')
        self.assertContains(response, 'Частые пути')
        self.assertContains(response, 'F3')

    def test_report_clamps_iterations(self):
        response = self.client.get(
            reverse('sim:report'), {'iterations': '999999', 'seed': '1'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['iterations'], 50000)

    def test_report_falls_back_to_known_strategy(self):
        response = self.client.get(
            reverse('sim:report'),
            {'strategy': 'нет-такой', 'iterations': '100', 'seed': '1'},
        )
        self.assertEqual(response.context['strategy'], 'random')

    def test_random_strategy_report_has_no_warnings(self):
        response = self.client.get(
            reverse('sim:report'), {'iterations': '3000', 'seed': '1'}
        )
        self.assertEqual(response.context['report'].warnings, [])
