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

    if settings['s3_ouput'] is False:
        return

    # LOAD ASIM OUTPUTS
    output_tables_settings = settings['output_tables']
    prefix = output_tables_settings['prefix']
    output_tables = output_tables_settings['tables']

    asim_output_dict = {}
    for table_name in output_tables:
        file_name = "%s%s.csv" % (prefix, table_name)
        file_path = config.output_file_path(file_name)
        asim_output_dict[table_name] = pd.read_csv(file_path)

    # LOAD USIM INPUTS
    data_store_path = os.path.join(data_dir, settings['usim_data_store'])

    if not os.path.exists(data_store_path):
        logger.info("Loading input .h5 from s3!")
        remote_s3_path = os.path.join(
            settings['bucket_name'], "input", settings['sim_year'])
        s3 = s3fs.S3FileSystem()
        with open(data_store_path, 'w') as f:
            s3.get(remote_s3_path, f.name)

    store = pd.HDFStore(data_store_path)
    households_cols = store['households'].reset_index().columns
    persons_cols = store['persons'].reset_index().columns

    # UPDATE USIM PERSONS
    # new columns to persist: workplace_taz, school_taz
    p_names_dict = {'PNUM': 'member_id'}
    asim_p_cols_to_include = ['workplace_taz', 'school_taz']
    if 'persons' in asim_output_dict.keys():

        asim_output_dict['persons'].rename(columns=p_names_dict, inplace=True)
        if not all([col in asim_output_dict['persons'].columns for col in persons_cols]):
            raise KeyError("Not all required columns are in the persons table!")
        asim_output_dict['persons'] = asim_output_dict['persons'][
            list(persons_cols) + asim_p_cols_to_include]

    # UPDATE USIM HOUSEHOLDS
    # no new columns to persist, just convert auto_ownership --> cars
    hh_names_dict = {
        'HHID': 'household_id',
        'hhsize': 'persons',
        'num_workers': 'workers',
        'auto_ownership': 'cars',
        'PNUM': 'member_id'}
    if 'households' in asim_output_dict.keys():
        asim_output_dict['households'].rename(
            columns=hh_names_dict, inplace=True)
        if not all([col in asim_output_dict['households'].columns for col in households_cols]):
            raise KeyError("Not all required columns are in the persons table!")
        asim_output_dict['households'] = asim_output_dict[
            'households'][households_cols]

    # WRITE OUT
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

    s3 = s3fs.S3FileSystem()
    remote_s3_path = os.path.join(
        settings['bucket_name'], "output", settings['sim_year'], archive_name)
    logger.info('Sending combined data to s3!')
    s3.put(outpath, remote_s3_path)

    store.close()

    logger.info(
        'Zipped archive of results for use in UrbanSim or BEAM now available '
        'at {0}'.format("s3://" + remote_s3_path))
