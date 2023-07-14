FROM python:alpine3.17
ENV PYTHONUNBUFFERED=1

# Install python/pip
RUN apk add --update --no-cache python3 git curl build-base python3-dev libffi-dev && ln -sf python3 /usr/bin/python
RUN python3 -m ensurepip
RUN pip3 install --no-cache --upgrade pip setuptools poetry && ln -sf pip3 /usr/bin/pip

RUN mkdir /service
RUN mkdir /service/dependencies
COPY ../.env /service/dependencies/.env
COPY start.ps1 /service/dependencies/start.ps1
COPY pyproject.toml /service/dependencie/pyproject.toml
COPY dependencies.py /service/dependencies/dependencies.py
COPY services/listener.py /service/dependencies/services/listener.py
COPY services/__init__.py /service/dependencies/services/__init__.py
COPY services/__main__.py /service/dependencies/services/__main__.py
COPY tools/ /service/dependencies/tools/
WORKDIR /service/dependencies
RUN cat pyproject.toml


RUN poetry config virtualenvs.create false \
 && poetry install --no-interaction --no-ansi

# Create a group and user: ayonuser
RUN addgroup -S ayonuser && adduser -SH ayonuser -G ayonuser

RUN chown ayonuser:ayonuser -R /service
RUN chmod 777 -R /service

# Tell docker that all future commands should run as the appuser user
USER ayonuser

CMD ["python", "services/listener.py"]