import pandas as pd

path_to_usim_data = '/home/mgardner/src/bayarea_urbansim/data/'
usim_data_file = '2015_09_01_bayarea_v3.h5'
usim_store = pd.HDFStore(path_to_usim_data + usim_data_file)
asim_store = pd.HDFStore('../data/mtc_asim.h5')

# merge households table
usim_households = usim_store['households'].copy()
asim_col_names = asim_store['households'].columns
asim_index_name = asim_store['households'].index.name
asim_households = usim_households
asim_households.columns = asim_col_names.tolist() + \
    usim_store['households'].columns.tolist()[len(asim_col_names):]
asim_households.index.name = asim_index_name
asim_store['households'] = asim_households

# alter asim persons table
asim_persons = asim_store['persons']
persons_mask = asim_persons.household_id.isin(asim_store['households'].index)
asim_persons = asim_persons[persons_mask]
asim_store['persons'] = asim_persons

# close up shop
usim_store.close()
asim_store.close()
