FROM python:3.8.3-slim

RUN apt-get update && apt-get install -y \
    vim \
    less

COPY ./requirements-test.txt /requirements-test.txt
RUN python -m pip install \
    -r requirements-test.txt \
    flit \
    pygments
COPY . /opt/project
WORKDIR /opt/project
RUN FLIT_ROOT_INSTALL=1 flit install --pth-file

CMD ["/bin/bash"]
