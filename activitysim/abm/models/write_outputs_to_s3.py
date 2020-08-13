import s3fs
import logging
import pandas as pd
import zipfile
import os

from activitysim.core import config
from activitysim.core import inject


logger = logging.getLogger(__name__)


@inject.step()
def write_outputs_to_s3(data_dir, settings):

    s3_output = inject.get_injectable('s3_output', settings['s3_output'])
    if s3_output is False:
        return

    logger.info("Writing outputs to s3!")

    # 1. LOAD ASIM OUTPUTS
    output_tables_settings = settings['output_tables']
    h5_output = output_tables_settings['h5_store']
    prefix = output_tables_settings['prefix']
    output_tables = output_tables_settings['tables']

    asim_output_dict = {}

    if h5_output is False:
        for table_name in output_tables:
            file_name = "%s%s.csv" % (prefix, table_name)
            file_path = config.output_file_path(file_name)
            if table_name == 'persons':
                index_col = 'person_id'
            elif table_name == 'households':
                index_col = 'household_id'
            else:
                index_col = None
            asim_output_dict[table_name] = pd.read_csv(
                file_path, index_col=index_col)
    else:
        file_name = '%soutput_tables.h5' % prefix
        file_path = config.output_file_path(file_name)
        store = pd.HDFStore(file_path)
        for table_name in output_tables:
            asim_output_dict[table_name] = store[table_name]

    # 2. LOAD USIM INPUTS
    data_store_path = os.path.join(data_dir, settings['usim_data_store'])

    if not os.path.exists(data_store_path):
        logger.info("Loading input .h5 from s3!")
        remote_s3_path = os.path.join(
            settings['bucket_name'], "input", settings['sim_year'])
        s3 = s3fs.S3FileSystem()
        with open(data_store_path, 'w') as f:
            s3.get(remote_s3_path, f.name)

    store = pd.HDFStore(data_store_path)

    households_cols = store['households'].columns
    persons_cols = store['persons'].columns

    # 3. UPDATE USIM PERSONS
    # new columns to persist: workplace_taz, school_taz
    p_names_dict = {'PNUM': 'member_id'}
    asim_p_cols_to_include = ['workplace_taz', 'school_taz']
    if 'persons' in asim_output_dict.keys():

        asim_output_dict['persons'].rename(columns=p_names_dict, inplace=True)
        if not all([
                col in asim_output_dict['persons'].columns
                for col in persons_cols]):
            raise KeyError(
                "Not all required columns are in the persons table!")

        asim_output_dict['persons'] = asim_output_dict['persons'][
            list(persons_cols) + asim_p_cols_to_include]

    # 4. UPDATE USIM HOUSEHOLDS
    # no new columns to persist, just convert auto_ownership --> cars
    hh_names_dict = {
        'hhsize': 'persons',
        'num_workers': 'workers',
        'auto_ownership': 'cars',
        'PNUM': 'member_id'}

    if 'households' in asim_output_dict.keys():
        asim_output_dict['households'].rename(
            columns=hh_names_dict, inplace=True)

        if not all([
                col in asim_output_dict['households'].columns
                for col in households_cols]):
            raise KeyError(
                "Not all required columns are in the persons table!")

        asim_output_dict['households'] = asim_output_dict[
            'households'][households_cols]

    # 5. WRITE OUT FOR BEAM
    archive_name = 'asim_outputs.zip'
    outpath = config.output_file_path(archive_name)
    logger.info(
        'Merging results back into UrbanSim format and storing as .zip!')
    with zipfile.ZipFile(outpath, 'w') as csv_zip:

        # copy usim static inputs into archive
        for table_name in store.keys():
            if table_name not in [
                    '/persons', '/households', 'persons', 'households']:
                df = store[table_name].reset_index()
                csv_zip.writestr(
                    "{0}.csv".format(table_name), pd.DataFrame(df).to_csv())

        # copy asim outputs into archive
        for table_name in asim_output_dict.keys():
            csv_zip.writestr(
                table_name + ".csv", asim_output_dict[table_name].to_csv())

    s3fs.S3FileSystem.read_timeout = 84600
    fs = s3fs.S3FileSystem(config_kwargs={'read_timeout': 86400})
    bucket = inject.get_injectable('bucket_name', settings['bucket_name'])
    scenario = inject.get_injectable('scenario', settings['scenario'])
    year = inject.get_injectable('year', settings['sim_year'])
    if not isinstance(year, str):
        year = str(year)
    remote_s3_path = os.path.join(
        bucket, "output", scenario, year, archive_name)

    if fs.exists(remote_s3_path):
        logger.info("Archiving old outputs first.")
        ts = fs.info(remote_s3_path)['LastModified'].strftime(
            "%Y_%m_%d_%H%M%S")
        new_fname = archive_name.split('.')[0] + \
            '_' + ts + '.' + archive_name.split('.')[-1]
        new_path_elements = remote_s3_path.split("/")[:4] + [
            'archive', new_fname]
        new_fpath = os.path.join(*new_path_elements)
        if fs.exists(new_fpath):
            fs.rm(remote_s3_path)
        else:
            fs.mv(remote_s3_path, new_fpath)
    logger.info('Sending combined data to s3!')
    fs.put(outpath, remote_s3_path)
    logger.info(
        'Zipped archive of results for use in UrbanSim or BEAM now available '
        'at {0}'.format("s3://" + remote_s3_path))

    # 6. WRITE OUT FOR USIM
    usim_archive_name = 'model_data.h5'
    outpath_usim = config.output_file_path(usim_archive_name)
    usim_bucket = bucket.replace('activitysim', 'urbansim')
    usim_remote_s3_path = os.path.join(
        usim_bucket, 'input', scenario, year, usim_archive_name)
    logger.info(
        'Merging results back into UrbanSim format and storing as .h5!')
    out_store = pd.HDFStore(outpath_usim)
    # copy usim static inputs into archive
    for table_name in store.keys():
        if table_name not in [
                '/persons', '/households', 'persons', 'households']:
            out_store.put(table_name, store[table_name])

    # copy usim outputs into archive
    for table_name in asim_output_dict.keys():
        out_store.put(table_name, asim_output_dict[table_name])

    out_store.close()
    logger.info("Copying outputs to UrbanSim inputs!")
    fs.put(outpath_usim, usim_remote_s3_path)
    logger.info(
        'New UrbanSim model data now available '
        'at {0}'.format("s3://" + usim_remote_s3_path))

    store.close()
