import os
import yaml
import numpy as np
import openmatrix as omx
from shutil import copytree, rmtree
from activitysim.core import config


import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')

##############################
### RUN BASE YEAR SCENARIO ###
##############################

# Run activitySim 

#################################
## RUN POLICES WITH SCENARIOS ###
#################################

## General Guidelines
#1. Create directory and copy needed information
#2. Modify whatever input needed 
#3. Run scenarios with input modfies

def copy_directory(source, destination):
    """ Creates a copy of source (directory) in destination (another directory):
    Parameters
    -----------
    - source: directory path
    - destiantion: directory path 
    
    Returns 
    None 
    """
    try:
#         logging.info('Create new model directory in {}'.format(destination))
        copytree(source, destination)
    except FileExistsError:
        print ('File {} already exist - Replacing file'.format(destination))
        rmtree(destination)
        copytree(source, destination)
        

def create_policy_directory(policy_name, replace = False):
    """ Create the directory and subdirectores to run all possible analysis """
    
    scenarios = ['scenario_1','scenario_2','scenario_3']
    base_directory = 'bay_area_base'
    paths = [os.path.join('policy_analysis',policy_name, scenario) for scenario in scenarios]
    
    directory_exist = all([os.path.isdir(path) for path in paths])

    if directory_exist:  
        
        if replace:
            [copy_directory(base_directory, path) for path in paths]
        else:
            logging.info('Directory for policy {} already exist'.format(policy_name))
    else:
        logging.info('Creating directory for {} policy'.format(policy_name))
#         [os.makedirs(path) for path in paths]
        [copy_directory(base_directory, path) for path in paths]

def overrride_settings(settings_path, new_settings_dict):
    ''' Modifies the settings.yaml file in the configs files'''
    
    #Reads settings file
    logging.info('Modifying settings.yaml file')
    a_yaml_file = open(settings_path)
    settings = yaml.load(a_yaml_file, Loader=yaml.FullLoader)
    
    #Modify Settings
    for key, value in new_settings_dict.items():
        settings[key] = value
    
    #Dumps modified settings
    with open(settings_path, 'w') as outfile:
        yaml.dump(settings, outfile, default_flow_style=False)

##########################################
## Public transportation fare reduction ##

def transit_fare_reduction(skims_path, reduction, modes = None):
    ''' 
    Reduces transit fare by reduction 
    Parameters
    -----------
    - skims: skims file or path 
    - reduction: float. Number between 0 and 1
    - modes: str. Can be "bus", 'rail', 'lightrail' ## To add functionality
    '''
    assert 0<= reduction <=1, 'reduction parameter should be between zero and one'
    logging.info('Modifying transit skims for reduction{:.0f}'.format(reduction*100))
    
    skims = omx.open_file(skims_path,'a')
    skims_modified = omx.open_file(skims_path[:-4] + '_transit_fare_reduction.omx','w')

    matrices = skims.list_matrices()
    for matrix in matrices: 
        try:
            if 'FAR' in(matrix):
                skims_modified[matrix] = np.array(skims[matrix])*(1-reduction)
            else:
                skims_modified[matrix] = np.array(skims[matrix])
        except NodeError:
            pass 
    skims.close()
    skims_modified.close()
    
def modify_transit_fare(transit_fare_scenarios):
    for scenario, reduction in transit_fare_scenarios.items():
        
        skim_path = os.path.join('policy_analysis',policy_name, scenario, 'data','skims.omx')
        transit_fare_reduction(skim_path, reduction, modes = None)
        
        settings_path = os.path.join('policy_analysis',policy_name, scenario, 'configs','settings.yaml')
        overrride_settings(settings_path, {'skims_file': 'skims_transit_fare_reduction.omx', 
                                           'sample_size':100, 
                                           'chunck_size':2})
        
    

policy_name = 'transit_reduction'
transit_fare_scenarios = {'scenario_1': 1, 'scenario_2': 0.5, 'scenario_3': 0}
create_policy_directory(policy_name, replace = False)
modify_transit_fare(transit_fare_scenarios)

os.chdir('policy_analysis/transit_reduction/scenario_1')
os.system('python simulation.py')


#########################
### SUMARIZE RESULTS ###
#########################

#Save plot s