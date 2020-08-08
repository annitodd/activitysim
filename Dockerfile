FROM continuumio/miniconda3

ENV CONDA_PATH /opt/conda
ENV CONDA_ENV $CONDA_PATH/envs/asim
ENV ASIM_PATH /activitysim
ENV ASIM_SUBDIR example
ENV EXEC_NAME simulation.py
ENV WRITE_TO_S3 -w

RUN apt-get update \
	&& apt-get install -y build-essential zip unzip
RUN conda update conda --yes

RUN git clone https://github.com/ual/activitysim.git

RUN conda env create --quiet -p $CONDA_ENV --file activitysim/environment.yml
RUN cd activitysim && $CONDA_ENV/bin/python setup.py install

ENTRYPOINT cd $ASIM_PATH/$ASIM_SUBDIR && $CONDA_ENV/bin/python $EXEC_NAME $WRITE_TO_S3