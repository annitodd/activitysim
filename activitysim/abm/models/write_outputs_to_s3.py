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

    updated_tables = ['households', 'persons']

    # run vars
    bucket = inject.get_injectable('bucket_name', settings['bucket_name'])
    scenario = inject.get_injectable('scenario', settings['scenario'])
    year = inject.get_injectable('year', settings['sim_year'])
    if not isinstance(year, str):
        year = str(year)

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
        store.close()

    # 2. LOAD USIM INPUTS
    data_store_path = os.path.join(data_dir, settings['usim_data_store'])

    if not os.path.exists(data_store_path):
        logger.info("Loading input .h5 from s3!")
        remote_s3_path = os.path.join(
            settings['bucket_name'], "input", scenario, year,
            settings['usim_data_store'])
        s3 = s3fs.S3FileSystem()
        with open(data_store_path, 'w') as f:
            s3.get(remote_s3_path, f.name)

    input_store = pd.HDFStore(data_store_path)

    required_cols = {}
    for table_name in updated_tables:
        required_cols[table_name] = list(input_store[table_name].columns)

    # 3. PREPARE NEW PERSONS TABLE
    # new columns to persist: workplace_taz, school_taz
    p_names_dict = {'PNUM': 'member_id'}
    p_cols_to_include = required_cols['persons']
    if 'persons' in asim_output_dict.keys():

        asim_output_dict['persons'].rename(columns=p_names_dict, inplace=True)

        # only preserve original usim columns and two new columns
        for col in ['workplace_taz', 'school_taz']:
            if col not in asim_output_dict['persons'].columns:
                p_cols_to_include.append(col)
        asim_output_dict['persons'] = asim_output_dict['persons'][
            p_cols_to_include]

    # 4. PREPARE NEW HOUSEHOLDS TABLE
    # no new columns to persist, just convert column names
    hh_names_dict = {
        'hhsize': 'persons',
        'num_workers': 'workers',
        'auto_ownership': 'cars',
        'PNUM': 'member_id'}

    if 'households' in asim_output_dict.keys():

        asim_output_dict['households'].rename(
            columns=hh_names_dict, inplace=True)

        # only preserve original usim columns
        asim_output_dict['households'] = asim_output_dict[
            'households'][required_cols['households']]

    # 5. ENSURE MATCHING SCHEMAS FOR UPDATED TABLES
    for table_name in updated_tables:

        # make sure all required columns are present
        if not all([
                col in asim_output_dict[table_name].columns
                for col in required_cols[table_name]]):
            raise KeyError(
                "Not all required columns are in the {0} table!".format(
                    table_name))

        # make sure data types match
        else:
            dtypes = input_store[table_name].dtypes.to_dict()
            for col in required_cols[table_name]:
                if asim_output_dict[table_name][col].dtype != dtypes[col]:
                    asim_output_dict[table_name][col] = asim_output_dict[
                        table_name][col].astype(dtypes[col])

    # specific dtype required conversions
    asim_output_dict['households']['block_id'] = asim_output_dict[
        'households']['block_id'].astype(str)

    # 5. WRITE OUT FOR BEAM
    archive_name = 'asim_outputs.zip'
    outpath = config.output_file_path(archive_name)
    logger.info(
        'Merging results back into UrbanSim format and storing as .zip!')
    with zipfile.ZipFile(outpath, 'w') as csv_zip:

        # copy usim static inputs into archive
        for table_name in input_store.keys():
            if table_name not in [
                    '/persons', '/households', 'persons', 'households']:
                df = input_store[table_name].reset_index()
                csv_zip.writestr(
                    "{0}.csv".format(table_name), pd.DataFrame(df).to_csv())

        # copy asim outputs into archive
        for table_name in asim_output_dict.keys():
            csv_zip.writestr(
                table_name + ".csv", asim_output_dict[table_name].to_csv())

    s3fs.S3FileSystem.read_timeout = 84600
    fs = s3fs.S3FileSystem(config_kwargs={'read_timeout': 86400})

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
    usim_remote_s3_path = os.path.join(
        bucket, 'output', scenario, year, usim_archive_name)
    logger.info(
        'Merging results back into UrbanSim format and storing as .h5!')
    out_store = pd.HDFStore(outpath_usim)

    # copy usim static inputs into archive
    for table_name in input_store.keys():
        if table_name not in [
                '/persons', '/households', 'persons', 'households']:
            out_store.put(table_name, input_store[table_name], format='t')

    # copy asim outputs into archive
    for table_name in updated_tables:
        out_store.put(table_name, asim_output_dict[table_name], format='t')

    out_store.close()
    logger.info("Copying outputs to UrbanSim inputs!")
    if fs.exists(usim_remote_s3_path):
        logger.info("Archiving old outputs first.")
        ts = fs.info(usim_remote_s3_path)['LastModified'].strftime(
            "%Y_%m_%d_%H%M%S")
        new_fname = archive_name.split('.')[0] + \
            '_' + ts + '.' + usim_archive_name.split('.')[-1]
        new_path_elements = usim_remote_s3_path.split("/")[:4] + [
            'archive', new_fname]
        new_fpath = os.path.join(*new_path_elements)
        if fs.exists(new_fpath):
            fs.rm(usim_remote_s3_path)
        else:
            fs.mv(usim_remote_s3_path, new_fpath)
    fs.put(outpath_usim, usim_remote_s3_path)
    logger.info(
        'New UrbanSim model data now available '
        'at {0}'.format("s3://" + usim_remote_s3_path))

    input_store.close()
