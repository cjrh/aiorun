FROM python:3.7.3-slim-stretch

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    vim \
    less \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/project

WORKDIR /opt/project
RUN python -m pip install \
    -r requirements-test.txt \
    flit \
    pygments
RUN FLIT_ROOT_INSTALL=1 flit install -s

CMD ["/bin/bash"]