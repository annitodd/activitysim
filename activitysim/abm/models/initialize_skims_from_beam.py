import os
import openmatrix as omx
import pandas as pd
import numpy as np
import logging

from activitysim.core import config
from activitysim.core import inject


logger = logging.getLogger(__name__)

# ActivitySim Skims Variables
periods = ['EA', 'AM', 'MD', 'PM', 'EV']
transit_paths = ['DRV_COM_WLK', 'DRV_HVY_WLK', 'DRV_LOC_WLK', 'DRV_LRF_WLK','DRV_EXP_WLK',
                 'WLK_COM_DRV', 'WLK_HVY_DRV', 'WLK_LOC_DRV', 'WLK_LRF_DRV','WLK_EXP_DRV',
                 'WLK_COM_WLK', 'WLK_HVY_WLK', 'WLK_LOC_WLK', 'WLK_LRF_WLK','WLK_EXP_WLK', 
                 'WLK_TRN_WLK']
hwy_paths = ['SOV', 'SOVTOLL', 'HOV2', 'HOV2TOLL', 'HOV3', 'HOV3TOLL']

# Map ActivitySim skim measures to input skims
beam_asim_hwy_measure_map = {
    'TIME': 'TIME_minutes',  # must be minutes
    'DIST': 'DIST_miles',  # must be miles
    'BTOLL': None,
    'VTOLL': 'VTOLL_FAR'}

beam_asim_transit_measure_map = {
    'WAIT': None,  # other wait time?
    'TOTIVT': 'TOTIVT_IVT_minutes',  # total in-vehicle time (minutes)
    'KEYIVT': 'KEYIVT_minutes',  # light rail IVT
    'FERRYIVT': 'FERRYIVT_minutes',  # ferry IVT
    'FAR': 'VTOLL_FAR',  # fare
    'DTIM': 'DTIM_minutes',  # drive time
    'DDIST': 'DDIST_miles',  # drive dist (nees to be in Miles)
    'WAUX': 'WAUX_minutes',  # walk other time
    'WEGR': 'WEGR_minutes',  # walk egress timed
    'WACC': 'WACC_minutes',  # walk access time
    'IWAIT': None,  # iwait?
    'XWAIT': None,  # transfer wait time
    'BOARDS': 'BOARDS',  # transfers
    'IVT': 'TOTIVT_IVT_minutes' # In vehicle travel time (minutes)
}

beam_skims_types = {'timePeriod': str, 
                    'pathType': str, 
                    'origin': int,
                    'destination': int,
                    'TIME_minutes': float,
                    'TOTIVT_IVT_minutes': float,
                    'VTOLL_FAR': float,
                    'DIST_meters': float,
                    'WACC_minutes': float,
                    'WAUX_minutes': float,
                    'WEGR_minutes': float,
                    'DTIM_minutes': float,
                    'DDIST_meters': float,
                    'KEYIVT_minutes': float,
                    'FERRYIVT_minutes': float,
                    'BOARDS': float
                    }


@inject.table()
def raw_beam_skims(settings):

    if inject.get_injectable('beam_skims_url', False):
        beam_skims_url = inject.get_injectable('beam_skims_url')
    else:
        try:
            logger.info(
                "No remote path to BEAM skims specified at runtime. "
                "Trying default URL.")
            beam_skims_url = settings['beam_skims_url']
        except KeyError:
            raise KeyError(
                "Couldn't find skims at the default URL either. See "
                "simulation.py --help or configs/settings.yaml "
                "for more ideas.")

    # load skims from url
    skims = pd.read_csv(beam_skims_url, dtype = beam_skims_types)

    return skims


# for use in initialize_inputs_from_usim
@inject.injectable(cache=True)
def h3_zone_ids(raw_beam_skims):
    return raw_beam_skims.origTaz.unique()

#### CHANGES ##### 
# @inject.injectable(cache = True)
def create_skim_object(data_dir):
    skims_path = os.path.join(data_dir, 'skims.omx')
    skims_exist = os.path.exists(skims_path)

    if skims_exist:
        logger.info("Found existing skims, no need to re-create.")
        return False

    else:
        logger.info("Creating skims.omx from BEAM skims")
        skims = omx.open_file(skims_path, 'w')
        skims.close()
        return True

# @inject.injectable(cache = True)
def create_skims_by_mode():
    '''Returns 2 OD pandas dataframe for auto and transit '''
    raw_beam_skims = inject.get_table('raw_beam_skims')
    skims_df = raw_beam_skims.to_frame()
    
    num_hours = skims_df['timePeriod'].nunique()
    num_modes = skims_df['pathType'].nunique()
    num_od_pairs = len(skims_df) / num_hours / num_modes

    # make sure the matrix is square
    num_taz = np.sqrt(num_od_pairs)
    assert num_taz.is_integer()
    num_taz = int(num_taz)
    
    # convert beam skims to activitysim units (miles and minutes)
    skims_df['DIST_miles'] = skims_df['DIST_meters'] * (0.621371 / 1000)
    skims_df['DDIST_miles'] = skims_df['DDIST_meters'] * (0.621371 / 1000)
    
    skims_df = skims_df.sort_values(['origin', 'destination','TIME_minutes'])
    auto_df = skims_df[(skims_df['pathType'] == 'SOV')]
    transit_df = skims_df[(skims_df['pathType'].isin(transit_paths))]
    return auto_df, transit_df, num_taz

# @inject.injectable(cache = True)
def distance_skims(auto_df, data_dir, num_taz):   
    #Open skims object
    skims_path = os.path.join(data_dir, 'skims.omx')
    skims = omx.open_file(skims_path, 'a')
    
    #TO DO: Include walk and bike distances, for now walk and bike are the same as drive. 
    distances_auto = auto_df.drop_duplicates(['origin', 'destination'], keep = 'last')[beam_asim_hwy_measure_map['DIST']]
    distances_auto = distances_auto.replace(0, np.random.normal(39,20)) #TO DO: Do something better. 
    # distances_walk = walk_df.drop_duplicates(['origin', 'destination'])[beam_asim_hwy_measure_map['DIST']]

    mx_auto = distances_auto.values.reshape((num_taz, num_taz))
    # mx_walk = distances_walk.values.reshape((num_taz, num_taz))
    
    #Distance matrices 
    skims['DIST'] = mx_auto
    skims['DISTBIKE'] = mx_auto 
    skims['DISTWALK'] = mx_auto 
    skims.close()

# @inject.injectable(cache = True)
def transit_acces(transit_df, access_paths, num_taz):
    ''' OD pair value for drive access '''
    df = transit_df[transit_df.pathType.isin(access_paths)]
    df.drop_duplicates(['origin','destination'], keep = 'last', inplace = True)
    assert df.shape[0] == num_taz * num_taz
    return df

# @inject.injectable(cache = True)
def transit_skims(transit_df, data_dir, num_taz):
    """ Generate transit OMX skims"""
    #Open skims object
    skims_path = os.path.join(data_dir, 'skims.omx')
    skims = omx.open_file(skims_path, 'a')
    
    drive_access = ['DRV_COM_WLK', 'DRV_HVY_WLK', 'DRV_LOC_WLK','DRV_LRF_WLK','DRV_EXP_WLK']
    walk_acces = ['WLK_COM_WLK', 'WLK_HVY_WLK', 'WLK_LOC_WLK','WLK_LRF_WLK','WLK_EXP_WLK', 'WLK_TRN_WLK']

    drive_access_values = transit_acces(transit_df, drive_access, num_taz)
    walk_access_values =  transit_acces(transit_df, walk_acces, num_taz)

    for path in transit_paths:
        path_ = path.replace('EXP', "LOC") #Get the values of LOC for EXP. 
        path_ = path_.replace('TRN', "LOC") #Get the values of LOC for TRN.
    #     mask1 = transit_df['pathType'] == path_ #When BEAM skims generates all skims
    #     df = transit_df[mask1] #When BEAM skims generates all skims
        ### TO DO: Drive access needs to be different for each transit mode 
        ### TO DO: Walk access needs to be different for each transit mode
        if path[:4] == 'DRIVE':
            df = drive_access_values
        else:
            df = walk_access_values
        for period in periods:
    #         mask2 = df_['timePeriod'] == period
    #         df_ = df[mask2]
            df_ = df
            for measure in beam_asim_transit_measure_map.keys():
                name = '{0}_{1}__{2}'.format(path, measure, period)
                if beam_asim_transit_measure_map[measure]:
                    vals = df_[beam_asim_transit_measure_map[measure]]
                    mx = vals.values.reshape((num_taz, num_taz), order = 'C')
                else:
                    mx = np.zeros((num_taz, num_taz))
                skims[name] = mx
    skims.close()
    

# @inject.injectable(cache = True, data_dir)
def auto_skims(auto_df, data_dir,num_taz):
    #Open skims object
    skims_path = os.path.join(data_dir, 'skims.omx')
    skims = omx.open_file(skims_path, 'a')
    
    #Create skims
    for period in periods:
        mask1 = auto_df['timePeriod'] == period
        df = auto_df[mask1]
        for path in hwy_paths:
            for measure in beam_asim_hwy_measure_map.keys():
                name = '{0}_{1}__{2}'.format(path, measure, period)
                if beam_asim_hwy_measure_map[measure]:
                    vals = df[beam_asim_hwy_measure_map[measure]]
                    mx = vals.values.reshape((num_taz, num_taz), order = 'C')
                else:
                    mx = np.zeros((num_taz, num_taz))
                skims[name] = mx
    skims.close()

def create_offset(auto_df, data_dir):
    #Open skims object
    skims_path = os.path.join(data_dir, 'skims.omx')
    skims = omx.open_file(skims_path, 'a')
    
    #Generint offset
    taz_equivs = auto_df.origin.sort_values().unique()
    skims.create_mapping('taz', taz_equivs)
    skims.close()
    
@inject.step()
def create_skims_from_beam(data_dir):
    
    new = create_skim_object(data_dir)
    if new:
        auto_df, transit_df , num_taz = create_skims_by_mode()

        #Create skims
        distance_skims(auto_df, data_dir, num_taz)
        auto_skims(auto_df, data_dir, num_taz)
        transit_skims(transit_df, data_dir, num_taz)

        #Create offset
        create_offset(auto_df, data_dir)

#### Changes ############

# @inject.step()
# def create_skims_from_beam(data_dir):
#     skims_path = os.path.join(data_dir, 'skims.omx')
#     skims_exist = os.path.exists(skims_path)

#     if skims_exist:
#         logger.info("Found existing skims, no need to re-create.")

#     else:
#         logger.info("Creating skims.omx from BEAM skims")
#         raw_beam_skims = inject.get_table('raw_beam_skims')
# #         h3_zone_ids = inject.get_injectable('h3_zone_ids')# What is it is not a h3? 
#         skims_df = raw_beam_skims.to_frame()

#         # figure out the size of the skim matrices
#         num_hours = skims_df['timePeriod'].nunique()
#         num_modes = skims_df['pathType'].nunique()
#         num_od_pairs = len(skims_df) / num_hours / num_modes

#         # make sure the matrix is square
#         num_taz = np.sqrt(num_od_pairs)
#         assert num_taz.is_integer()

#         num_taz = int(num_taz)

#         # convert beam skims to activitysim units (miles and minutes)
#         skims_df['DIST_miles'] = skims_df['DIST_meters'] * (0.621371 / 1000)
#         skims_df['DDIST_miles'] = skims_df['DDIST_meters'] * (0.621371 / 1000)

#         skims = omx.open_file(skims_path, 'w')
        
#         #Organize skims
#         skims_df = skims_df.sort_values(['origin', 'destination'])
        
#         auto_df = skims_df[(skims_df['pathType'] == 'SOV')]
#         transit_df = skims_df[(skims_df['pathType'].isin(transit_paths))]

# #         # make sure the order of the rows in the skims table matches the
# #         # the order in which the zone IDs are being stored
# #         assert np.array_equal(
# #             auto_df['origTaz'].values.reshape((num_taz, num_taz))[:, 0],
# #             h3_zone_ids)
# #         assert np.array_equal(
# #             active_df['origTaz'].values.reshape((num_taz, num_taz))[:, 0],
# #             h3_zone_ids)
# #         assert np.array_equal(
# #             transit_df['origTaz'].values.reshape((num_taz, num_taz))[:, 0],
# #             h3_zone_ids)

#         # activitysim estimated its models using transit skims from Cube
#         # which store time values as scaled integers (e.g. x100), so their
#         # models also divide transit skim values by 100. Since our skims
#         # aren't coming out of Cube, we multiply by 100 to negate the division.
# #         transit_df['generalizedTimeInM'] = transit_df['generalizedTimeInM'] * 100
        
#         time_vars = ['TIME_minutes','TOTIVT_IVT_minutes', 'WACC_minutes',
#                      'WAUX_minutes', 'WEGR_minutes', 'DTIM_minutes', 
#                      'KEYIVT_minutes', 'FERRYIVT_minutes']
        
#         transit_df[time_vars] = transit_df[time_vars] * 100

#         # Adding car distance skims
        
#         distances = auto_df.drop_duplicates(['origin', 'destination'])[beam_asim_hwy_measure_map['DIST']]
#         mx = distances.values.reshape((num_taz, num_taz))
        
#         skims['DIST'] = mx
        
#         # TO DO: get separate walk skims from beam so we don't
#         # just have to use bike distances for walk distances
#         skims['DISTBIKE'] = mx # TO DO: Get bike distance
#         skims['DISTWALK'] = mx # TO DO: Get walk distance

# #         # active skims
# #         for mode in active_modes:

# #             # TO DO: get separate walk skims from beam so we don't
# #             # just have to use bike distances for walk distances
# #             name = 'DIST{0}'.format(mode)

# #             vals = active_df[beam_asim_hwy_measure_map['DIST']].values
# #             mx = vals.reshape((num_taz, num_taz))
# #             skims[name] = mx

#         for period in periods:
#             ###############
#             #Transit paths
#             ###############
#             mask1 = transit_df['timePeriod'] == period
#             df = transit_df[mask1]
#             for path in transit_paths:
#                 path_ = path.replace('EXP', "LOC") #Get the values of LOC for EXP. 
#                 path_ = path_.replace('TRN', "LOC") #Get the values of LOC for TRN.
#                 mask2 = df['pathType'] == path_
#                 df_ = df[mask2]
#                 for measure in beam_asim_transit_measure_map.keys():
#                     name = '{0}_{1}__{2}'.format(path, measure, period)
#                     if beam_asim_transit_measure_map[measure]:
#                         vals = df_[beam_asim_transit_measure_map[measure]]
#                         mx = vals.values.reshape((num_taz, num_taz))
#                     else:
#                         mx = np.zeros((num_taz, num_taz))
#                     skims[name] = mx
             
#             ###############
#             # Auto paths
#             ###############
#             mask1 = auto_df['timePeriod'] == period
#             df = auto_df[mask1]
#             for path in hwy_paths:
#                 for measure in beam_asim_hwy_measure_map.keys():
#                     name = '{0}_{1}__{2}'.format(path, measure, period)
#                     if beam_asim_hwy_measure_map[measure]:
#                         vals = df[beam_asim_hwy_measure_map[measure]]
#                         mx = vals.values.reshape((num_taz, num_taz))
#                     else:
#                         mx = np.zeros((num_taz, num_taz))
#                     skims[name] = mx
        
#         # Create mapping of TAZ to corresponding index in OMX matrix
#         taz_equivs = np.arange(1,num_taz + 1) 
#         skims.create_mapping('taz', taz_equivs)
        
#         skims.close()
