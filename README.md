ActivitySim
===========

[![Build Status](https://travis-ci.org/UDST/activitysim.svg?branch=master)](https://travis-ci.org/UDST/activitysim) [![Coverage Status](https://coveralls.io/repos/UDST/activitysim/badge.png?branch=master)](https://coveralls.io/r/UDST/activitysim?branch=master)

ActivitySim is an open platform for activity-based travel modeling.  It emerged
from a consortium of Metropolitan Planning Organizations (MPOs) and other
transportation planning agencies that wanted to build a shared, open, platform
that could be easily adapted to their individual needs, but would share a
robust, efficient, and well-maintained common core.

## Loosest Coupling

Initial work to loosely couple ActivitySim and UrbanSim for integrated modeling

### PHASE I:
Getting long-term choice models from ActivitySim to run in sequence with UrbanSim. These models include **auto ownership**, **school location choice** and **workplace location choice**.

#### TO DO
- ActivitySim <--> UrbanSim
  - [x] script to merge households tables
  - [x] script to update `asim.persons` table, i.e. drop persons with households not included in updated `asim.households` table
  - [x] get ActivitySim to run on `usim.households`-derived data
  - [ ] script to merge ActivitySim and UrbanSim outputs
  - [ ] automate the sequential execution of UrbanSim and ActivitySim long-term choice models
  - [ ] update `land_use/taz_data` data in **mtc_asim.h5** datastore to reflect changes after UrbanSim run
    - `land_use/taz_data` seems to have its closest UrbanSim analog in the **taz_summaries_XXXX.csv** UrbanSim outputs, but some of the headers are different.
  - [ ] update skims.omx to reflect changes after UrbanSim run

### PHASE II:
Sequential execution of UrbanSim, long-term ActivitySim models, and daily travel ActivitySim models to generate OD demand data

#### TO DO
- ActivitySim --> Traffic Assignment
  - [ ] Script to take ActivitySim output and generate demand data for zone-based OD pairs
    - How do we deal with time?
- Traffic Assignment --> ActivitySim
  - [ ]  update `skims/accessibility` data in **mtc_asim.h5** 


## Documentation

https://udst.github.io/activitysim
