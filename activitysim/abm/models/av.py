import pandas as pd
import numpy as np
import logging

from activitysim.core import tracing
from activitysim.core import config
from activitysim.core import pipeline
from activitysim.core import inject

from activitysim.core.util import reindex

logger = logging.getLogger(__name__)

@inject.step()
def autonomous_vehicles(persons_merged, persons, households,
       skim_dict, skim_stack,
       chunk_size, trace_hh_id, locutor):
    
    """
    Rate-base model for Autonomous vehicles Ownership. 

    Returns:
    ---------
    Simulated Autonomous Vehicle ownership
    """
    trace_label = 'autonomous_vehicle'
    
    #Settings
    model_settings = config.read_model_settings('av.yaml')
    rate = model_settings['av_rate']

    #Choosers
    households = households.to_frame()
    logger.info('Autonomous Vehicles penetration rate:{}'.format(rate))
    
    av = np.random.choice(np.array([0,1]), size = len(households), p = np.array([1 - rate, rate]))
    
    households['av'] = av

    pipeline.replace_table("households", households)
    
#     if trace_hh_id:
#         tracing.trace_df(persons,
#                          label=trace_label,
#                          warn_if_empty=True)