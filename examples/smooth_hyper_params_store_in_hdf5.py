#%%
# smooth values from results table, store in separate files - to be used for generating predictions late
import json
import re

import pandas as pd
import numpy as np
import numba as nb

import matplotlib.pyplot as plt

from astropy.convolution import convolve, Gaussian2DKernel

from PyOptimalInterpolation.utils import json_serializable, cprint
from PyOptimalInterpolation.utils import EASE2toWGS84_New, dataframe_to_2d_array, nested_dict_literal_eval
from PyOptimalInterpolation.plot_utils import plot_pcolormesh, get_projection
from PyOptimalInterpolation import get_parent_path, get_data_path
from PyOptimalInterpolation.models.gpflow_models import GPflowGPRModel
from PyOptimalInterpolation.decorators import timer

# TODO: tidy up this script!

# ---
# helper function
# ---


def smooth_2d(x, stddev=None, x_stddev=None, y_stddev=None,
              min=None, max=None):

    # TODO: make nan masking optional?
    if x_stddev is None:
        x_stddev = stddev
    if y_stddev is None:
        y_stddev = stddev
    assert (x_stddev is not None) & (y_stddev is not None), \
        f"x_stddev is: {x_stddev}, y_stddev is: {y_stddev} , they both can't be None"

    nan_mask = np.isnan(x)

    # apply clipping and trimming
    if min is not None:
        min_mask = x < min
        x[min_mask] = min
    if max is not None:
        max_mask = x > max
        x[max_mask] = max

    # convert 0s to NaNs
    x[x==0] = np.nan

    # smooth out lengthscales
    out = convolve(x, Gaussian2DKernel(x_stddev=x_stddev, y_stddev=y_stddev))
    out[nan_mask] = np.nan

    return out


# @timer
@nb.guvectorize([(nb.float64[:], nb.float64[:], nb.float64[:], nb.float64[:],
                  nb.float64[:], nb.float64[:], nb.float64[:], nb.float64[:]),
                 (nb.float32[:], nb.float32[:], nb.float32[:], nb.float32[:],
                  nb.float32[:], nb.float32[:], nb.float32[:], nb.float32[:])],
                '(), (), (n), (n), (), (), (n)->()',
                nopython=True, target='parallel')
def gaussian_2d_weight(x0, y0, x, y, l_x, l_y, vals, out):
    """weight functions of the form exp(-d^2), where d is the distance between reference position
    (x0, y0) and the others"""

    # calculate the squared distance from the reference equation (normalising dist in each dimension by a length_scale)
    # - can they be specified with defaults?
    d2 = ((x-x0)/l_x[0]) ** 2 + ((y - y0)/l_y[0])**2

    # get the weight function (un-normalised)
    w = np.exp(-d2/2)

    # get the weighted sum of vals, skipping vals which are nan
    w_sum = 0
    w_val = 0
    for i in range(len(vals)):
        if ~np.isnan(vals[i]):
            w_val += w[i] * vals[i]
            w_sum += w[i]

    # if all weights are zero, i.e. in the case all nan vals, return np.nan
    if w_sum == 0:
        out[0] = np.nan
    # otherwise return the normalised weighted value
    else:
        out[0] = w_val / w_sum

# ----
# parameters
# ----

# results file
# store_path = get_parent_path("results", "synthetic", "ABC_baseline.h5")
# store_path = get_parent_path("results", "xval", "cs2cpom_elev_lead_binned_xval_25x25km.h5")
# store_path = get_parent_path("results", "GPFGPR_cs2s3cpom_2019-2020_25km.h5")
# store_path = get_parent_path("results", "elev", "GPOD_elev_lead_binned_25x25km_rerun_BKUP.h5")
store_path = get_parent_path("results", "xval", "cs2cpom_lead_binned_date_2019_2020_25x25km.h5")


# pd.set_option("display.max_columns", 200)
# store = pd.HDFStore(store_path, mode='r')
#
# for k in store.keys():
#     print("-" * 10)
#     print(k)
#     print(store.get(k).head(2))
#


# list of all hyper-parameters to fetch from results - all need not be smooth
all_hyper_params = ["lengthscales", "likelihood_variance", "kernel_variance"]

table_suffix = "_SMOOTHED"
assert table_suffix != ""

# output config file
out_config = re.sub("\.h5$", f"{table_suffix}.json", store_path)

# new prediction locations? set to None if
new_pred_loc = None
# new_pred_loc = {
#     "method": "from_dataframe",
#     "df_file": get_data_path("locations", "2d_xy_grid_5x5km.csv"),
#     "max_dist": 200000
# }

# store smoothed results in separate file
# out_file = re.sub("\.h5$", "_SMOOTHED.h5", store_path)
out_file = store_path

# method / function to use
# use_method = "smooth_2d"
use_method = "gaussian_2d_weight"

# hyper parameters to smooth
# - specify the parameters to smooth with keys, and smoothing parameters as values
# smooth_dict = {
#     "lengthscales": {
#         "stddev": 1
#     },
#     "likelihood_variance": {
#         "stddev": 1
#     },
#     "kernel_variance": {
#         "stddev": 1,
#         "max": 0.1
#     }
# }

# smoothing dict to use with use_method = "gaussian_2d_weight"
smooth_dict = {
    "lengthscales": {
        "l_x": 200_000,
        "l_y": 200_000,
        "max": 12
    },
    "likelihood_variance": {
        "l_x": 200_000,
        "l_y": 200_000
    },
    "kernel_variance": {
        "l_x": 200_000,
        "l_y": 200_000,
        "max": 0.1
    }
}

# determine the dimensions to smooth over, will be used to make a 2d array
x_col, y_col = 'x', 'y'

# plot values?
plot_values = True

# additional parameters to be passed to dataframe_to_2d_array
to_2d_array_params = {}

# used only for plotting, and if x_col, y_col = 'x', 'y'
EASE2toWGS84_New_params = {}
# projection only used for plotting, same conditions above
projection = 'north'

# ----
# read in all hyper parameters
# ----

# read in all the hyper parameter tables
where = None
with pd.HDFStore(store_path, mode="r") as store:
    # TODO: determine if it's faster to use select_colum - does not have where condition?

    all_keys = store.keys()
    dfs = {re.sub("/", "", k): store.select(k, where=where).reset_index()
           for k in all_hyper_params}

    try:
        oi_configs = store.get("oi_config")
        config_df = oi_configs[['config']].drop_duplicates()
        oi_configs = [nested_dict_literal_eval(json.loads(c)) for c in config_df['config'].values]
        # take the most recent one for reference
        oi_config = oi_configs[-1]
    except KeyError as e:
        oi_config = store.get_storer("oi_config").attrs['oi_config']
        oi_configs = [oi_config]

# check all keys in smooth_dict are in all_hyper_params
for k in smooth_dict.keys():
    assert k in all_hyper_params

# coords_col: used as an multi-index in hyperparameter tables
coords_col = oi_config['data']['coords_col']

# -----
# (optionally) smooth hyper parameters
# -----
#%%
out = {}

for hp in all_hyper_params:
    # if current hyper parameter is specified in the smooth dict
    if hp in smooth_dict:
        df = dfs[hp].copy(True)
        if use_method == "smooth_2d":
            df[hp] = df[hp].fillna(0) # Replace NaNs with zero

        df_org_col_order = df.columns.values.tolist()
        # smoothing params
        smooth_params = smooth_dict[hp]
        # get the other (None smoothing) dimensions, to iterate over
        other_dims = [c for c in coords_col if c not in [x_col, y_col]]
        # add the other "_dim_*" columns
        dim_cols = [c for c in df.columns if re.search("^_dim_\d", c)]
        other_dims += dim_cols
        # get the unique combinations of other_dims, used to select subset of data
        unique_odims = df[other_dims].drop_duplicates()

        # increment over the rows -want to get a DataFrame representation of each row
        smooth_list = []
        for idx, row in unique_odims.iterrows():
            # get the row as a DataFrame
            row_df = row.to_frame().T

            # and merge on the other dim columns
            _ = row_df.merge(df,
                             on=other_dims,
                             how='inner')

            if use_method == "smooth_2d":
                # convert dataframe to 2d array - this expects x_col, y_cols to be regularly spaced!
                val2d, x_grid, y_grid = dataframe_to_2d_array(_, val_col=hp, x_col=x_col, y_col=y_col,
                                                              **to_2d_array_params)

                # apply smoothing (includes nan masking - make optional?)
                smth_2d = smooth_2d(val2d, **smooth_params)

            # experimental smoothing method
            elif use_method == "gaussian_2d_weight":

                # converting to 2d to be able to plot
                # val2d, x_grid, y_grid = dataframe_to_2d_array(_, val_col=hp, x_col=x_col, y_col=y_col,
                #                                               **to_2d_array_params)

                x0, y0 = [_[c].values for c in [x_col, y_col]]
                x, y = [_[c].values for c in [x_col, y_col]]
                vals = _[hp].values

                if 'max' in smooth_params:
                    vals[vals > smooth_params['max']] = smooth_params['max']

                if 'min' in smooth_params:
                    vals[vals < smooth_params['min']] = smooth_params['min']

                l_x, l_y = smooth_params.get("l_x", 1), smooth_params.get("l_y", 1)

                tmp = gaussian_2d_weight(x0, y0, x, y, l_x, l_y, vals)
                _[f"{hp}_smooth"] = tmp

                _['lon'], _['lat'] = EASE2toWGS84_New(_[x_col], _[y_col])

                if plot_values:
                    figsize = (15, 15)

                    fig, ax = plt.subplots(figsize=figsize,
                                           subplot_kw={'projection': get_projection(projection)})

                    plot_pcolormesh(ax,
                                    lon=_['lon'],
                                    lat=_['lat'],
                                    plot_data=_[hp],
                                    scatter=True,
                                    s=200,
                                    fig=fig,
                                    cbar_label=f"{hp}_smooth\n{row_df}",
                                    cmap='YlGnBu_r')

                    plt.tight_layout()
                    plt.show()


                val2d, x_grid, y_grid = dataframe_to_2d_array(_,
                                                                val_col=hp,
                                                                x_col=x_col,
                                                                y_col=y_col,
                                                                **to_2d_array_params)

                smth_2d, x_grid, y_grid = dataframe_to_2d_array(_,
                                                                val_col=f"{hp}_smooth",
                                                                x_col=x_col,
                                                                y_col=y_col,
                                                                **to_2d_array_params)

            else:
                raise NotImplementedError(f"use_method: {use_method} is not implemented")

            # TODO: optionally plot here?
            #  - show the original and smoothed side by side, could show on a map
            # if plot_values:
            #
            #     fig = plt.figure(figsize=(15, 8))
            #     row_str = ", ".join([f"{k}: {v}" for k,v in row.items()])
            #     smooth_str = ", ".join([f"{k}: {v}" for k,v in smooth_params.items()])
            #     fig.suptitle(f"hyper-parameter: {hp}\nselecting: {row_str}\nsmooth_params: {smooth_str}")
            #
            #     # TODO: could replace this with plot_pcolormesh
            #     if (x_col == 'x') & (y_col == 'y'):
            #         lon, lat = EASE2toWGS84_New(x_grid, y_grid, **EASE2toWGS84_New_params)
            #
            #         ax = plt.subplot(1, 2, 1, projection=get_projection(projection))
            #
            #         # make sure both plots have same vmin/max
            #         vmax = np.max([np.nanquantile(_, q=0.99) for _ in [smth_2d, val2d] ])
            #         vmin = np.min([np.nanquantile(_, q=0.01) for _ in [smth_2d, val2d]])
            #
            #         plot_pcolormesh(ax=ax,
            #                         lon=lon,
            #                         lat=lat,
            #                         plot_data=val2d,
            #                         title="original",
            #                         fig=fig,
            #                         vmin=vmin,
            #                         vmax=vmax)
            #
            #         ax = plt.subplot(1, 2, 2, projection=get_projection(projection))
            #         plot_pcolormesh(ax=ax,
            #                         lon=lon,
            #                         lat=lat,
            #                         plot_data=smth_2d,
            #                         title="smoothed",
            #                         fig=fig,
            #                         vmin=vmin,
            #                         vmax=vmax)
            #
            #     else:
            #         ax = plt.subplot(1, 2, 1)
            #         ax.imshow(val2d)
            #         ax.set_title("original")
            #
            #         ax = plt.subplot(1, 2, 2)
            #         ax.imshow(smth_2d)
            #         ax.set_title("smoothed")
            #
            #     plt.tight_layout()
            #     plt.show()

            # put values back in dataframe
            tmp = pd.DataFrame({
                hp: smth_2d.flatten(),
                x_col: x_grid.flatten(),
                y_col: y_grid.flatten()})

            # drop nans
            tmp.dropna(inplace=True)

            # add in the 'other dimension' values
            for od in other_dims:
                tmp[od] = row[od]

            # re-order columns to previous order: strictly not needed
            tmp = tmp[df_org_col_order]

            smooth_list.append(tmp)

        smooth_df = pd.concat(smooth_list)

        # set index to be coordinates column
        smooth_df.set_index(coords_col, inplace=True)

        out[hp] = smooth_df

    # if not smoothing, just take values as is
    else:
        # set index to coords_col
        out[hp] = dfs[hp].set_index(coords_col)

# ---
# write results to table
# ---


cprint(f"writing (smoothed) hyper parameters to:\n{out_file}\ntable_suffix:{table_suffix}", c="OKGREEN")
with pd.HDFStore(out_file, mode="a") as store:
    for k, v in out.items():
        out_table = f"{k}{table_suffix}"
        # TODO: confirm this will overwrite existing table?
        store.put(out_table, v, format="table", append=False)

        store_attrs = store.get_storer(out_table).attrs
        store_attrs['smooth_config'] = smooth_dict[k]


# ---
# write the configs to file

tmp = []
for oic in oi_configs:
    # change, update the run kwargs to not optimise and use the table_suffix
    run_kwargs = oic.get("run_kwargs", {})
    run_kwargs["optimise"] = False
    run_kwargs["table_suffix"] = table_suffix
    run_kwargs["store_path"] = out_file

    # add load_params - load from self
    model = oic["model"]
    model["load_params"] = {
        "file": out_file,
        "table_suffix": table_suffix
    }

    oic["run_kwargs"] = run_kwargs
    oic["model"] = model

    if new_pred_loc is not None:
        oic["pred_loc"] = new_pred_loc

    tmp.append(json_serializable(oic))

cprint(f"writing config (to use to make predictions with smoothed values) to:\n{out_config}", c="OKBLUE")
with open(out_config, "w") as f:
    json.dump(tmp, f, indent=4)

# oi_configs
# # specifying table_suffix



# TODO: here optionally write oi_config to file, with load_params specified
#  - first should add 'run_kwargs' back to config
# from PyOptimalInterpolation.utils import json_serializable
# oi_config["model"]["load_params"] = {"file": out_file}
# with open(get_parent_path("configs", "check.json"), "w") as f:
#     json.dump(json_serializable(oi_config), f, indent=4)


# %%
