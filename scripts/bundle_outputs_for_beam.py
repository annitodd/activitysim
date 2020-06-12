import pandas as pd
import zipfile
import os


local_dir = '../detroit/'

# ASIM OUTPUTS
households_a = pd.read_csv(
    os.path.join(local_dir, 'output/final_households.csv'))
persons_a = pd.read_csv(
    os.path.join(local_dir, 'output/final_persons.csv'))
plans = pd.read_csv(
    os.path.join(local_dir, 'output/final_beam_plans.csv'))
tours = pd.read_csv(
    os.path.join(local_dir, 'output/final_tours.csv'))
trips = pd.read_csv(
    os.path.join(local_dir, 'output/final_trips.csv'))
jtp = pd.read_csv(
    os.path.join(local_dir, 'output/final_joint_tour_participants.csv'))

# USIM DATA
store = pd.HDFStore(os.path.join(local_dir, 'data/model_data_full.h5'))
households_u_cols = store['households'].reset_index().columns
persons_u_cols = store['persons'].reset_index().columns

# PERSONS
# new columns to persist: workplace_taz, school_taz
p_names_dict = {'PNUM': 'member_id'}
asim_p_cols_to_include = ['workplace_taz', 'school_taz']
persons_a.rename(columns=p_names_dict, inplace=True)
persons_final = persons_a[list(persons_u_cols) + asim_p_cols_to_include]

# HOUSEHOLDS
# new columns to persist: auto_ownership/cars
hh_names_dict = {
    'HHID': 'household_id',
    'hhsize': 'persons',
    'num_workers': 'workers',
    'auto_ownership': 'cars',
    'PNUM': 'member_id'}
households_a.rename(columns=hh_names_dict, inplace=True)
households_final = households_a[households_u_cols]

# WRITE OUT
outpath = os.path.join(local_dir, 'output/asim_outputs.zip')
with zipfile.ZipFile(outpath, 'w') as csv_zip:
    for table_name in store.keys():
        if table_name not in ['/persons', '/households']:
            df = store[table_name].reset_index()
            csv_zip.writestr(
                "{0}.csv".format(table_name), pd.DataFrame(df).to_csv())
    csv_zip.writestr("households.csv", pd.DataFrame(households_final).to_csv())
    csv_zip.writestr("persons.csv", pd.DataFrame(persons_final).to_csv())
    csv_zip.writestr("plans.csv", pd.DataFrame(plans).to_csv())
    csv_zip.writestr("tours.csv", pd.DataFrame(tours).to_csv())
    csv_zip.writestr("trips.csv", pd.DataFrame(trips).to_csv())
    csv_zip.writestr("joint_tour_participants.csv", pd.DataFrame(jtp).to_csv())
print(
    'Zipped archive of ActivitySim outputs/BEAM inputs now available '
    'at {0}'.format(outpath))
