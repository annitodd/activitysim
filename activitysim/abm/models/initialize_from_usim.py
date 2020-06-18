import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import geopandas as gpd
import orca
from shapely.geometry import Polygon, Point
from geopy.distance import vincenty
from h3 import h3
from urbansim.utils import misc
import requests
import openmatrix as omx
from shapely import wkt
import logging
from tqdm import tqdm
import time

from activitysim.core import config
from activitysim.core import inject


logger = logging.getLogger(__name__)


def get_zone_geoms_from_h3(h3_ids):
    polygon_shapes = []
    for zone in h3_ids:
        boundary_points = h3.h3_to_geo_boundary(h3_address=zone, geo_json=True)
        shape = Polygon(boundary_points)
        polygon_shapes.append(shape)

    return polygon_shapes


def get_county_block_geoms(state_fips, county_fips):

    base_url = (
        'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/'
        'Tracts_Blocks/MapServer/12/query?where=STATE%3D{0}+and+COUNTY%3D{1}'
        '&outFields=GEOID%2CSTATE%2CCOUNTY%2CTRACT%2CBLKGRP%2CBLOCK%2CCENTLAT'
        '%2CCENTLON&outSR=%7B"wkid"+%3A+4326%7D&f=pjson')
    url = base_url.format(state_fips, county_fips)
    result = requests.get(url)
    features = result.json()['features']
    if len(features) >= 100000:
        raise RuntimeError("too many blocks in county to query at once!")
    else:
        df = pd.DataFrame()
        for feature in features:
            tmp = pd.DataFrame([feature['attributes']])
            tmp['geometry'] = Polygon(
                feature['geometry']['rings'][0],
                feature['geometry']['rings'][1:])
            df = pd.concat((df, tmp))
        gdf = gpd.GeoDataFrame(df, crs="EPSG:4326")
        return gdf


def get_taz_from_block_geoms(blocks_gdf, zones_gdf, local_crs):

    # df to store GEOID to TAZ results
    block_to_taz_results = pd.DataFrame()

    # ignore empty geoms
    zones_gdf = zones_gdf[~zones_gdf['geometry'].is_empty]

    # convert to meter-based proj
    zones_gdf = zones_gdf.to_crs(local_crs)
    blocks_gdf = blocks_gdf.to_crs(local_crs)

    zones_gdf['zone_area'] = zones_gdf.geometry.area

    # assign blocks to zone with a spatial within query
    within = gpd.sjoin(
        blocks_gdf, zones_gdf.reset_index(), how='inner', op='within')

    # when a block falls within multiple (overlapping) zones,
    # assign it to the zone with the smallest area
    within = within.sort_values(['GEOID', 'zone_area'])
    within = within.drop_duplicates('GEOID', keep='first')

    # add to results df
    block_to_taz_results = pd.concat((
        block_to_taz_results, within[['GEOID', 'TAZ']]))

    # assign remaining blocks based on a spatial intersection
    unassigned_mask = ~blocks_gdf['GEOID'].isin(block_to_taz_results['GEOID'])
    intx = gpd.overlay(
        blocks_gdf[unassigned_mask], zones_gdf.reset_index(),
        how='intersection')

    # assign zone ID's to blocks based on max area of intersection
    intx['intx_area'] = intx['geometry'].area
    intx = intx.sort_values(['GEOID', 'intx_area'], ascending=False)
    intx = intx.drop_duplicates('GEOID', keep='first')

    # add to results df
    block_to_taz_results = pd.concat((block_to_taz_results, intx[['GEOID', 'TAZ']]))

    # assign zone ID's to remaining blocks based on shortest
    # distance between block and zone centroids
    unassigned_mask = ~blocks_gdf['GEOID'].isin(block_to_taz_results['GEOID'])

    if any(unassigned_mask):

        blocks_gdf['geometry'] = blocks_gdf['geometry'].centroid
        zones_gdf['geometry'] = zones_gdf['geometry'].centroid

        all_dists = blocks_gdf.loc[unassigned_mask, 'geometry'].apply(
            lambda x: zones_gdf['geometry'].distance(x))

        nearest = all_dists.idxmin(axis=1).reset_index()
        nearest.columns = ['blocks_idx', 'TAZ']
        nearest.set_index('blocks_idx', inplace=True)
        nearest['GEOID'] = blocks_gdf.reindex(nearest.index)['GEOID']

        block_to_taz_results = pd.concat((block_to_taz_results, nearest[['GEOID', 'TAZ']]))

    return block_to_taz_results.set_index('GEOID')['TAZ']


def get_taz_from_points(df, zones_gdf, local_crs):
    '''
    Assigns the gdf index (TAZ ID) for each index in df
    Input:
    - df columns names x, and y. The index is the ID of the point feature.
    - zones_gdf: GeoPandas GeoDataFrame with TAZ as index, geometry, area.

    Output:
        A series with df index and corresponding gdf id
    '''
    logger.info("Assigning TAZs to {0}".format(df.index.name))
    df = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df.x, df.y), crs="EPSG:4326")
    zones_gdf.geometry.crs = "EPSG:4326"

    # convert to meters-based local crs
    df = df.to_crs(local_crs)
    zones_gdf = zones_gdf.to_crs(local_crs)

    # Spatial join
    intx = gpd.sjoin(
        df.reset_index(), zones_gdf.reset_index(), how='left', op='intersects')

    # Drop duplicates and keep the one with the smallest H3 area
    intx['intx_area'] = intx['geometry'].area
    intx = intx.sort_values('intx_area')
    intx.drop_duplicates(subset=[df.index.name], keep='first', inplace=True)
    intx.set_index(df.index.name, inplace=True)
    df['TAZ'] = intx['TAZ'].reindex(df.index)

    # Check if there is any unassigined object
    unassigned_mask = pd.isnull(df['TAZ'])
    if any(unassigned_mask):

        zones_gdf['geometry'] = zones_gdf['geometry'].centroid
        all_dists = df.loc[unassigned_mask, 'geometry'].apply(
            lambda x: zones_gdf['geometry'].distance(x))

        df.loc[unassigned_mask, 'TAZ'] = all_dists.idxmin(
            axis=1).values

    return df['TAZ']


# ** define injectables we'll use over and over **

@orca.injectable()
def county_codes(blocks):
    county_codes = blocks.index.str.slice(2, 5).unique().values
    return county_codes


@orca.injectable()
def state_fips():
    return config.setting('state_fips')


@orca.injectable()
def data_dir():
    return inject.get_injectable('data_dir')


@orca.injectable()
def local_crs():
    return config.setting('local_crs')


# ** 1. CREATE NEW TABLES **
@orca.table(cache=True)
def block_geoms(data_dir, state_fips, county_codes):

    all_block_geoms = []

    if os.path.exists(os.path.join(data_dir, "blocks.shp")):

        logger.info("Loading block geoms from disk!")
        blocks_gdf = gpd.read_file(os.path.join(data_dir, "blocks.shp"))

    else:

        logger.info("Downloading block geoms from Census TIGERweb API!")

        # get block geoms from census tigerweb API
        for county in tqdm(
                county_codes, total=len(county_codes),
                desc='Getting block geoms for {0} counties'.format(
                    len(county_codes))):
            county_gdf = get_county_block_geoms(state_fips, county)
            all_block_geoms.append(county_gdf)

        blocks_gdf = gpd.GeoDataFrame(
            pd.concat(all_block_geoms, ignore_index=True), crs="EPSG:4326")

        # save to disk
        logger.info("Saving block geoms to disk!")
        blocks_gdf.to_file(os.path.join(data_dir, "blocks.shp"))

    return blocks_gdf


# Zones
@orca.table('zones', cache=True)
def zones(block_geoms, local_crs):
    """
    if loading zones from shapefile, coordinates must be
    referenced to WGS84 (EPSG:4326) projection.
    """
    usim_zone_geoms = config.setting('usim_zone_geoms')

    if ".shp" in usim_zone_geoms:
        fname = usim_zone_geoms
        filepath = config.data_file_path(fname)
        zones = gpd.read_file(filepath, crs="EPSG:4326")
        zones.reset_index(inplace=True, drop=True)
        zones.index.name = 'TAZ'

    elif usim_zone_geoms == 'h3':

        try:

            h3_zone_ids = inject.get_injectable('h3_zone_ids')
            zone_geoms = get_zone_geoms_from_h3(h3_zone_ids)
            zones = gpd.GeoDataFrame(
                h3_zone_ids, geometry=zone_geoms, crs="EPSG:4326")
            zones.columns = ['h3_id', 'geometry']
            zones['TAZ'] = list(range(1, len(h3_zone_ids) + 1))
            zones = zones.set_index('TAZ')

            # if using h3 zones, must clip geoms to block bounds using
            # local CRS
            block_bounds = block_geoms.to_frame().to_crs(local_crs).unary_union
            zones = zones.to_crs(local_crs)
            zones['geometry'] = zones['geometry'].intersection(block_bounds)
            
            # convert back to epsg:4326 for storage in memory
            zones = zones.to_crs('EPSG:4326')


        except KeyError:
            raise RuntimeError(
                "Trying to create intermediate zones table from h3 IDs "
                "but the 'h3_zone_ids' injectable is not defined")

    else:
        raise RuntimeError(
            "Zone geometries incorrectly specified in settings.yaml")

    return zones


# Schools
@orca.table(cache=True)
def schools(state_fips, county_codes):

    base_url = 'https://educationdata.urban.org/api/v1/' + \
        '{topic}/{source}/{endpoint}/{year}/?{filters}'

    school_tables = []
    for county in county_codes:
        county_fips = str(state_fips) + str(county)
        enroll_filters = 'county_code={0}'.format(county_fips)
        enroll_url = base_url.format(
            topic='schools', source='ccd', endpoint='directory',
            year='2015', filters=enroll_filters)

        enroll_result = requests.get(enroll_url)
        enroll = pd.DataFrame(enroll_result.json()['results'])
        school_tables.append(enroll)
        time.sleep(5)

    enrollment = pd.concat(school_tables, axis=0)
    enrollment = enrollment[[
        'ncessch', 'county_code', 'latitude',
        'longitude', 'enrollment']].set_index('ncessch')
    enrollment.rename(
        columns={'longitude': 'x', 'latitude': 'y'}, inplace=True)
    return enrollment.dropna()


# Colleges
@orca.table(cache=True)
def colleges(state_fips, county_codes):

    base_url = 'https://educationdata.urban.org/api/v1/' + \
        '{topic}/{source}/{endpoint}/{year}/?{filters}'

    colleges_list = []
    for county in county_codes:
        county_fips = str(state_fips) + str(county)
        college_filters = 'county_fips={0}'.format(county_fips)
        college_url = base_url.format(
            topic='college-university', source='ipeds', endpoint='directory',
            year='2015', filters=college_filters)

        college_result = requests.get(college_url)
        college = pd.DataFrame(college_result.json()['results'])
        colleges_list.append(college)
        time.sleep(5)

    colleges = pd.concat(colleges_list)
    colleges = colleges[[
        'unitid', 'inst_name', 'longitude', 'latitude']].set_index('unitid')
    colleges.rename(columns={'longitude': 'x', 'latitude': 'y'}, inplace=True)
    return colleges


# ** 2. ASSIGN TAZ's to GEOMS **
@orca.column('blocks', cache=True)
def TAZ(data_dir, block_geoms, zones, local_crs):

    zones_gdf = zones.to_frame(columns=['geometry'])
    zones_gdf.crs = 'EPSG:4326'

    blocks_gdf = block_geoms.to_frame()
    blocks_gdf.crs = 'EPSG:4326'

    # assign TAZs to blocks
    blocks_to_taz = get_taz_from_block_geoms(blocks_gdf, zones_gdf, local_crs)

    return blocks_to_taz


@orca.column('schools', cache=True)
def TAZ(schools, zones, local_crs):
    zones_gdf = zones.to_frame(columns=['geometry', 'h3_id'])
    schools_df = schools.to_frame(columns=['x', 'y'])
    schools_df.index.name = 'school_id'
    return get_taz_from_points(schools_df, zones_gdf, local_crs)


@orca.column('colleges', cache=True)
def TAZ(colleges, zones, local_crs):
    colleges_df = colleges.to_frame(columns=['x', 'y'])
    colleges_df.index.name = 'college_id'
    zones_gdf = zones.to_frame(columns=['geometry', 'h3_id'])
    return get_taz_from_points(colleges_df, zones_gdf, local_crs)


@orca.column('usim_households')
def TAZ(blocks, usim_households):
    return misc.reindex(blocks.TAZ, usim_households.block_id)


@orca.column('usim_persons')
def TAZ(usim_households, usim_persons):
    return misc.reindex(usim_households.TAZ, usim_persons.household_id)


@orca.column('jobs')
def TAZ(blocks, jobs):
    return misc.reindex(blocks.TAZ, jobs.new_block_id)


# ** 3. CREATE NEW VARIABLES/COLUMNS **

# Jobs Variables

@orca.column('jobs', cache=True)
def new_block_id(jobs, blocks, block_geoms, local_crs):
    """
    Reassign any jobs from blocks with zero land area
    to closest block with land area
    """

    jobs_df = jobs.to_frame(columns=['block_id'])
    blocks_df = blocks.to_frame(columns=['square_meters_land'])
    jobs_df['square_meters_land'] = blocks_df.reindex(
        jobs_df['block_id'])['square_meters_land'].values
    jobs_w_no_land = jobs_df[jobs_df['square_meters_land'] == 0]

    blocks_to_reassign = jobs_w_no_land['block_id'].unique()

    if len(blocks_to_reassign) > 0:
        blocks_gdf = block_geoms.to_frame().set_index('GEOID')
        blocks_gdf['square_meters_land'] = blocks['square_meters_land'].reindex(
            blocks_gdf.index)
        blocks_gdf = blocks_gdf.to_crs(local_crs)

        for block_id in tqdm(
                blocks_to_reassign,
                desc="Redistributing jobs from blocks with no land area:"):

            candidate_mask = (
                blocks_gdf.index.values != block_id) & (
                blocks_gdf['square_meters_land'] > 0)
            new_block_id = blocks_gdf[candidate_mask].distance(
                blocks_gdf.loc[block_id, 'geometry']).idxmin()

            jobs_df.loc[jobs_df['block_id'] == block_id, 'block_id'] = new_block_id

    return jobs_df['block_id']


# Block Variables

@orca.column('blocks')
def TOTEMP(blocks, jobs):
    jobs_df = jobs.to_frame(columns=['new_block_id', 'sector_id'])
    return jobs_df.groupby('new_block_id')['sector_id'].count().reindex(
        blocks.index).fillna(0)


@orca.column('blocks')
def TOTPOP(blocks, usim_households):
    hh = usim_households.to_frame(columns=['block_id', 'persons'])
    return hh.groupby('block_id')['persons'].sum().reindex(
        blocks.index).fillna(0)


@orca.column('blocks')
def TOTACRE(blocks):
    return blocks['square_meters_land'] / 4046.86


@orca.column('blocks')
def area_type_metric(blocks):
    """
    we calculate the metric at the block level because h3 zones are so
    variable in size, so this size-dependent metric is very susceptible
    to the modifiable areal unit problem.
    """

    metric_vals = (
        (1 * blocks['TOTPOP']) + (2.5 * blocks['TOTEMP'])) / blocks['TOTACRE']
    return metric_vals.fillna(0)


# Colleges Variables

@orca.column('colleges')
def full_time_enrollment(state_fips):
    base_url = 'https://educationdata.urban.org/api/v1/{t}/{so}/{e}/{y}/{l}/?{f}&{s}&{r}&{cl}&{ds}&{fips}'
    levels = ['undergraduate', 'graduate']
    enroll_list = []
    for level in levels:
        level_url = base_url.format(
            t='college-university', so='ipeds', e='fall-enrollment',
            y='2015', l=level, f='ftpt=1', s='sex=99',
            r='race=99', cl='class_level=99', ds='degree_seeking=99',
            fips='fips={0}'.format(state_fips))

        enroll_result = requests.get(level_url)
        enroll = pd.DataFrame(enroll_result.json()['results'])
        enroll = enroll[['unitid', 'enrollment_fall']].rename(
            columns={'enrollment_fall': level})
        enroll.set_index('unitid', inplace=True)
        enroll_list.append(enroll)

    full_time = pd.concat(enroll_list, axis=1)
    full_time['full_time'] = full_time['undergraduate'] + full_time['graduate']
    s = full_time.full_time
    return s


@orca.column('colleges')
def part_time_enrollment(state_fips):
    base_url = 'https://educationdata.urban.org/api/v1/{t}/{so}/{e}/{y}/{l}/?{f}&{s}&{r}&{cl}&{ds}&{fips}'
    levels = ['undergraduate', 'graduate']
    enroll_list = []
    for level in levels:
        level_url = base_url.format(
            t='college-university', so='ipeds', e='fall-enrollment',
            y='2015', l=level, f='ftpt=2', s='sex=99',
            r='race=99', cl='class_level=99', ds='degree_seeking=99',
            fips='fips={0}'.format(state_fips))

        enroll_result = requests.get(level_url)
        enroll = pd.DataFrame(enroll_result.json()['results'])
        enroll = enroll[['unitid', 'enrollment_fall']].rename(
            columns={'enrollment_fall': level})
        enroll.set_index('unitid', inplace=True)
        enroll_list.append(enroll)

    part_time = pd.concat(enroll_list, axis=1)
    part_time['part_time'] = part_time['undergraduate'] + part_time['graduate']
    s = part_time.part_time
    return s


# Households Variables

@orca.column('usim_households')
def HHT(usim_households):
    s = usim_households.persons
    return s.where(s == 1, 4)


# Persons Variables

@orca.column('usim_persons')
def ptype(usim_persons):

    # Filters for person type segmentation
    # https://activitysim.github.io/activitysim/abmexample.html#setup
    age_mask_1 = usim_persons.age >= 18
    age_mask_2 = usim_persons.age.between(18, 64, inclusive=True)
    age_mask_3 = usim_persons.age >= 65
    work_mask = usim_persons.worker == 1
    student_mask = usim_persons.student == 1

    # Series for each person segmentation
    type_1 = ((age_mask_1) & (work_mask) & (~student_mask)) * 1  # Full time
    type_4 = ((age_mask_2) & (~work_mask) & (~student_mask)) * 4
    type_5 = ((age_mask_3) & (~work_mask) & (~student_mask)) * 5
    type_3 = ((age_mask_1) & (student_mask)) * 3
    type_6 = (usim_persons.age.between(16, 17, inclusive=True)) * 6
    type_7 = (usim_persons.age.between(6, 16, inclusive=True)) * 7
    type_8 = (usim_persons.age.between(0, 5, inclusive=True)) * 8
    type_list = [
        type_1, type_3, type_4, type_5, type_6, type_7, type_8]

    # Colapsing all series into one series
    for x in type_list:
        type_1.where(type_1 != 0, x, inplace=True)

    return type_1


@orca.column('usim_persons')
def pemploy(usim_persons):
    pemploy_1 = ((usim_persons.worker == 1) & (usim_persons.age >= 16)) * 1
    pemploy_3 = ((usim_persons.worker == 0) & (usim_persons.age >= 16)) * 3
    pemploy_4 = (usim_persons.age < 16) * 4

    # Colapsing all series into one series
    type_list = [pemploy_1, pemploy_3, pemploy_4]
    for x in type_list:
        pemploy_1.where(pemploy_1 != 0, x, inplace=True)

    return pemploy_1


@orca.column('usim_persons')
def pstudent(usim_persons):
    pstudent_1 = (usim_persons.age <= 18) * 1
    pstudent_2 = ((usim_persons.student == 1) & (usim_persons.age > 18)) * 2
    pstudent_3 = (usim_persons.student == 0) * 3

    # Colapsing all series into one series
    type_list = [pstudent_1, pstudent_2, pstudent_3]
    for x in type_list:
        pstudent_1.where(pstudent_1 != 0, x, inplace=True)

    return pstudent_1


# Zones variables

@orca.column('zones', cache=True)
def TOTHH(usim_households, zones):
    s = usim_households.TAZ.groupby(usim_households.TAZ).count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HHPOP(usim_persons, zones):
    s = usim_persons.TAZ.groupby(usim_persons.TAZ).count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def EMPRES(usim_households, zones):
    s = usim_households.to_frame(
        columns=['TAZ', 'workers']).groupby('TAZ')['workers'].sum()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HHINCQ1(usim_households, zones):
    df = usim_households.to_frame(columns=['income', 'TAZ'])
    df = df[df.income < 30000]
    s = df.groupby('TAZ')['income'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HHINCQ2(usim_households, zones):
    df = usim_households.to_frame(columns=['income', 'TAZ'])
    df = df[df.income.between(30000, 59999)]
    s = df.groupby('TAZ')['income'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HHINCQ3(usim_households, zones):
    df = usim_households.to_frame(columns=['income', 'TAZ'])
    df = df[df.income .between(60000, 99999)]
    s = df.groupby('TAZ')['income'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HHINCQ4(usim_households, zones):
    df = usim_households.to_frame(columns=['income', 'TAZ'])
    df = df[df.income >= 100000]
    s = df.groupby('TAZ')['income'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGE0004(usim_persons, zones):
    df = usim_persons.to_frame(columns=['TAZ', 'age'])
    df = df[df.age.between(0, 4)]
    s = df.groupby('TAZ')['age'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGE0519(usim_persons, zones):
    df = usim_persons.to_frame(columns=['TAZ', 'age'])
    df = df[df.age.between(5, 19)]
    s = df.groupby('TAZ')['age'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGE2044(usim_persons, zones):
    df = usim_persons.to_frame(columns=['TAZ', 'age'])
    df = df[df.age.between(20, 44)]
    s = df.groupby('TAZ')['age'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGE4564(usim_persons, zones):
    df = usim_persons.to_frame(columns=['TAZ', 'age'])
    df = df[df.age.between(45, 64)]
    s = df.groupby('TAZ')['age'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGE65P(usim_persons, zones):
    df = usim_persons.to_frame(columns=['TAZ', 'age'])
    df = df[df.age >= 65]
    s = df.groupby('TAZ')['age'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGE62P(usim_persons, zones):
    df = usim_persons.to_frame(columns=['TAZ', 'age'])
    df = df[df.age >= 62]
    s = df.groupby('TAZ')['age'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def SHPOP62P(zones):
    return (zones.AGE62P / zones.HHPOP).reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def TOTEMP(jobs, zones):
    s = jobs.TAZ.groupby(jobs.TAZ).count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def RETEMPN(jobs, zones):
    df = jobs.to_frame(columns=['sector_id', 'TAZ'])

    # difference is here (44, 45 vs 4445)
    # sector ids don't match
    df = df[df.sector_id.isin([4445])]
    s = df.groupby('TAZ')['sector_id'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def FPSEMPN(jobs, zones):
    df = jobs.to_frame(columns=['sector_id', 'TAZ'])
    df = df[df.sector_id.isin([52, 54])]
    s = df.groupby('TAZ')['sector_id'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HEREMPN(jobs, zones):
    df = jobs.to_frame(columns=['sector_id', 'TAZ'])
    df = df[df.sector_id.isin([61, 62, 71])]
    s = df.groupby('TAZ')['sector_id'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def AGREMPN(jobs, zones):
    df = jobs.to_frame(columns=['sector_id', 'TAZ'])
    df = df[df.sector_id.isin([11])]
    s = df.groupby('TAZ')['sector_id'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def MWTEMPN(jobs, zones):
    df = jobs.to_frame(columns=['sector_id', 'TAZ'])

    # sector ids don't match
    df = df[df.sector_id.isin([42, 3133, 32, 4849])]
    s = df.groupby('TAZ')['sector_id'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def OTHEMPN(jobs, zones):
    df = jobs.to_frame(columns=['sector_id', 'TAZ'])

    # sector ids don't match
    df = df[~df.sector_id.isin([
        4445, 52, 54, 61, 62, 71, 11, 42, 3133, 32, 4849])]
    s = df.groupby('TAZ')['sector_id'].count()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def TOTACRE(blocks, zones, local_crs):

    # aggregate acreage from blocks
    blocks_df = blocks.to_frame(columns=['TOTACRE', 'TAZ'])
    s = blocks_df.groupby('TAZ')['TOTACRE'].sum()

    ## compute actual acreage of zone
    # zones_gdf = zones.to_frame(columns=['geometry'])

    # # project to meter-based crs
    # g = zones_gdf.to_crs(local_crs)

    # g['area'] = g['geometry'].area

    # # square meters to acres
    # area_polygons = g['area'] / 4046.86
    return s.reindex(zones.index).fillna(0)


@orca.column('zones', cache=True)
def HSENROLL(schools, zones):
    s = schools.to_frame(columns=['TAZ', 'enrollment']).groupby(
        'TAZ')['enrollment'].sum()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones')
def TOPOLOGY():
    # assumes everything is flat
    return 1


@orca.column('zones')
def employment_density(zones):
    return zones.TOTEMP / zones.TOTACRE


@orca.column('zones')
def pop_density(zones):
    return zones.HHPOP / zones.TOTACRE


@orca.column('zones')
def hh_density(zones):
    return zones.TOTHH / zones.TOTACRE


@orca.column('zones')
def hq1_density(zones):
    return zones.HHINCQ1 / zones.TOTACRE


@orca.column('zones')
def PRKCST(zones):
    params = pd.Series(
        [-1.92168743, 4.89511403, 4.2772001, 0.65784643], index=[
            'pop_density', 'hh_density', 'hq1_density',
            'employment_density'])

    cols = zones.to_frame(columns=[
        'employment_density', 'pop_density', 'hh_density', 'hq1_density'])

    s = cols @ params
    return s.where(s > 0, 0)


@orca.column('zones')
def OPRKCST(zones):
    params = pd.Series(
        [-6.17833544, 17.55155703, 2.0786466],
        index=['pop_density', 'hh_density', 'employment_density'])

    cols = zones.to_frame(
        columns=['employment_density', 'pop_density', 'hh_density'])

    s = cols @ params
    return s.where(s > 0, 0)


@orca.column('zones')  # College enrollment
def COLLFTE(colleges, zones):
    s = colleges.to_frame(columns=['TAZ', 'full_time_enrollment']).groupby(
        'TAZ')['full_time_enrollment'].sum()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones')  # College enrollment
def COLLPTE(colleges, zones):
    s = colleges.to_frame(columns=['TAZ', 'part_time_enrollment']).groupby(
        'TAZ')['part_time_enrollment'].sum()
    return s.reindex(zones.index).fillna(0)


@orca.column('zones')
def atm_zone(zones):

    zones_df = zones.to_frame(columns=['HHPOP', 'TOTEMP', 'TOTACRE'])

    metric_vals = (
        (1 * zones_df['HHPOP']) + (2.5 * zones_df['TOTEMP'])) / zones_df['TOTACRE']

    return metric_vals.fillna(0)



@orca.column('zones')
def area_type_metric(blocks, zones):

    # because of the MAUP, we have to aggregate the area_type_metric values
    # up from the block level to the h3 zone. instead of a simple average,
    # we us a weighted average of the blocks based on the square root of the
    # sum of the number of jobs and residents of each block. this method
    # was found to produce the best results in the Austin metro region compared
    # to the simple average (underclassified urban areas) and the fully
    # weighted average (overclassified too many CBDs).

    # it is probably a good idea to visually assess the accuracy of the
    # metric when implementing in a new region.

    blocks_df = blocks.to_frame(
        columns=['TAZ', 'TOTPOP', 'TOTEMP', 'area_type_metric'])
    blocks_df['weight'] = np.round(
        np.sqrt(blocks_df['TOTPOP'] + blocks_df['TOTEMP']))
    blocks_weighted = blocks_df.loc[
        blocks_df.index.repeat(blocks_df['weight'])]
    area_type_avg = blocks_weighted.groupby('TAZ')['area_type_metric'].mean()
    return area_type_avg.reindex(zones.index).fillna(0)


@orca.column('zones')
def area_type(zones):
    # Integer, 0=regional core, 1=central business district,
    # 2=urban business, 3=urban, 4=suburban, 5=rural
    area_types = pd.cut(
        zones['area_type_metric'],
        [0, 6, 30, 55, 100, 300, float("inf")],
        labels=['5', '4', '3', '2', '1', '0'],
        include_lowest=True).astype(str)
    return area_types


@orca.column('zones')
def TERMINAL():
    # TO DO:
    # Improve the imputation of this variable
    # Average time to travel from automobile storage location to
    # origin/destination. We assume zero for now
    return 0  # Assuming O


@orca.column('zones')
def COUNTY():
    return 1  # Assuming 1 all San Francisco County


# ** 4. Define Orca Steps **

@inject.step()
def load_usim_data(data_dir, settings):
    """
    Loads UrbanSim outputs into memory as Orca tables. These are then
    manipulated and updated into the format required by ActivitySim.
    """
    hdf = pd.HDFStore(
        os.path.join(data_dir, settings['usim_data_store']))
    households = hdf['/households']
    persons = hdf['/persons']
    blocks = hdf['/blocks']
    jobs = hdf['/jobs']

    hdf.close()

    # add home x,y coords to persons table
    persons_w_res_blk = pd.merge(
        persons, households[['block_id']],
        left_on='household_id', right_index=True)
    persons_w_xy = pd.merge(
        persons_w_res_blk, blocks[['x', 'y']],
        left_on='block_id', right_index=True)
    persons['home_x'] = persons_w_xy['x']
    persons['home_y'] = persons_w_xy['y']

    del persons_w_res_blk
    del persons_w_xy

    orca.add_table('usim_households', households, cache=True)
    orca.add_table('usim_persons', persons, cache=True)
    orca.add_table('blocks', blocks, cache=True)
    orca.add_table('jobs', jobs, cache=True)


# Export households tables
@inject.step()
def create_inputs_from_usim_data(data_dir):

    persons_table = os.path.exists(os.path.join(data_dir, "persons.csv"))
    households_table = os.path.exists(os.path.join(data_dir, "households.csv"))
    land_use_table = os.path.exists(os.path.join(data_dir, "land_use.csv"))

    # if the input tables don't exist yet, create them from urbansim data
    if not (persons_table & households_table & land_use_table):
        logger.info("Creating inputs from UrbanSim data")

        # create households input table
        hh_names_dict = {
            'household_id': 'HHID',
            'persons': 'PERSONS',
            'cars': 'VEHICL',
            'member_id': 'PNUM'}

        usim_households = orca.get_table('usim_households').to_frame()
        hh_df = usim_households.rename(columns=hh_names_dict)
        hh_df = hh_df[~hh_df.TAZ.isnull()]
        hh_df.to_csv(os.path.join(data_dir, 'households.csv'))
        del hh_df

        # create persons input table
        usim_persons = orca.get_table('usim_persons').to_frame()
        p_names_dict = {'member_id': 'PNUM'}
        p_df = usim_persons.rename(columns=p_names_dict)
        p_df = p_df[~p_df.TAZ.isnull()]
        p_df.sort_values('household_id', inplace=True)
        p_df.reset_index(drop=True, inplace=True)
        p_df.index.name = 'person_id'
        p_df.to_csv(os.path.join(data_dir, 'persons.csv'))
        del p_df

        # create land use input table
        lu_df = orca.get_table('zones').to_frame()
        lu_df.to_csv(os.path.join(data_dir, 'land_use.csv'))
        del lu_df

    else:
        logger.info("Found existing input tables, no need to re-create.")
