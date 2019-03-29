FROM python:3.7-stretch

RUN apt-get update
RUN apt-get install -y vim less

VOLUME /opt/project

#RUN mkdir /opt/project
ADD . /opt/project

WORKDIR /opt/project
RUN python3.7 -m pip install \
    -r requirements-test.txt \
    flit \
    pygments
RUN FLIT_ROOT_INSTALL=1 flit install --pth-file

CMD ["/bin/bash"]