FROM continuumio/miniconda3

ENV CONDA_DIR /opt/conda
ENV CONDA_ENV asim
ENV FULL_CONDA_PATH $CONDA_DIR/envs/$CONDA_ENV


ENV ASIM_PATH /activitysim
ENV ASIM_SUBDIR example
ENV EXEC_NAME simulation.py

RUN apt-get --allow-releaseinfo-change update \
	&& apt-get install -y build-essential zip unzip
RUN conda update conda --yes

RUN git clone -b carb https://github.com/ual/activitysim.git && cd activitysim && git checkout carb

RUN conda env create --quiet -p $FULL_CONDA_PATH --file activitysim/environment.yml
RUN cd activitysim && $FULL_CONDA_PATH/bin/python setup.py install

ENV PATH $FULL_CONDA_PATH/bin:$PATH
ENV CONDA_DEFAULT_ENV $CONDA_ENV

WORKDIR $ASIM_PATH/$ASIM_SUBDIR
ENTRYPOINT ["python", "-u", "simulation.py"]