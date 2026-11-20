from django.db import migrations


def drop_pregenerated_tokens(apps, schema_editor):
    """Заранее созданные жетоны (лист мастера) больше не используются: жетоны выдаются в темноте.

    Отдельной миграцией: удаление строк и смена схемы той же таблицы в одной транзакции
    на PostgreSQL упирается в отложенные проверки внешних ключей.
    """
    apps.get_model('ls', 'Token').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ls', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(drop_pregenerated_tokens, migrations.RunPython.noop),
    ]
