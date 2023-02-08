# read binned data
# select local data - relative to some reference location
# provide to GP Model
# optimise hyper parameters
# make predictions
# extract hyper parameters

# TODO: this script is a mess and needs to be refactored
# BEFORE RUNNING: Double check the inline config below!

import os
import re
import time
import tables
import warnings

import numpy as np
import xarray as xr
import pandas as pd

from functools import reduce

from PyOptimalInterpolation import get_parent_path, get_data_path
from PyOptimalInterpolation.utils import check_prev_oi_config
from PyOptimalInterpolation.models import GPflowGPRModel
from PyOptimalInterpolation.dataloader import DataLoader
from PyOptimalInterpolation.local_experts import LocalExpertOI


# --
# helper functions
# --

def get_where_list(global_select, local_select=None, ref_loc=None):
    # store results in list
    out = []
    for gs in global_select:
        # check if static where
        is_static = all([c in gs for c in ['col', 'comp', 'val']])
        # if it's a static where condition just add
        if is_static:
            out += [gs]
        # otherwise it's 'dynamic' - i.e. a function local_select and reference location
        else:
            # require local_select and ref_loc are provided
            assert local_select is not None, \
                f"dynamic where provide: {gs}, however local_select is: {type(local_select)}"
            assert ref_loc is not None, \
                f"dynamic where provide: {gs}, however ref_loc is: {type(ref_loc)}"
            # check required elements are
            assert all([c in gs for c in ['loc_col', 'src_col', 'func']]), \
                f"dynamic where had keys: {gs.keys()}, must have: ['loc_col', 'src_col', 'func'] "
            # get the location column
            loc_col = gs['loc_col']
            # require location column is reference
            assert loc_col in ref_loc, f"loc_col: {loc_col} not in ref_loc: {ref_loc}"

            func = gs['func']
            if isinstance(func, str):
                func = eval(func)
            # increment over the local select - will make a selection
            for ls in local_select:
                # if the location column matchs the local select
                if loc_col == ls['col']:
                    # create a 'where' dict using comparison and value from local select
                    _ = {
                        "col": gs['src_col'],
                        "comp": ls['comp'],
                        "val": func(ref_loc[loc_col], ls['val'])
                    }
                    out += [_]

    return out


def get_xarray_bool_from_where_dict(ds, w):

    coord = getattr(ds,w['col'])
    # _ = eval(f"{coord} {w['comp']} {val}")
    if w['comp'] == "<=":
        out = coord <= w['val']
    elif w['comp'] == ">=":
        out = coord >= w['val']
    else:
        assert False
    return out


def remove_previously_run_locations(store_path, xprt_locs):
    # read existing / previous results
    try:
        with pd.HDFStore(store_path, mode='r') as store:
            # get index from previous results
            prev_res = store.select('run_details', columns=[])
            keep_bool = ~xprt_locs.index.isin(prev_res.index)
            print(f"using: {keep_bool.sum()} / {len(keep_bool)} reference locations - some were already found")
            xprt_locs = xprt_locs.loc[~xprt_locs.index.isin(prev_res.index)]
    except OSError as e:
        print(e)
    except KeyError as e:
        print(e)

    return xprt_locs


def get_previous_oi_config(store_path, oi_config, skip_valid_checks_on=None):

    if skip_valid_checks_on is None:
        skip_valid_checks_on = []

    # if the file exists - it is expected to contain a dummy table (oi_config) with oi_config as attr
    if os.path.exists(store_path):
        # TODO: put try/except here
        with pd.HDFStore(store_path, mode='r') as store:
            prev_oi_config = store.get_storer("oi_config").attrs['oi_config']
    else:
        with pd.HDFStore(store_path, mode='a') as store:
            _ = pd.DataFrame({"oi_config": ["use get_storer('oi_config').attrs['oi_config'] to get oi_config"]},
                             index=[0])
            # TODO: change key to configs / config_info
            store.append(key="oi_config", value=_)
            # HACK: in one case 'date' was too long

            try:
                store.get_storer("oi_config").attrs['oi_config'] = oi_config
            except tables.exceptions.HDF5ExtError as e:
                # TODO: log
                print(e)
                oi_config['local_expert_locations']['add_cols'].pop('date')
                store.get_storer("oi_config").attrs['oi_config'] = oi_config
                skip_valid_checks_on += ['local_expert_locations']

            # store.get_storer("raw_data_config").attrs["raw_data_config"] = raw_data_config
            # store.get_storer("oi_config").attrs['input_data_config'] = input_data_config
            prev_oi_config = oi_config

    return prev_oi_config


# silence INFO messages from tf
# In detail:- ref: https://stackoverflow.com/questions/70429982/how-to-disable-all-tensorflow-warnings
# 0 = all messages are logged (default behavior) ,
# 1 = INFO messages are not printed ,
# 2 = INFO and WARNING messages are not printed ,
# 3 = INFO, WARNING, and ERROR messages are not printed
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'

import tensorflow as tf

gpus = tf.config.list_physical_devices('GPU')
print("GPUS")
print(gpus)

# TODO: move sections of this file into methods of a new class (GridOI?)
# TODO: want to specify select in config that can work for netcdf or ndf5
# TODO: improve / add ref location functionality
# TODO: add run attributes to the output data - local select, coord scale
#  - namely so can be build model from scratch


pd.set_option("display.max_columns", 200)

# TODO: silence tensorflow NUMA warning
# TODO: specify an OI config, store in an output table (run_details?)
#  - if table exists check config is compatible (identical?)
# ---
# parameters
# ---

# parameters for location selection
days_ahead = 4
days_behind = 4
incl_rad = 300 * 1000

# ocean_or_lead = "lead"
# TODO: (briefly) think about file naming convention
# TODO: need to specify date (year) range
ocean_or_lead = "freeboard"

# column in data to use as observations
# obs_col = "elev"
# obs_col = "elev_mss"
obs_col = "obs"

# parameters use to specify input / output files
# grid_size = "10km"
# grid_size = "25km"
grid_size = "50km"

data_source = "cs2s3cpom"
# data_source = "gpod"

season = "2019-2020"

# should data be de-meaned naively? e.g. should a local mean be subtracted?
prior_mean = None
# prior_mean = "local"


# REVIEW BELOW: namely results, input_data, local_expert_locations
# oi_config file
oi_config = {
    "results": {
        "dir": get_parent_path("results", "freeboard"),
        "file": f"oi_bin_{data_source}_{days_ahead}_{int(incl_rad / 1000)}_{ocean_or_lead}_{obs_col}_{grid_size}_{prior_mean}.h5"
    },
    "input_data": {
        "file_path": get_data_path("binned", f"{data_source}_{season}_{grid_size}.zarr"),
        "obs_col": obs_col,
        "coords_col": ['x', 'y', 't'],
        "col_funcs": {
            "date": {"func": "lambda x: x.astype('datetime64[D]')", "col_args": "date"},
            "t": {"func": "lambda x: x.astype('datetime64[D]').astype(int)", "col_args": "date"}
        }
    },
    # from either ncdf, zarr or ndf
    "global_select": [
        # static where - condition like this wi
        {"col": "lat", "comp": ">=", "val": 60},
        # dynamic where - become a function of expert location and local
        # - loc_col - column / coordinate of expert location
        # - src_col - column / coordinate in data source
        # - func - function to transform loc_col (from expert location)
        #     and val (from local_select)
        {"loc_col": "t", "src_col": "date", "func": "lambda x,y: np.datetime64(pd.to_datetime(x+y, unit='D'))"}
    ],
    # how to select data for local expert - i.e. within the vicinity
    "local_select": [
        {"col": "t", "comp": "<=", "val": days_ahead},
        {"col": "t", "comp": ">=", "val": -days_behind},
        {"col": ["x", "y"], "comp": "<", "val": incl_rad}
    ],
    "constraints": {
        "lengthscales": {
            "low": [0, 0, 0],
            "high": [2 * incl_rad, 2 * incl_rad, days_ahead + days_behind + 1]
        }
    },
    "local_expert_locations": {
        # file path of expert locations
        "file": get_data_path("aux", "SIE", f"SIE_masking_{grid_size}_{season}_season.csv"),
        # columns shall be added or manipulated as follows
        # are these neded
        "col_funcs": {
            "date": {"func": "lambda x: x.astype('datetime64[D]')", "col_args": "date"},
            "t": {"func": "lambda x: x.astype('datetime64[D]').astype(int)", "col_args": "date"},
        },
        # keep only relevant columns - (could keep all?)
        "keep_cols": ["x", "y", "date", "t"],
        # select a subset of expert locations
        "row_select": [
            # select locations with dates in Dec 2018
            # TODO: provide example of using specific dates - used 'func'
            {"col": "date", "comp": ">=", "val": "2020-03-01"},
            {"col": "date", "comp": "<", "val": "2020-03-15"},
            # latitude values above 65N
            {"col": "lat", "comp": ">=", "val": 65},
            # sie extent > 0.15 (15%)
            {"col": "sie", "comp": ">=", "val": 0.15}
        ]
    },
    # DEBUGGING: shouldn't skip model params - only skip misc (?)
    # "skip_valid_checks_on": ["local_expert_locations", "misc", "results", "input_data"],
    "skip_valid_checks_on": ["local_expert_locations", "misc"],
    # parameters to provide to model (inherited from BaseGPRModel) when initialising
    "model_params": {
        "coords_scale": [50000, 50000, 1]
    },
    "misc": {
        "store_every": 10,
        # TODO: this should be used in the model_params
        "obs_mean": prior_mean
    }
}
# local expert locations
# HARDCODED: dates for local expert locations

local_expert_locations = oi_config["local_expert_locations"]

# store result in a directory
result_dir = oi_config['results']['dir']
result_file = oi_config['results']['file']
os.makedirs(result_dir, exist_ok=True)
store_path = os.path.join(result_dir, result_file)

# global selection criteria
global_select = oi_config.get("global_select", [])

# selection criteria for local expert
local_select = oi_config.get("local_select", [])

# TODO: all "skip_valid_checks_on" in config just to be a str -> convert to list
skip_valid_checks_on = ["skip_valid_checks_on"] + oi_config.get("skip_valid_checks_on", [])

# input data
# CURRENTLY ONLY HANDLES FILES FOR open_dataset
input_data_file = oi_config['input_data']['file_path']
input_col_funcs = oi_config['input_data'].get("col_funcs", {})

# columns containing observations and coordinates
obs_col = oi_config['input_data']['obs_col']
coords_col = oi_config['input_data']['coords_col']

# parameters for model
model_params = oi_config.get("model_params", {})

# misc
misc = oi_config.get("misc", {})
store_every = misc.get("store_every", 10)
obs_mean = misc.get("obs_mean", None)

# --------
# initialise LocalExpertOI object
# --------

locexp = LocalExpertOI()

# ---
# read / connect to data source (set data_source)
# ---

locexp.set_data_source(file=input_data_file)

# ---------
# expert locations
# ---------

locexp.local_expert_locations(**local_expert_locations)

# TODO: get / generate the reference location more systematically
# TODO: review this - coordinates should be in multi-index (?)
#  - allow for extra columns (date, lon, lat?)
# extract expert locations - for now ...
xprt_locs = locexp.expert_locs.copy(True)

# TODO: review if using ref_locs.index is the best way
# - remove columns
# set multi index of ref_locs
# - this is a bit messy, done so can use index.isin(...) when reading in previous result
tmp_index = xprt_locs.set_index(xprt_locs.columns.values.tolist())
xprt_locs.index = tmp_index.index

# ------------
# remove previously found local expert locations
# ------------

xprt_locs = remove_previously_run_locations(store_path, xprt_locs)

# ---
# check previous oi config is consistent
# ---

prev_oi_config = get_previous_oi_config(store_path, oi_config,
                                        skip_valid_checks_on=skip_valid_checks_on)

# check previous oi_config matches current - want / need them to be consistent (up to a point)
check_prev_oi_config(prev_oi_config, oi_config,
                     skip_valid_checks_on=skip_valid_checks_on)

# ----
# Load initial global data
# ----

# get the first ref location
rl = xprt_locs.iloc[0]

# get current where list
cur_where = get_where_list(global_select,
                           local_select=local_select,
                           ref_loc=rl)

# extract 'global' data
df = DataLoader.data_select(obj=locexp.data_source,
                            where=cur_where,
                            return_df=True,
                            reset_index=True)

# add additional columns to data - as needed
DataLoader.add_cols(df, col_func_dict=input_col_funcs)


prev_where = cur_where

# get global data where for first location
# DataLoader.data_select(ds, where, table=None, return_df=True, drop=True, copy=True)

# sort by date/t - cant use sort_values because od index having same name
t_arg_sort = np.argsort(xprt_locs['t'].values)
xprt_locs = xprt_locs.iloc[t_arg_sort, :]

# create a dictionary to store result (DataFrame / tables)
store_dict = {}
count = 0
for idx, rl in xprt_locs.iterrows():

    # TODO: use log_lines
    print("-" * 30)
    count += 1
    print(f"{count} / {len(xprt_locs)}")

    # ----------------------------
    # (update) global data
    # ----------------------------

    # given the current expert locations - get the list of where dicts to select global data
    cur_where = get_where_list(global_select,
                               local_select=local_select,
                               ref_loc=rl)

    # check if where's are the same - here the order matters
    where_same = all([w == prev_where[i] for i, w in enumerate(cur_where)])

    # if there where conditions (for global data) have changed
    # - update global data
    if not where_same:
        print("*|" * 40)
        print("reading in new global data")
        print(rl)
        df = DataLoader.data_select(obj=locexp.data_source,
                                    where=cur_where,
                                    return_df=True,
                                    reset_index=True)
        DataLoader.add_cols(df, col_func_dict=input_col_funcs)
        # update the previous where list
        prev_where = cur_where

    # start timer
    t0 = time.time()

    # ----------------------------
    # select local data - relative to expert's location
    # ----------------------------

    df_local = DataLoader.local_data_select(df,
                                            reference_location=rl,
                                            local_select=local_select,
                                            verbose=False)
    print(f"number obs: {len(df_local)}")

    # TODO: if number obs = 0 should just skip (write default values)
    # TODO: clean this up - handle in model params

    # TODO: specify a min number of observations required?
    if len(df_local) >= 2:

        # TODO: this should be in model_params
        if obs_mean == "local":
            # print("will subtract local mean")
            obsmu = df_local[obs_col].mean()
        elif obs_mean == "global":
            obsmu = df[obs_col].mean()
        elif obs_mean is None:
            obsmu = None
        else:
            print(f"obs_mean: {obs_mean} not understood, not using anything")
            obsmu = None

        # -----
        # build model - provide with data
        # -----

        # initialise model
        # TODO: needed to review the unpacking of model_params, when won't it work?
        gpr_model = GPflowGPRModel(data=df_local,
                                   obs_col=obs_col,
                                   coords_col=coords_col,
                                   obs_mean=obsmu,
                                   **model_params)

        # --
        # apply constraints
        # --

        # TODO: generalise this to apply any constraints - use apply_param_transform (may require more checks)
        #  - may need information from config, i.e. obj = model.kernel, specify the bijector, other parameters

        if "lengthscales" in oi_config['constraints']:
            print("applying lengthscales contraints")
            low = oi_config['constraints']['lengthscales'].get("low", np.zeros(len(coords_col)))
            high = oi_config['constraints']['lengthscales'].get("high", None)
            gpr_model.set_lengthscale_constraints(low=low, high=high, move_within_tol=True, tol=1e-8, scale=True)

        # --
        # optimise parameters
        # --

        opt_dets = gpr_model.optimise_hyperparameters()

        # get the hyper parameters - for storing
        hypes = gpr_model.get_hyperparameters()

        # --
        # make prediction - at the local expert location
        # --

        pred = gpr_model.predict(coords=rl)
        # - remove y to avoid conflict with coordinates
        # pop no longer needed?
        pred.pop('y')

        # remove * from names - causes issues when saving to hdf5 (?)
        # TODO: make this into a private method
        for k, v in pred.items():
            if re.search("\*", k):
                pred[re.sub("\*","s", k)] = pred.pop(k)

        t1 = time.time()

        # ----
        # store results in tables (keys) in hdf file
        # ----

        run_time = t1-t0

        device_name = gpr_model.cpu_name if gpr_model.gpu_name is None else gpr_model.gpu_name

        # run details / info - for reference
        run_details = {
            "num_obs": len(df_local),
            "run_time": run_time,
            # "device": device_name,
            "mll": opt_dets['marginal_loglikelihood'],
            "optimise_success":  opt_dets['optimise_success']
        }

        # store data to specified tables according to key
        # - will add mutli-index based on location
        pred_df = pd.DataFrame(pred, index=[0])
        pred_df.rename(columns={c: re.sub("\*", "s", c) for c in pred_df.columns}, inplace=True)
        save_dict = {
            "preds": pred_df,
            "run_details": pd.DataFrame(run_details, index=[0]),
            **hypes
        }
    # otherwise there are too few observations
    else:

        # store dummy run details
        # - needed so can skip these later (if restarted)

        run_details = {
            "num_obs": len(df_local),
            "run_time": np.nan,
            # "device": "",
            "mll": np.nan,
            "optimise_success":  False
        }
        save_dict = {
            "run_details": pd.DataFrame(run_details, index=[0])
        }

    # ---
    # append results or write to file
    # ---

    tmp = DataLoader.make_multiindex_df(idx_dict=rl, **save_dict)

    # if store dict is empty - populate with list of multi-index dataframes
    if len(store_dict) == 0:
        store_dict = {k: [v] for k, v in tmp.items()}
        num_store = 1
    # otherwise add
    else:
        for k, v in tmp.items():
            if k in store_dict:
                store_dict[k] += [v]
            # for non 'run_details' maybe missing
            else:
                store_dict[k] = [v]
        num_store += 1

    if num_store >= store_every:
        print("SAVING RESULTS")
        for k, v in store_dict.items():
            print(k)
            df_tmp = pd.concat(v, axis=0)
            try:
                with pd.HDFStore(store_path, mode='a') as store:
                    store.append(key=k, value=df_tmp, data_columns=True)
            except ValueError as e:
                print(e)
            except Exception as e:
                cc = 1

        store_dict = {}
        num_store = 0

    t2 = time.time()
    print(f"total run time (including saving): {t2-t0:.2f} seconds")


    # if count > 3:
    #     break