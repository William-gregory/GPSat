# given results file containing an oi_config
# - read in input data
# - read in hyper-parameters, store as DataArray (?)
# - for a given (local) expert location
# - - select local data
# - - set previously generated hyper parameters
# - validate previous predictions against current


import os
import re
import datetime

import numpy as np
import xarray as xr
import pandas as pd

from PyOptimalInterpolation import get_parent_path
from PyOptimalInterpolation.models import GPflowGPRModel
from PyOptimalInterpolation.dataloader import DataLoader

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'
pd.set_option("display.max_columns", 200)

# TODO: confirm if using a different conda environment (say without a GPU) will get the same results
# TODO: run a full validation / regression to ensure all results are recovered
# TODO:

# ---
# parameters
# ---


# results_dir = get_parent_path("results", "gpod_lead_25km_INVST")
results_dir = get_parent_path("results", "example")
# file = f"oi_bin_4_300.h5"
file = f"ABC_binned3.h5"

store_path = os.path.join(results_dir, file)

# ---
# read in previous config
# ---

with pd.HDFStore(store_path, mode='r') as store:
    oi_config = store.get_storer("oi_config").attrs['oi_config']

# extract needed components
# local_select = oi_config['local_select']
# obs_col = oi_config['input_data']['obs_col']
# coords_col = oi_config['input_data']['coords_col']
# model_params = oi_config.get("model_params", {})

local_select = oi_config['data']['local_select']
obs_col = oi_config['data']['obs_col']
coords_col = oi_config['data']['coords_col']
model_params = oi_config['model'].get("init_params", {})

# ---
# read in previous (global) data
# ---


# TODO: reading in input_data should be done by a method in GridOI and handle many different input file types
#
# input_data_file = oi_config['input_data']['file_path']
input_data_file = oi_config['data']['data_source']


# connect to Dataset
ds = xr.open_dataset(input_data_file)

# get the configuration(s) use to generate dataset
# raw_data_config = ds.attrs['raw_data_config']
# bin_config = ds.attrs['bin_config']

# ---
# prep data
# ---

# TODO: how input_data is prepped should be specified in config and be done by a method

df = ds.to_dataframe().dropna().reset_index()
df['date'] = df['date'].values.astype('datetime64[D]')
df['t'] = df['date'].values.astype('datetime64[D]').astype(int)

# --
# load previous results
# ---


with pd.HDFStore(store_path, mode='r') as store:
    prev_preds = store.get("preds")
    prev_det = store.get("run_details")

# get previous expert locations - from multi-index
# - this is maybe a bit faffy, have a duplication of data...
# expt_locs = pd.DataFrame(index=prev_preds.index).reset_index()
# expt_locs.index = prev_preds.index


idx_names = prev_det.index.names
expt_locs = prev_preds.reset_index()[idx_names]

# ---
# get local data (for local expert)
# ---

# select a reference location
i = 1
rl = expt_locs.iloc[i, :]
# idx = expt_locs.index[i]

# select local data
df_local = DataLoader.local_data_select(df,
                                        reference_location=rl,
                                        local_select=local_select,
                                        verbose=False)
print(f"number obs: {len(df_local)}")

# ---
# initialise GP model
# ---

gpr_model = GPflowGPRModel(data=df_local,
                           obs_col=obs_col,
                           coords_col=coords_col,
                           **model_params)

# check a prediction works
# pred = gpr_model.predict(coords=rl)

# ---
# set hyper parameters
# ---

# TODO: setting of hyper parameters could be done more cleanly

# - get hyper parameter names
# - what would be a better way of doing this?
# initial_hyps = gpr_model.get_parameters()
# initial_hyps = gpr_model.get_parameters()
param_names = gpr_model.param_names


# rl_where = [f"{k} == {str(v)}"
#             if not isinstance(v, datetime.date) else
#             f"{k} == '{str(v)}'"
#             for k, v in rl.to_dict().items()]

rl_where = [f"{k} == {str(v)}"
            if not isinstance(v, datetime.date) else
            f"{k} == '{str(v)}'"
            for k, v in rl.to_dict().items()
            if k in gpr_model.coords_col]



hyp_dict = {}
prev_hyps = {}
# - here could use select with a where condition - need to convert location to compatible where
with pd.HDFStore(store_path, mode='r') as store:
    for k in param_names:
        prev_hyps[k] = store.select(k, where=rl_where)
        tmp = DataLoader.mindex_df_to_mindex_dataarray(df=prev_hyps[k],
                                                       data_name=k,
                                                       infer_dim_cols=True)
        hyp_dict[k] = tmp.values[0]

# store as DataArray with a dimension which is a multi-index
# hyp_dict = {}
# for k, v in prev_hyps.items():
#     tmp = DataLoader.mindex_df_to_mindex_dataarray(df=v.loc[[idx]],
#                                                        data_name=k,
#                                                        infer_dim_cols=True)
#     hyp_dict[k] = tmp.values[0]

# select for current location
# hyp_dict = {}
# for k, v in hyps.items():
#     # hyp_dict[k] = v.sel(index=idx).values
#     hyp_dict[k] = v.values[0]

# set hyper parameters
gpr_model.set_parameters(**hyp_dict)

gpr_model.get_parameters()

# ----
# make prediction
# ----

pred = gpr_model.predict(coords=rl)

# rename prediction values - as done before...
for k, v in pred.items():
    if re.search("\*", k):
        pred[re.sub("\*", "s", k)] = pred.pop(k)

# check previous predictions are in line
pp = prev_preds.loc[idx].to_dict()

tol = 1e-15
for k, v in pp.items():
    diff = np.abs(v - pred[k])
    assert diff < tol, f"difference for {k} was {diff}, greater than tol: {tol}"

