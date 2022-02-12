import os
import sys
import yaml
import time 
import numpy as np
import openmatrix as omx
from shutil import copytree, rmtree
from activitysim.core import config


import logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(asctime)s %(name)s - %(levelname)s - %(message)s',
                    datefmt='%m/%d/%Y %I:%M:%S %p')

logger = logging.getLogger(__name__)


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

RESULTS_DIR = 'policy_analysis'

def formatted_print(string, width=50, fill_char='#'):
    print('\n')
    if len(string) + 2 > width:
        width = len(string) + 4
    string = string.upper()
    print(fill_char * width)
    print('{:#^{width}}'.format(' ' + string + ' ', width=width))
    print(fill_char * width, '\n')
    
def run_time(func):
    start = time.time
    func
    end = time.time
    logging.info(('Simulation Finished. Total time: {:. 2f} minutes').format((end - start)/60))

def copy_directory(source, destination, replace = False):
    """ Creates a copy of source (directory) in destination (another directory):
    Parameters
    -----------
    - source: directory path
    - destiantion: directory path 
    
    Returns 
    None 
    """
    try:
        logging.info('Creating directory {} '.format(destination))
        copytree(source, destination)
    except FileExistsError:
        if replace:
            logging.info('Directory {} already exist - Replacing file'.format(destination))
            rmtree(destination)
            copytree(source, destination)
        else:
            logging.info('Directory {} already exist - Skip'.format(destination))
        
def overrride_settings(settings_path, new_settings_dict):
    ''' Modifies the settings.yaml file in the configs files'''
    
    #Reads settings file
    logging.debug('Modifying settings.yaml file')
    a_yaml_file = open(settings_path)
    settings = yaml.load(a_yaml_file, Loader=yaml.FullLoader)
    
    #Modify Settings
    for key, value in new_settings_dict.items():
        settings[key] = value
    
    #Dumps modified settings
    with open(settings_path, 'w') as outfile:
        yaml.dump(settings, outfile, default_flow_style=False)

def read_policy_settings():
    a_yaml_file = open('policies.yaml')
    settings = yaml.load(a_yaml_file, Loader=yaml.FullLoader)
    return settings

def input_transformation(policy_name, scenario, settings):
    if policy_name == 'transit_reduction':
        input_transit_reduction(policy_name, scenario, settings)
    elif policy_name == 'share_moblity':
        logging.info('Policy {} cannot be run at this time').format(reduction*100)

def run_scenario(policy_name, settings):
    """ Runs scenarios for a given policy"""
    
    scenarios = settings['policies'][policy_name]['scenarios']
    replace = settings['policies'][policy_name]['replace']
    for scenario in scenarios.keys():
        start = time.time()
        formatted_print(('Simulation for Policy {} - {}').format(policy_name, scenario))
        logging.info(('Simulation for Policy {} - {}').format(policy_name, scenario))
        path = os.path.join('policy_analysis', policy_name, scenario)
        copy_directory('bay_area_base', path, replace)
        os.chdir(path)
        input_transformation(policy_name, scenario, settings)
        os.system('python simulation.py')
        os.chdir('../../..')
        end = time.time()
        running_time_message = "Simulation Finished. Total time: {:.2f} minutes".format((end - start)/60)
        logging.info(running_time_message)
        
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
    
def input_transit_reduction(policy_name, scenario, settings):
    logging.info('Creating transit skims for {}'.format(scenario))
    
    file_exist = os.path.exists('data/skims_transit_fare_reduction.omx')
    
    if file_exist:
        logging.info('Modified transit skims for {} already exist - Skip'.format(scenario))
    else:
        reduction = settings['policies'][policy_name]['scenarios'][scenario]
        skim_path = os.path.join('data','skims.omx')
        settings_path = os.path.join('configs','settings.yaml')
        
        transit_fare_reduction(skim_path, reduction, modes = None)
        overrride_settings(settings_path, {'skims_file': 'skims_transit_fare_reduction.omx'})
        overrride_settings(settings_path, settings['asim_settings'])
        
settings = read_policy_settings()
policy_name = 'transit_reduction'
run_scenario(policy_name, settings)

#########################
### SUMARIZE RESULTS ###
#########################

#Save plot s