# %%
# OI using a constant mean function


import os
import re
import subprocess
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from global_land_mask import globe

# set tensorflow log level to INFO (?) - to reduce output to screen
# - needs to be done before tensorflow import
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'
import tensorflow as tf
from GPSat import get_data_path, get_parent_path
from GPSat.dataprepper import DataPrep
from GPSat.utils import (WGS84toEASE2, EASE2toWGS84, cprint, grid_2d_flatten, get_weighted_values,
                         get_git_information, pip_freeze_to_dataframe)
from GPSat.local_experts import LocalExpertOI, get_results_from_h5file
from GPSat.plot_utils import plot_pcolormesh, get_projection, plot_pcolormesh_from_results_data, plot_hyper_parameters
from GPSat.postprocessing import smooth_hyperparameters
from gpflow.functions import Constant

np.random.seed(42)
tf.random.set_seed(42)

# ---
# parameters
# ---

git_info = get_git_information()

package_info = pip_freeze_to_dataframe()

# run optimal interpolation
# - no predictions locations supplied
store_path = get_parent_path("results", f"inline_example_{git_info['branch']}_{git_info['commit'][:7]}_w_Constant_4.h5")

# re-run / completely replace results?
replace_results = False


# %%
# ----
# read in raw data
# ----

# read in all the *_RAW.csv files in data/example

raw_files = [get_data_path("example", i)
             for i in os.listdir(get_data_path("example")) if re.search("_RAW\.csv$", i)]

# read in, add source
tmp = []
for rw in raw_files:
    source = re.sub("_RAW\.csv$", "", os.path.basename(rw))
    _ = pd.read_csv(rw)
    _['source'] = source
    tmp.append(_)
df = pd.concat(tmp)


# convert lon, lat, datetime to x, y, t - to be used as the coordinate space
df['x'], df['y'] = WGS84toEASE2(lon=df['lon'], lat=df['lat'], lat_0=90, lon_0=0)
df['t'] = df['datetime'].values.astype("datetime64[D]").astype(float)

# %%
# ----
# bin raw data
# ----

# bin by date, source
# - returns a DataSet
bin_ds = DataPrep.bin_data_by(df=df.loc[(df['z'] > -0.25) & (df['z'] < 0.65)],
                              by_cols=['t', 'source'],
                              val_col='z',
                              x_col='x',
                              y_col='y',
                              grid_res=50_000,
                              x_range=[-4500000.0, 4500000.0],
                              y_range=[-4500000.0, 4500000.0])

# convert bin data to DataFrame
# - removing all the nans that would be added at grid locations away from data
bin_df = bin_ds.to_dataframe().dropna().reset_index()

# %%
# --
# plot binned data
# --

# this will plot all observations, some on top of each other
bin_df['lon'], bin_df['lat'] = EASE2toWGS84(bin_df['x'], bin_df['y'])

fig = plt.figure(figsize=(12, 12))
ax = fig.add_subplot(1, 1, 1, projection=get_projection('north'))

plot_pcolormesh(ax=ax,
                lon=bin_df['lon'],
                lat=bin_df['lat'],
                plot_data=bin_df['z'],
                title="example: binned obs",
                scatter=True,
                s=20,
                fig=fig,
                # vmin=[-]
                extent=[-180, 180, 60, 90])

plt.tight_layout()
plt.show()

# %%
# ----
# expert locations - on evenly spaced grid
# ----

expert_x_range = [-750000.0, 1000000.0]
expert_y_range = [-500000.0, 1250000.0]
xy_grid = grid_2d_flatten(x_range=expert_x_range,
                          y_range=expert_y_range,
                          step_size=200_000)

# store in dataframe
eloc = pd.DataFrame(xy_grid, columns=['x', 'y'])

# add a time coordinate
eloc['t'] = np.floor(df['t'].mean())

# %%
# ---
# plot expert locations
# ---

eloc['lon'], eloc['lat'] = EASE2toWGS84(eloc['x'], eloc['y'])


fig = plt.figure(figsize=(12, 12))
ax = fig.add_subplot(1, 1, 1, projection=get_projection('north'))

plot_pcolormesh(ax=ax,
                lon=eloc['lon'],
                lat=eloc['lat'],
                plot_data=eloc['t'],
                title="expert locations",
                scatter=True,
                s=20,
                fig=fig,
                # vmin=[-]
                extent=[-180, 180, 60, 90])

plt.tight_layout()
plt.show()

# %%
# ----
# prediction locations
# ----

pred_xy_grid = grid_2d_flatten(x_range=expert_x_range,
                               y_range=expert_y_range,
                               step_size=5_000)


# store in dataframe
# NOTE: the missing 't' coordinate will be determine by the expert location
# - alternatively the prediction location can be specified
ploc = pd.DataFrame(pred_xy_grid, columns=['x', 'y'])

ploc['lon'], ploc['lat'] = EASE2toWGS84(ploc['x'], ploc['y'])

# identify if a position is in the ocean (water) or not
ploc["is_in_ocean"] = globe.is_ocean(ploc['lat'], ploc['lon'])

# keep only prediction locations in ocean
ploc = ploc.loc[ploc["is_in_ocean"]]

# %%
# --
# prediction locations
# --


fig = plt.figure(figsize=(12, 12))
ax = fig.add_subplot(1, 1, 1, projection=get_projection('north'))

plot_pcolormesh(ax=ax,
                lon=ploc['lon'],
                lat=ploc['lat'],
                plot_data=np.full(len(ploc), 1.0), #np.arange(len(ploc)),
                title="prediction locations",
                scatter=True,
                s=0.1,
                # fig=fig,
                extent=[-180, 180, 60, 90])

plt.tight_layout()
plt.show()

# %%
# ----
# configurations:
# ----

# observation data
data = {
    "data_source": bin_df,
    "obs_col": "z",
    "coords_col": ["x", "y", "t"],
    # selection criteria used for each local expert
    "local_select": [
        {
            "col": "t",
            "comp": "<=",
            "val": 4
        },
        {
            "col": "t",
            "comp": ">=",
            "val": -4
        },
        {
            "col": [
                "x",
                "y"
            ],
            "comp": "<",
            "val": 300_000
        }
    ]
}

# local expert locations
local_expert = {
    "source": eloc
}

# model
model = {
    "oi_model": "GPflowGPRModel",
    "init_params": {
        # scale (divide) coordinates
        "coords_scale": [50000, 50000, 1],
        "mean_function": "Constant"
    },
    "constraints": {
        # lengthscales - same order coord_col (see data)
        "lengthscales": {
            # "low": [1e-08, 1e-08, 1e-08],
            "low": [5_000, 5_000, 1e-08],
            "high": [600000, 600000, 9]
        }
    }
}

# prediction locations
# -
pred_loc = {
    "method": "from_dataframe",
    "df": ploc,
    "max_dist": 200_000
}

# %%
# ----
# Local Expert OI
# ----


locexp = LocalExpertOI(expert_loc_config=local_expert,
                       data_config=data,
                       model_config=model,
                       pred_loc_config=pred_loc)



# for the purposes of a simple example, if store_path exists: delete it
if os.path.exists(store_path) and replace_results:
    cprint(f"removing: {store_path}")
    os.remove(store_path)

# run optimal interpolation
locexp.run(store_path=store_path,
           optimise=True,
           check_config_compatible=False)

# %%
# ----
# results are store in hdf5
# ----

# extract, store in dict
dfs, oi_config = get_results_from_h5file(store_path)

print(f"tables in results file: {list(dfs.keys())}")

# %%
# ---
# Plot Hyper Parameters
# ---

# a template to be used for each created plot config
plot_template = {
    "plot_type": "heatmap",
    "x_col": "x",
    "y_col": "y",
    # use a northern hemisphere projection, centered at (lat,lon) = (90,0)
    "subplot_kwargs": {"projection": "north"},
    "lat_0": 90,
    "lon_0": 0,
    # any additional arguments for plot_hist
    "plot_kwargs": {
        "scatter": True,
    },
    # lat/lon_col needed if scatter = True
    # TODO: remove the need for this
    "lat_col": "lat",
    "lon_col": "lon",
}

fig = plot_hyper_parameters(dfs,
                            coords_col=oi_config[0]['data']['coords_col'], # ['x', 'y', 't']
                            row_select=None, # this could be used to select a specific date in results data
                            table_names=["lengthscales", "kernel_variance", "likelihood_variance", "mean_function_c"],
                            plot_template=plot_template,
                            plots_per_row=3,
                            suptitle="hyper params",
                            qvmin=0.01,
                            qvmax=0.99)

plt.show()


# ----
# store the package composition
# ----

pd.set_option('display.max_columns', 200)

# add config_id - assume the last config was used
package_info['config_id'] = len(oi_config)

with pd.HDFStore(store_path, mode='a') as store:
    # add the current entry
    store.append(key="pip_freeze",
                 value=package_info,
                 index=False)


# skip smoothing


# %%
# ----
# Smooth Hyper Parameters
# ----

print("-" * 100)
print("smoothing hyper parameters")

smooth_config = {
    # get hyper parameters from the previously stored results
    "result_file": store_path,
    # store the smoothed hyper parameters in the same file
    "output_file": store_path,
    # get the hyper params from tables ending with this suffix ("" is default):
    "reference_table_suffix": "",
    # newly smoothed hyper parameters will be store in tables ending with table_suffix
    "table_suffix": "_SMOOTHED",
    # dimension names to smooth over
    "xy_dims": [
        "x",
        "y"
    ],
    # parameters to smooth
    "params_to_smooth": [
        "lengthscales",
        "kernel_variance",
        "likelihood_variance",
        "mean_function_c"
    ],
    # length scales for the kernel smoother in each dimension
    # - as well as any min/max values to apply
    "smooth_config_dict": {
        "lengthscales": {
            "l_x": 200_000,
            "l_y": 200_000
        },
        "likelihood_variance": {
            "l_x": 200_000,
            "l_y": 200_000,
            "max": 0.3
        },
        "kernel_variance": {
            "l_x": 200_000,
            "l_y": 200_000,
            "max": 0.1
        },
        "mean_function_c": {
            "l_x": 1_000,
            "l_y": 1_000,
        }
    },
    "save_config_file": True,
    "model_init_params": model["init_params"]
}

smooth_result_config_file = smooth_hyperparameters(**smooth_config)

# modify the model configuration to include "load_params"
model_load_params = model.copy()
model_load_params["load_params"] = {
                "file": store_path,
                "table_suffix": smooth_config["table_suffix"]
            }

locexp_smooth = LocalExpertOI(expert_loc_config=local_expert,
                              data_config=data,
                              model_config=model_load_params,
                              pred_loc_config=pred_loc)


# run optimal interpolation (again)
# - this time don't optimise hyper parameters, but make predictions
# - store results in new tables ending with '_SMOOTHED'
locexp_smooth.run(store_path=store_path,
                  optimise=False,
                  predict=True,
                  table_suffix=smooth_config['table_suffix'],
                  check_config_compatible=False)



# %%
# ---
# Plot Smoothed Hyper Parameters
# ---

# extract, store in dict
dfs, oi_config = get_results_from_h5file(store_path)

preds = dfs['preds_SMOOTHED']

# ref_loc = (-650000.0 -400000.0 18326.0)

# preds.loc[(preds['x'] == -650000.0) & (preds['y'] == -400000.0)]


fig = plot_hyper_parameters(dfs,
                            coords_col=oi_config[0]['data']['coords_col'], # ['x', 'y', 't']
                            row_select=None,
                            table_names=["lengthscales", "kernel_variance", "likelihood_variance",  "mean_function_c"],
                            table_suffix=smooth_config["table_suffix"],
                            plot_template=plot_template,
                            plots_per_row=3,
                            suptitle="smoothed hyper params",
                            qvmin=0.01,
                            qvmax=0.99)

plt.tight_layout()
plt.show()

# %%
# ----
# get weighted the predictions and plot
# ----

# plt_data = dfs["preds"]
plt_data = dfs["preds" + smooth_config["table_suffix"]]
# plt_data = dfs["preds"]
#
# plt_data = dfs['GPR_mean_function_c_SMOOTHED']
# plt_data = dfs['GPR_kernel_lengthscales']
# dfs.keys()


# tmp = plt_data.loc[(plt_data['x'] == 950000.0) & \
#                    (plt_data['y'] == 1200000.0)]


weighted_values_kwargs = {
        "ref_col": ["pred_loc_x", "pred_loc_y", "pred_loc_t"],
        "dist_to_col": ["x", "y", "t"],
        "val_cols": ["f*", "f*_var"],
        "weight_function": "gaussian",
        "lengthscale": 200_000
    }
plt_data = get_weighted_values(df=plt_data, **weighted_values_kwargs)

plt_data['lon'], plt_data['lat'] = EASE2toWGS84(plt_data['pred_loc_x'], plt_data['pred_loc_y'])


fig = plt.figure(figsize=(12, 12))
ax = fig.add_subplot(1, 1, 1, projection=get_projection('north'))
plot_pcolormesh_from_results_data(ax=ax,
                                  dfs={"preds": plt_data},
                                  table='preds',
                                  val_col="f*",
                                  x_col='pred_loc_x',
                                  y_col='pred_loc_y',
                                  fig=fig)
plt.tight_layout()
plt.show()


# %%