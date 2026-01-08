#!/bin/bash

/ucs/.pyenv/versions/ucs/bin/python3 manage.py makemigrations
/ucs/.pyenv/versions/ucs/bin/python3 manage.py migrate
echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@ucs.ucs', 'qwerty')" | /ucs/.pyenv/versions/ucs/bin/python3 manage.py shell
/ucs/.pyenv/versions/ucs/bin/uwsgi --ini /ucs/uwsgi/ucs.main
