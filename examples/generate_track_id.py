# assign (arbitrary) track number for raw observation data
# - this script is a work in progress
import os.path

import numpy as np
import pandas as pd
import numba as nb

import matplotlib.pyplot as plt

from PyOptimalInterpolation.plot_utils import plot_pcolormesh, get_projection
from PyOptimalInterpolation.utils import WGS84toEASE2_New, cprint
from PyOptimalInterpolation.dataloader import DataLoader

# ---
# helper function
# ---


@nb.jit(nopython=True)
def guess_track_num(x, thresh):
    out = np.full(len(x), np.nan)
    track_num = 0
    for i in range(0, len(x)):
        # if there is a jump, increment track
        if x[i] > thresh:
            track_num += 1
        out[i] = track_num
    return out


@nb.jit(nopython=True)
def track_num_for_date(x):
    out = np.full(len(x), np.nan)
    out[0] = 0
    for i in range(1, len(x)):
        if x[i] == x[i - 1]:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 0

    return out


def diff_distance(x, p=2, k=1):
    # given a 2-d array, get the p-norm distance between (k) rows
    # require x be 2d if it is 1d
    if len(x.shape) == 1:
        x = x[:, None]
    assert len(x.shape) == 2, f"x must be 2d, len(x.shape) = {len(x.shape)}"
    out = np.full(x.shape[0], np.nan)

    # get the difference raised to the pth power
    dx = (x[k:, :] - x[:-k, :]) ** p
    # sum over rows
    dx = np.sum(dx, axis=1)
    # raise to the 1/p
    dx = dx ** (1 / p)
    # populate output array
    out[k:] = dx

    return out

# ----
# parameters
# -----

# TODO: add ability to further subset data, e.g. by each 'sat' - just wrap in for loop

# to get 'daily' track numbers
add_by_track_date = True

# data_file = get_data_path("RAW", "sats_ra_cry_processed_arco.h5")
# data_file = get_data_path("RAW", "gpod_all_rows.h5")
# data_file = get_data_path("RAW", "gpod_lead.h5")
# data_file = get_data_path("RAW", "sats_ra_cry_processed_arco_all_elev.h5")
data_file = "/mnt/m2_red_1tb/Data/CSAO/SAR_A.h5"
# data_file = "/mnt/m2_red_1tb/Data/CSAO/SIN.h5"

# table = "_data_batches"
table = "data"

# output table
out_table = f"{table}_w_tracks"
assert out_table != table

# TODO: allow for a group or split by, e.g. 'sat' - also allow it to be missing (implied for a single sat)
# sort_by = ['sat', 'datetime']
sort_by = ['datetime']

# lon, lat = 'lon', 'lat'
lon_col, lat_col = 'lon_20_ku', 'lat_20_ku'
lat_0, lon_0 = -90, 0

# for plotting
pole = "south"
# set to None to skip plotting
plot_before = "2019-01-15"

# ---
# read in data
# ---

pd.set_option("display.max_columns", 200)

assert os.path.exists(data_file)

print(f"reading in {table}")
with pd.HDFStore(data_file, mode="r") as store:
    print(store.keys())
    df = store.get(table)
    # get the input config
    storer = store.get_storer(table)
    try:
        input_config = storer.attrs['config']
        run_info = storer.attrs['run_info']
    except Exception as e:
        print(e)

# --
# prep data
# --
# NOTE: this bit is fairly hardcoded

print(f"data has: {len(df)} rows")

dt = df['datetime'].values

df.sort_values(sort_by, inplace=True)

# handle this when reading in?
# df['elev_mss'] = df['elev'] - df['mss']

# get time in ms
df['t'] = df['datetime'].values.astype("datetime64[ms]").astype(float)

# add x,y coordinates - will use for measuring distance
df['x'], df['y'] = WGS84toEASE2_New(df[lon_col], df[lat_col], lat_0=lat_0, lon_0=lon_0)

# select a single satellite
# all_sats = df['sat'].unique()
# _ = df.loc[df['sat'] == all_sats[0]].copy(True)

_ = df  # .copy(True)

# create a distance (space or time) to next observation
x = _[['x', 'y']].values

# distance in space
_['dr'] = diff_distance(x, p=2, k=1)
# distance in time
_['dt'] = diff_distance(_['t'].values, p=2, k=1)

# plt.plot(_.iloc[:1000, :]['dt'].values)

# hardcoded: columns just add to drop before writing to file
drop_columns = ['x', 'y', 't', 'dt', 'dr']

# ------
# visualise the deltas - in order to determine the delta cut off size for a new track
# ------

if plot_before is not None:
    print("visualise")
    # identify tracks - where there is a large jump in 'distance' between obs
    # - space distance will be in meters
    # _.loc[_['dr'] > (50 * 1000)]

    # pick take a subset of dates
    tmp = _.loc[_['datetime'] < plot_before].copy(True)

    # NOTE: time differences seem to be unreliable for
    # gpod_all_rows.h5
    # - would expect there to be a large jump - review how they a parsed
    plt.plot(tmp['datetime'][:10000], tmp['dt'][:10000])
    plt.title("time diff")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()

    plt.plot(tmp['datetime'], tmp['dr'])
    plt.title("space diff")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()

# ---
# id tracks
# ---

# based on time delta
_['track'] = guess_track_num(_['dt'].values, 2e6).astype(int)
# distance delta
# _['track'] = guess_track_num(_['dr'].values, 2e6)


# ---
# track by start_date
# ---

if add_by_track_date:
    # starting datetime of each track
    tsd = pd.pivot_table(_,
                         index=['track'],
                         values=['datetime'],
                         aggfunc='min').reset_index()
    tsd.rename(columns={"datetime": "start_date"}, inplace=True)
    tsd['start_date'] = tsd['start_date'].astype("datetime64[D]")

    # for each start_date get the track number
    tsd[['start_date', 'track']].drop_duplicates()

    sd = tsd['start_date'].values.astype('int')

    # track number for given start date
    tsd['track_start'] = track_num_for_date(sd).astype(int)

    _ = _.merge(tsd,
                on=['track'],
                how='left')

# --
# plot tracks
# --

tmp = _.loc[_['datetime'] < plot_before].copy(True)

utracks = tmp['track'].unique()
print(f"unique track count: {len(utracks)}")

select_tracks = np.random.choice(utracks, 200, replace=False)
# select_tracks = utracks[:26]
# select_bool = tmp['track'].isin(select_tracks).values
select_bool = tmp['date'].isin(["2019-01-01"])

lat, lon = tmp.loc[select_bool, lat_col].values, tmp.loc[select_bool, lon_col].values

# z = tmp['elev'].values
# z[np.isnan(z)] = np.nanmean(z)

# z = np.arange(len(tmp))
z = tmp.loc[select_bool, 'track'].values
print(len(np.unique(z)))
# TOOD: plot a random subset of tracts

# first plot: heat map of observations


# parameters for projections
projection = get_projection(pole)
extent = [-180, 180, 60, 90] if pole == "north" else [-180, 180, -60, -90]

figsize = (20, 20)
fig = plt.figure(figsize=figsize)

ax = fig.add_subplot(1, 1, 1,
                     projection=projection)

cmap = 'YlGnBu_r'
cmap = 'nipy_spectral'
# TODO: allo for plotting the south pole
plot_pcolormesh(ax,
                lon=lon,
                lat=lat,
                plot_data=z,
                scatter=True,
                s=5,
                fig=fig,
                cbar_label="track number",
                cmap=cmap,
                extent=extent)

# - would it be faster to write to file
plt.show()

# ----
# write to table
# ----

_.drop(drop_columns, axis=1, inplace=True)

cprint("writing:", c='OKBLUE')
print(_.head(2))
cprint(f"to:\n{data_file}\ntable: '{out_table}'", c='OKBLUE')
with pd.HDFStore(data_file, mode="a") as store:

    track_config = {"comment": f"added tracks using data from table: '{table}'"}
    run_info = DataLoader.get_run_info()

    # format table to get 'pytables' functionality
    store.put(out_table, _, data_columns=True, format='table')

    storer = store.get_storer(out_table)
    storer.attrs["config"] = track_config
    storer.attrs["run_info"] = run_info
