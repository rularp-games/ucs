"""Пакетный прогон дерева прозрений и сводка по модели.

    python3 manage.py sim_run
    python3 manage.py sim_run --iterations 50000 --strategy counter
    python3 manage.py sim_run --strategy passive --guest-probability 0.5 --log
    python3 manage.py sim_run --validate-only
    python3 manage.py sim_run --compare
"""

from django.core.management.base import BaseCommand, CommandError

from sim.loader import TreeError, available_trees, get_tree
from sim.montecarlo import run_batch
from sim.report import format_report, format_sample_log
from sim.strategies import STRATEGIES


class Command(BaseCommand):
    help = 'Прогоняет дерево прозрений и печатает статистику по модели'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tree',
            default=None,
            help=f'дерево из sim/data (есть: {", ".join(available_trees())})',
        )
        parser.add_argument(
            '--iterations', type=int, default=10000, help='число прогонов'
        )
        parser.add_argument(
            '--strategy',
            default='random',
            choices=sorted(STRATEGIES),
            help='политика решений группы и Джона/Джейн',
        )
        parser.add_argument('--seed', type=int, default=None, help='зерно случайности')
        parser.add_argument(
            '--guest-probability',
            type=float,
            default=1.0,
            help='вероятность прихода гостя на лампу (модель предполагает 1.0)',
        )
        parser.add_argument(
            '--top-paths', type=int, default=8, help='сколько частых путей показать'
        )
        parser.add_argument(
            '--log', action='store_true', help='показать журнал первого прогона'
        )
        parser.add_argument(
            '--validate-only',
            action='store_true',
            help='только проверить дерево на целостность',
        )
        parser.add_argument(
            '--compare',
            action='store_true',
            help='короткая сводка по всем стратегиям сразу',
        )

    def handle(self, *args, **options):
        try:
            tree = get_tree(options['tree']) if options['tree'] else get_tree()
        except TreeError as error:
            raise CommandError(str(error)) from error

        if options['validate_only']:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Дерево «{tree.title}» корректно: '
                    f'{len(tree.nodes)} узлов, {len(tree.finals)} финалов, '
                    f'{len(tree.marks)} меток, {len(tree.lamp_nodes())} ламп.'
                )
            )
            return

        probability = options['guest_probability']
        if not 0.0 <= probability <= 1.0:
            raise CommandError('--guest-probability должна быть от 0 до 1')

        if options['compare']:
            self._compare(options)
            return

        report, tree = run_batch(
            iterations=options['iterations'],
            strategy_name=options['strategy'],
            tree_name=options['tree'],
            seed=options['seed'],
            guest_probability=probability,
        )
        self.stdout.write(format_report(report, tree, top_paths=options['top_paths']))
        if options['log']:
            self.stdout.write('')
            self.stdout.write(format_sample_log(report))
        if report.warnings:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING(f'Расхождений: {len(report.warnings)}')
            )

    def _compare(self, options):
        """Одна строка на стратегию: чем отличаются режимы игры."""
        header = (
            f'{"стратегия":<10} {"меток":>6} {"фин.дост":>9} {"ниж.гран":>9} '
            f'{"ходов":>6}  доля финалов'
        )
        self.stdout.write(header)
        self.stdout.write('-' * len(header))
        for name in sorted(STRATEGIES):
            report, tree = run_batch(
                iterations=options['iterations'],
                strategy_name=name,
                tree_name=options['tree'],
                seed=options['seed'],
                guest_probability=options['guest_probability'],
            )
            marks = sum(
                count * value for count, value in report.mark_count_at_final.items()
            ) / max(report.iterations, 1)
            available = sum(
                count * value for count, value in report.available_count.items()
            ) / max(report.iterations, 1)
            moves = sum(
                count * value for count, value in report.john_moves_spent.items()
            ) / max(report.iterations, 1)
            finals = ' '.join(
                f'{key} {report.share(report.finals_chosen[key]):.0%}'
                for key in sorted(tree.finals)
            )
            self.stdout.write(
                f'{name:<10} {marks:6.2f} {available:9.2f} '
                f'{report.share(report.floor_rule_fired):9.1%} {moves:6.2f}  {finals}'
            )
