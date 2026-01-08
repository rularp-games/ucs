FROM ubuntu:24.04

ENV TZ="Asia/Yekaterinburg"
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN mkdir /ucs

RUN apt update && apt -y upgrade && apt -yq install nginx git supervisor make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm libncurses5-dev\
    libncursesw5-dev xz-utils tk-dev libffi-dev liblzma-dev python3-openssl \
    net-tools vim uwsgi uwsgi-plugin-python3 nodejs npm
RUN git clone 'https://github.com/pyenv/pyenv.git' /ucs/.pyenv
RUN git clone 'https://github.com/pyenv/pyenv-virtualenv.git' /ucs/.pyenv/plugins/pyenv-virtualenv

ENV PYENV_ROOT="/ucs/.pyenv"
ENV PATH="$PYENV_ROOT/bin:$PATH"

RUN /ucs/.pyenv/bin/pyenv install 3.14.0
RUN /ucs/.pyenv/bin/pyenv virtualenv 3.14.0 ucs

ENV LC_ALL="en-US.UTF-8"
ENV LANG="en-US.UTF-8"
ENV LANGUAGE="en-US.UTF-8"

# Copy backend
COPY ucs /ucs

# Copy frontend
COPY front /ucs/front

WORKDIR /ucs

RUN chmod 755 /ucs/init.sh

RUN /ucs/.pyenv/versions/3.14.0/envs/ucs/bin/python -m pip install --upgrade pip

RUN /ucs/.pyenv/versions/ucs/bin/pip3 install -r requirements.txt

RUN /ucs/.pyenv/versions/ucs/bin/python3 manage.py collectstatic --noinput

# Build Vue frontend
WORKDIR /ucs/front
RUN npm install && npm run build

WORKDIR /ucs

RUN ln -s /ucs/ucs.conf /etc/nginx/conf.d/ucs.conf
