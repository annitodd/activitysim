# ActivitySim
# See full license in LICENSE.txt.

from __future__ import (absolute_import, division, print_function, )
from future.standard_library import install_aliases
install_aliases()  # noqa: E402

import logging
import argparse

from activitysim.core import inject
from activitysim.core import tracing
from activitysim.core import config
from activitysim.core import pipeline
from activitysim.core import mp_tasks
from activitysim.core import chunk
# from activitysim.cli import run

logger = logging.getLogger('activitysim')


def cleanup_output_files():

    active_log_files = \
        [h.baseFilename for h in logger.root.handlers if isinstance(
            h, logging.FileHandler)]
    tracing.delete_output_files('log', ignore=active_log_files)

    tracing.delete_output_files('h5')
    tracing.delete_output_files('csv')
    tracing.delete_output_files('txt')
    tracing.delete_output_files('yaml')
    tracing.delete_output_files('prof')


def run(run_list, injectables=None):

    # TO DO: move these pre-processing steps to PILATES

    # Create a new skims.omx file from BEAM (http://beam.lbl.gov/) skims
    # if skims do not already exist in the input data directory
    if config.setting('create_skims_from_beam', False):
        pipeline.run(models=['create_skims_from_beam'])
        pipeline.close_pipeline()

    # Create persons, households, and land use .csv files from UrbanSim
    # data if these files do not already exist in the input data directory
    if config.setting('create_inputs_from_usim_data', False):
        pipeline.run(models=['create_inputs_from_usim_data'])
        pipeline.close_pipeline()

    if run_list['multiprocess']:
        logger.info("run multiprocess simulation")
        mp_tasks.run_multiprocess(run_list, injectables)
    else:
        logger.info("run single process simulation")
        pipeline.run(
            models=run_list['models'], resume_after=run_list['resume_after'])
        pipeline.close_pipeline()
        chunk.log_write_hwm()


def log_settings():

    settings = [
        'households_sample_size',
        'chunk_size',
        'multiprocess',
        'num_processes',
        'resume_after',
    ]

    for k in settings:
        logger.info("setting %s: %s" % (k, config.setting(k)))


if __name__ == '__main__':

    parser = argparse.ArgumentParser(add_help=False)

    parser.add_argument(
        "-b", "--bucket_name", action="store", help="s3 bucket name")
    parser.add_argument(
        "-y", "--year", action="store", type=int, help="data year")
    parser.add_argument(
        "-s", "--scenario", action="store", help="scenario")
    parser.add_argument(
        "-u", "--skims_url", action="store", help="url of skims .csv")
    parser.add_argument(
        "-x", "--path_to_remote_data", action="store",
        help="url of urbansim .h5 model data")
    parser.add_argument(
        "-w", "--write_to_s3", action="store_true", help="write output to s3?")
    parser.add_argument(
        "-h", "--household_sample_size", action="store",
        help="household sample size")
    parser.add_argument(
        "-n", "--num_processes", action="store",
        help="# of multiprocess workers to use")
    parser.add_argument(
        "-c", "--chunk_size", action="store",
        help="batch size for processing choosers")

    args = parser.parse_args()

    if args.skims_url:
        config.override_setting('beam_skims_url', args.skims_url)

    if args.bucket_name:
        config.override_setting('bucket_name', args.bucket_name)

    if args.scenario:
        config.override_setting('scenario', args.scenario)

    if args.year:
        config.override_setting('year', args.year)

    if args.path_to_remote_data:
        config.override_setting(
            'remote_data_full_path', args.path_to_remote_data)

    if args.write_to_s3:
        config.override_setting('s3_output', True)

    if args.household_sample_size:
        config.override_setting(
            'households_sample_size', int(args.household_sample_size))

    if args.num_processes:
        config.override_setting('num_processes', int(args.num_processes))

    if args.num_processes:
        config.override_setting('chunk_size', int(args.num_processes))

    injectables = ['data_dir', 'configs_dir', 'output_dir']
    inject.add_injectable('data_dir', 'data')
    inject.add_injectable('configs_dir', ['configs', 'configs/configs'])

#     injectables = config.handle_standard_args()

    config.filter_warnings()
    tracing.config_logger()

    log_settings()

    t0 = tracing.print_elapsed_time()

    # cleanup if not resuming
    if not config.setting('resume_after', False):
        cleanup_output_files()

    run_list = mp_tasks.get_run_list()

    if run_list['multiprocess']:

        # do this after config.handle_standard_args,
        # as command line args may override injectables
        # injectables = list(
        #     set(injectables) | set(
        #     ['data_dir', 'configs_dir', 'output_dir']))
        injectables = {k: inject.get_injectable(k) for k in injectables}
    else:
        injectables = None

    run(run_list, injectables)

    # pipeline will be closed after run if multiprocessing
    # if you want access to tables, BE SURE TO OPEN
    # WITH '_' or all tables will be reinitialized (deleted)
    # pipeline.open_pipeline('_')

    t0 = tracing.print_elapsed_time("everything", t0)
