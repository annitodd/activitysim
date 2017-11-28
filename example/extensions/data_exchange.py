import pandas as pd

# system vars
path_to_usim = '/home/mgardner/src/bayarea_urbansim/'
usim_data_dir = 'data/'
usim_output_dir = 'output/'
usim_h5_file = '2015_09_01_bayarea_v3.h5'
path_to_asim = '/home/mgardner/src/activitysim/'
asim_data_dir = 'example/data/'
asim_h5_file = 'mtc_asim.h5'

# load both data stores
usim_store = pd.HDFStore(path_to_usim + usim_data_dir + usim_h5_file)
asim_store = pd.HDFStore(path_to_asim + asim_data_dir + asim_h5_file)

# replace asim households with usim households
usim_households = usim_store['households'].copy()
usim_store.close()
asim_col_names = asim_store['households'].columns
asim_index_name = asim_store['households'].index.name
asim_households = usim_households
asim_households.columns = asim_col_names.tolist() + \
    usim_households.columns.tolist()[len(asim_col_names):]
asim_households.index.name = asim_index_name
asim_store.put('households', asim_households, format='table')

# drop asim persons with no households in the updated households table
asim_persons = asim_store['persons']
persons_mask = asim_persons.household_id.isin(asim_store['households'].index)
asim_persons = asim_persons[persons_mask]
asim_store.put('persons', asim_persons, format='table')

# replace asim land_use/taz_data with usim taz baseyear summaries
usim_taz_filename = 'baseyear_taz_summaries_2010.csv'
usim_taz_summaries = pd.read_csv(
    path_to_usim + usim_output_dir + usim_taz_filename)
asim_taz_summaries = asim_store['land_use/taz_data']
assert len(asim_taz_summaries) == len(usim_taz_summaries)
asim_taz_persist = asim_taz_summaries[[  # these need to get updated somehow
    'HSENROLL', 'COLLFTE', 'COLLPTE', 'TOPOLOGY', 'ZERO']]   # persisting is a stop-gap!
asim_index_name = asim_taz_summaries.index.name
usim_taz_summaries.set_index('zone_id', inplace=True)
usim_taz_summaries.index.name = asim_index_name
usim_taz_summaries.rename(
    columns={'GQPOP': 'gqpop', 'AREA_TYPE': 'area_type'}, inplace=True)
usim_taz_summaries.loc[:, 'hhlds'] = usim_taz_summaries['TOTHH']
usim_taz_summaries.loc[:, 'sftaz'] = usim_taz_summaries.index.values
usim_taz_summaries = pd.merge(
    usim_taz_summaries, asim_taz_persist, left_index=True, right_index=True)
asim_store.put('land_use/taz_data', usim_taz_summaries, format='table')


# close up shop
asim_store.close()
