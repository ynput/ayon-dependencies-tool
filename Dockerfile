# Build AYON docker image
FROM ubuntu:focal AS builder
ARG PYTHON_VERSION=3.9.13
ARG BUILD_DATE
ARG VERSION

LABEL description="Docker Image to create dependency package for Ubuntu installer"
LABEL org.opencontainers.image.name="ynput/ayon-dependencies-ubuntu"
LABEL org.opencontainers.image.title="AYON Dependency Package Docker Image"
LABEL org.opencontainers.image.url="https://ayon.ynput.io/"
LABEL org.opencontainers.image.source="https://github.com/ynput/ayon-dependencies-tool"
LABEL org.opencontainers.image.documentation="https://ayon.ynput.io"
LABEL org.opencontainers.image.created=$BUILD_DATE
LABEL org.opencontainers.image.version=$VERSION

USER root

ARG DEBIAN_FRONTEND=noninteractive

# update base
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        bash \
        git \
        cmake \
        make \
        curl \
        wget \
        build-essential \
        checkinstall \
        libssl-dev \
        zlib1g-dev \
        libbz2-dev \
        libreadline-dev \
        libsqlite3-dev \
        llvm \
        libncursesw5-dev \
        xz-utils \
        tk-dev \
        libxml2-dev \
        libxmlsec1-dev \
        libffi-dev \
        liblzma-dev \
        patchelf

SHELL ["/bin/bash", "-c"]


RUN mkdir /opt/ayon-dependencies-tool

# download and install pyenv
RUN curl https://pyenv.run | bash \
    && echo 'export PATH="$HOME/.pyenv/bin:$PATH"'>> $HOME/init_pyenv.sh \
    && echo 'eval "$(pyenv init -)"' >> $HOME/init_pyenv.sh \
    && echo 'eval "$(pyenv virtualenv-init -)"' >> $HOME/init_pyenv.sh \
    && echo 'eval "$(pyenv init --path)"' >> $HOME/init_pyenv.sh

ENV PYENV_ROOT="/root/.pyenv"
ENV PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"

# install python with pyenv
RUN source $HOME/init_pyenv.sh \
    && pyenv install ${PYTHON_VERSION} \
    && pyenv global ${PYTHON_VERSION} \
    && pyenv rehash

RUN source /root/.bashrc && python --version

COPY . /opt/ayon-dependencies-tool/

RUN chmod +x /opt/ayon-dependencies-tool/start.sh

WORKDIR /opt/ayon-dependencies-tool

# set local python version
RUN source $HOME/init_pyenv.sh \
    && pyenv local ${PYTHON_VERSION}

# build launcher and installer
RUN source $HOME/.bashrc \
    && ./start.sh install

CMD [/opt/ayon-dependencies-tool/start.sh, listen]