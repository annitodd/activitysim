import numpy as np
import pandas as pd
import itertools
import random
from shapely import wkt
from shapely.geometry import Point, MultiPoint
import geopandas as gpd
import logging

from activitysim.core import pipeline
from activitysim.core import inject

logger = logging.getLogger('activitysim')


def expand_trips_table(trips, tours, jtp):
    """
    This code comes from
    https://github.com/RSGInc/activitysim/blob/develop/notebooks/trips_in_time_and_space.ipynb
    """
    logger.info("Expanding the trips table for multi-person trips.")
    trips_dtypes = trips.dtypes.to_dict()

    # 1. add related fields, including joint trip participant ids

    logger.info("Adding new fields to expanded trips.")
    trips["tour_participants"] = trips.tour_id.map(
        tours.number_of_participants)
    trips["tour_category"] = trips.tour_id.map(tours.tour_category)
    trips["parent_tour_id"] = trips.tour_id.map(
        tours.index.to_series()).map(tours.parent_tour_id)
    trips["tour_start"] = trips.tour_id.map(tours.start)
    trips["tour_end"] = trips.tour_id.map(tours.end)
    trips["parent_tour_start"] = trips.parent_tour_id.map(tours.start)
    trips["parent_tour_end"] = trips.parent_tour_id.map(tours.end)
    trips["inbound"] = ~trips.outbound

    # 2. create additional trips records for other persons on joint trips
    logger.info("Adding new records for joint trips.")
    tour_person_ids = jtp.groupby("tour_id").apply(lambda x: pd.Series(
        {"person_ids": " ".join(x["person_id"].astype("str"))}))
    trips = trips.join(tour_person_ids, "tour_id")
    trips["person_ids"] = trips["person_ids"].fillna("")
    trips.person_ids = trips.person_ids.where(
        trips.person_ids != "", trips.person_id)
    trips["person_ids"] = trips["person_ids"].astype(str)

    person_ids = [*map(
        lambda x: x.split(" "), trips.person_ids.tolist())]
    person_ids = list(itertools.chain.from_iterable(person_ids))

    trips_expanded = trips.loc[
        np.repeat(trips.index, trips['tour_participants'])]
    trips_expanded.person_id = person_ids

    trips_expanded["trip_id"] = trips_expanded.index
    trips_expanded["trip_id"] = trips_expanded["trip_id"].astype('complex128')

    logger.info("Entering a sketchy while loop.")
    while trips_expanded["trip_id"].duplicated().any():
        trips_expanded["trip_id"] = trips_expanded["trip_id"].where(
            ~trips_expanded["trip_id"].duplicated(),
            trips_expanded["trip_id"] + 0.1)

    trips_expanded = trips_expanded.sort_values([
        'person_id', 'tour_start', 'tour_id', 'inbound', 'trip_num'])

    # 3. Pull out at-work trips and put back in at the right spot
    logger.info("Re-ordering new at-work trips in expanded trips table.")
    atwork_trips = trips_expanded[trips_expanded.tour_category == "atwork"]

    trips_expanded_last_trips = trips_expanded[
        trips_expanded.trip_num == trips_expanded.trip_count]
    parent_tour_trips_with_atwork_trips = trips_expanded_last_trips.merge(
        atwork_trips, left_on="tour_id", right_on="parent_tour_id")
    parent_tour_trips_with_atwork_trips["atwork_depart_after"] = \
        parent_tour_trips_with_atwork_trips.eval("depart_y >= depart_x")

    parent_trip_id = parent_tour_trips_with_atwork_trips[
        parent_tour_trips_with_atwork_trips["atwork_depart_after"]]
    parent_trip_id.index = parent_trip_id["trip_id_y"]

    logger.info("Looping through hella persons.")
    for person in tqdm(parent_trip_id["person_id_x"].unique(), total=len(parent_trip_id["person_id_x"].unique())):

        person_all_trips = trips_expanded[(
            trips_expanded["person_id"].astype("str") == person) & (
            trips_expanded.tour_category != "atwork")]

        person_atwork_trips = parent_trip_id[
            parent_trip_id["person_id_x"].astype("str") == person]
        parent_trip_index = person_all_trips.index.astype(
            'complex128').get_loc(person_atwork_trips.trip_id_x[0])

        before_trips = person_all_trips.iloc[0:(parent_trip_index + 1)]
        after_trips = person_all_trips.iloc[(parent_trip_index + 1):]

        person_actual_atwork_trips = atwork_trips[(
            atwork_trips["person_id"].astype("str") == person)]

        new_person_trips = before_trips.append(
            person_actual_atwork_trips).append(after_trips)

        # remove and add back due to indexing
        trips_expanded = trips_expanded[
            ~(trips_expanded["person_id"].astype("str") == person)]
        trips_expanded = trips_expanded.append(new_person_trips)

    logger.info("Assessing consistency of new trips table.")
    trips_expanded["next_person_id"] = trips_expanded["person_id"].shift(-1)
    trips_expanded["next_origin"] = trips_expanded["origin"].shift(-1)
    trips_expanded["next_depart"] = trips_expanded["depart"].shift(-1)
    trips_expanded["spatial_consistent"] = \
        trips_expanded["destination"] == trips_expanded["next_origin"]
    trips_expanded["time_consistent"] = \
        trips_expanded["next_depart"] >= trips_expanded["depart"]
    trips_expanded["spatial_consistent"].loc[
        trips_expanded["next_person_id"] != trips_expanded["person_id"]] = True
    trips_expanded["time_consistent"].loc[
        trips_expanded["next_person_id"] != trips_expanded["person_id"]] = True

    print("{}\n\n{}".format(
        trips_expanded["spatial_consistent"].value_counts(),
        trips_expanded["time_consistent"].value_counts()))

    # make sure data types are the same as in the original trips table
    logger.info("Converting dtypes in new trips table to their original dtypes.")
    for col, dt in trips_expanded.dtypes.to_dict().items():
        if col in trips_dtypes.keys():
            if dt != trips_dtypes[col]:
                new_dt = trips_dtypes[col].name
                trips_expanded[col] = trips_expanded[col].astype(new_dt)

    trips_expanded = trips_expanded[[
        col for col in trips_expanded.columns if col != 'trip_id']]
    return trips_expanded


def random_points_in_polygon(number, polygon):
    '''
    Generate n number of points within a polygon
    Input:
    -number: n number of points to be generated
    - polygon: geopandas polygon
    Return:
    - List of shapely points
    source: https://gis.stackexchange.com/questions/294394/
        randomly-sample-from-geopandas-dataframe-in-python
    '''
    points = []
    min_x, min_y, max_x, max_y = polygon.bounds
    i = 0
    while i < number:
        point = Point(
            random.uniform(min_x, max_x), random.uniform(min_y, max_y))
        if polygon.contains(point):
            points.append(point)
            i += 1
    return points  # returns list of shapely point


def sample_geoseries(geoseries, size, overestimate=2):
    '''
    Generate at most "size" number of points within a polygon
    Input:
    - size: n number of points to be generated
    - geoseries: geopandas polygon
    - overestimate = int to multiply the size. It will account for
        points that may fall outside the polygon
    Return:
    - List points
    source: https://gis.stackexchange.com/questions/294394/
        randomly-sample-from-geopandas-dataframe-in-python
    '''
    polygon = geoseries.unary_union
    min_x, min_y, max_x, max_y = polygon.bounds
    ratio = polygon.area / polygon.envelope.area
    overestimate = 2
    samples = np.random.uniform(
        (min_x, min_y), (max_x, max_y), (int(size / ratio * overestimate), 2))
    multipoint = MultiPoint(samples)
    multipoint = multipoint.intersection(polygon)
    samples = np.array(multipoint)
    return samples[np.random.choice(len(samples), size)]


def get_trip_coords(trips, zones, persons, size=500):

    # Generates random points within each zone
    rand_point_zones = {}
    for zone in zones.TAZ:
        size = 500
        polygon = zones[zones.TAZ == zone].geometry
        points = sample_geoseries(polygon, size, overestimate=2)
        rand_point_zones[zone] = points

    # Assign semi-random (within zone) coords to trips
    df = trips[['origin']].reset_index().drop_duplicates('trip_id')
    origins = []
    for i, row in enumerate(df.itertuples(), 0):
        origins.append(random.choice(rand_point_zones[row.origin]))

    origins = np.array(origins)
    df['origin_x'] = origins[:, 0]
    df['origin_y'] = origins[:, 1]
    df = df.set_index('trip_id').reindex(trips.index)
    trips['origin_x'] = df['origin_x']
    trips['origin_y'] = df['origin_y']

    # retain home coords from urbansim data bc they will typically be
    # higher resolution than zone, so we don't need the semi-random coords
    trips = pd.merge(
        trips, persons[['home_x', 'home_y']],
        left_on='person_id', right_index=True)
    trips['origin_purpose'] = trips.groupby(
        'person_id')['purpose'].shift(periods=1).fillna('Home')
    trips['x'] = trips.origin_x.where(
        trips.origin_purpose != 'Home', trips.home_x)
    trips['y'] = trips.origin_y.where(
        trips.origin_purpose != 'Home', trips.home_y)

    return trips


def generate_departure_times(trips):
    df = trips[['person_id', 'depart']].reset_index().drop_duplicates('trip_id')
    df['frac'] = np.random.rand(len(df),)
    df.index.name = 'og_df_idx'
    # Making sure trips within the hour are sequential
    ordered = df.sort_values(
        by=['person_id', 'depart', 'frac']).reset_index()
    df2 = df.sort_values(by=['person_id', 'depart']).reset_index()
    df2['fractional'] = ordered.frac

    # Adding fractional to int hour
    df2['depart'] = np.round(df2['depart'] + df2['fractional'], 3)
    df2.set_index('og_df_idx', inplace=True)
    df2 = df2.reindex(df.index)
    df2.set_index('trip_id', inplace=True)
    df2 = df2.reindex(trips.index)
    return df2.depart


@inject.step()
def generate_beam_plans():

    # Importing ActivitySim results
    trips = pipeline.get_table('trips')
    tours = pipeline.get_table('tours')
    persons = pipeline.get_table('persons')
    zones = pipeline.get_table('land_use')

    # re-cast zones as a geodataframe
    zones['geometry'] = zones['geometry'].apply(wkt.loads)
    zones = gpd.GeoDataFrame(zones, geometry='geometry', crs="EPSG:4326")
    zones.geometry = zones.geometry.buffer(0)
    zones.reset_index(inplace=True)

    # expand trips table
    # THIS TAKES WAYYYYYY TOO LONG TO DO RIGHT NOW AND
    # BEAM CANT EVEN DEAL WITH PASSENGER TRIPS AT THE
    # MOMENT SO WE'RE TURNING IT OFF UNTIL WE CAN FIND
    # A BETTER SOLUTION
    # trips = expand_trips_table(trips, tours, jtp)
    # logger.info("Adding expanded trips table to pipeline.")
    # pipeline.replace_table("trips_expanded", trips)

    # augment trips table
    trips = get_trip_coords(trips, zones, persons)
    trips['departure_time'] = generate_departure_times(trips)

    # trim trips table
    cols = [
        'person_id', 'departure_time', 'purpose', 'origin',
        'destination', 'trip_mode', 'x', 'y']
    trips = trips[cols].sort_values(
        ['person_id', 'departure_time']).reset_index()

    # Adding a new row for each unique person_id
    # this row will represent the returning trip
    return_trip = pd.DataFrame(
        trips.groupby('person_id').agg({'x': 'first', 'y': 'first'}),
        index=trips.person_id.unique())

    trips = trips.append(return_trip)
    trips.reset_index(inplace=True)
    trips.person_id.fillna(trips['index'], inplace=True)

    # Creating the Plan Element activity Index
    # Activities have odd indices and legs (actual trips) will be even
    trips['PlanElementIndex'] = trips.groupby('person_id').cumcount() * 2 + 1
    trips = trips.sort_values(
        ['person_id', 'departure_time']).reset_index(drop=True)

    # Shifting type one row down
    trips['ActivityType'] = trips.groupby(
        'trip_id')['purpose'].shift(periods=1).fillna('Home')
    trips['ActivityElement'] = 'activity'

    # Creating legs (trips between activities)
    legs = pd.DataFrame({
        'PlanElementIndex': trips.PlanElementIndex - 1,
        'person_id': trips.person_id})
    legs = legs[legs.PlanElementIndex != 0]

    # Adding the legs to the main table
    trips = trips.append(legs).sort_values(['person_id', 'PlanElementIndex'])
    trips.ActivityElement.fillna('leg', inplace=True)

    trips['trip_id'] = trips['trip_id'].shift()

    # save back to pipeline
    pipeline.replace_table("beam_plans", trips[[
        'trip_id', 'person_id', 'PlanElementIndex', 'ActivityElement',
        'ActivityType', 'x', 'y', 'departure_time']])
