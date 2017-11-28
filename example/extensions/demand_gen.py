import pandas as pd
df = pd.read_csv('../output/final_trips_table.csv')
df = df[df['trip_mode'].isin(['DRIVEALONEFREE','DRIVE_LRF','SHARED2PAY','SHARED3FREE','SHARED2FREE','DRIVE_COM','DRIVEALONEPAY','DRIVE_LOC','DRIVE_HVY','DRIVE_EXP'])]
df = df[(df['end_trip'] < 21) & (df['start_trip'] > 17)]
demand = df[['trip_id','OTAZ','DTAZ']].groupby(['OTAZ','DTAZ'], as_index=False).agg('count')
demand.rename(columns={'trip_id':'trips'}, inplace=True)
demand.to_csv('../output/pm_peak.csv', index=False)


