import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
from osgeo import gdal
from pycequeau.core import utils as u
import rasterstats as rs
from math import ceil
import itertools
import sys


def convert_coords_to_index(df: gpd.GeoDataFrame,
                            dataset: gdal.Dataset) -> gpd.GeoDataFrame:
    band = dataset.GetRasterBand(1)
    cols = dataset.RasterXSize
    rows = dataset.RasterYSize
    df2 = pd.concat([df, pd.DataFrame(columns=["col_min", "row_min", "col_max", "row_max"],
                                      index=df.index.values)], axis=1)
    transform = dataset.GetGeoTransform()
    xOrigin = transform[0]
    yOrigin = transform[3]
    pixelWidth = transform[1]
    pixelHeight = -transform[5]
    for i in range(len(df)):
        df2.at[i, "col_min"] = ceil((df["minx"].iloc[i] - xOrigin)/pixelWidth)
        df2.at[i, "row_max"] = int((yOrigin - df["miny"].iloc[i])/pixelHeight)
        df2.at[i, "col_max"] = ceil((df["maxx"].iloc[i]-xOrigin)/pixelWidth)
        df2.at[i, "row_min"] = int((yOrigin - df["maxy"].iloc[i])/pixelHeight)
    return df2


def find_neighbors(gdf: gpd.GeoDataFrame,
                   id: str) -> gpd.GeoDataFrame:
    # https://gis.stackexchange.com/questions/281652/finding-all-neighbors-using-geopandas
    # Drop the column if it exist
    if 'NEIGHBORS' in gdf.columns:
        gdf = gdf.drop(columns=["NEIGHBORS", "KEEP"])
    # add NEIGHBORS column
    gdf = gdf.reindex(columns=gdf.columns.tolist() + ['NEIGHBORS', 'KEEP'])
    gdf["NEIGHBORS"] = ''
    gdf["KEEP"] = ''
    columns = gdf.columns.tolist()
    count = 0
    for index, CP in gdf.iterrows():
        # get 'not disjoint' countries
        neighbors = gdf[~gdf.geometry.disjoint(CP.geometry)][id].tolist()
        keep = gdf[~gdf.geometry.disjoint(CP.geometry)].Dissolve.tolist()
        # remove own name of the country from the list
        keep = [bool(numb) for numb, name in zip(
            keep, neighbors) if CP[id] != name]
        neighbors = [name for name in neighbors if CP[id] != name]
        # add names of neighbors as NEIGHBORS value
        # Catch an exception here
        try:
            gdf.at[index, 'NEIGHBORS'] = neighbors
            gdf.at[index, 'KEEP'] = keep
        except:
            if isinstance(neighbors,list):
                gdf["NEIGHBORS"].iloc[count] = neighbors
                gdf['KEEP'].iloc[count] = keep
            else:
                gdf["NEIGHBORS"].iloc[count] = list([int(neighbors)])
                gdf['KEEP'].iloc[count] = list([int(keep)])
        count += 1
    return gdf


def identify_small_CPs(CE_fishnet: gpd.GeoDataFrame,
                       CP_fishnet: gpd.GeoDataFrame,
                       thereshold: float):
    # Get the area of the CE grid
    CE_area = CE_fishnet.area[0]
    # Get area for each CP feature
    CP_fishnet = CP_fishnet.dropna(subset=['CEid'])
    CP_fishnet = CP_fishnet.explode()
    # CP_fishnet["CPid"] = range(1,len(CP_fishnet)+1)
    CP_fishnet["Area"] = CP_fishnet.area
    CP_fishnet = CP_fishnet.dropna(subset=['Area'])
    # Mask to drop values with extremly tiny areas
    mask_area = CP_fishnet["Area"] > 1.0
    CP_fishnet = CP_fishnet[mask_area]
    CP_fishnet["CPid"] = range(1, len(CP_fishnet)+1)
    mask_CP = CP_fishnet["Area"] < thereshold*CE_area
    CP_fishnet["Dissolve"] = 0
    CP_fishnet.at[mask_CP, "Dissolve"] = 1
    # Get the CP ousite the subbasin
    # This need to be changed for anohter
    mask_SUB = np.isnan(CP_fishnet["CATid"])
    index_drop = CP_fishnet.index[mask_SUB]
    # Drop this value from the main dataframe
    CP_fishnet = CP_fishnet.drop(index=index_drop)
    # CP_fishnet.at[mask_SUB, "Dissolve"] = 0
    CP_fishnet.index = CP_fishnet["CPid"].values
    # CP_fishnet["CPid"] = CP_fishnet.index
    CP_fishnet.index = CP_fishnet.index.rename("index")
    return CP_fishnet


def remove_border_CPs(CE_fishnet: gpd.GeoDataFrame,
                      CP_fishnet: gpd.GeoDataFrame,
                      FAC: str) -> list:
    # Get the area of the CE grid
    CE_area = CE_fishnet.area[0]
    # Add the bounds for each polygon

    bounds = CE_fishnet["geometry"].bounds
    CE_fishnet = pd.concat([CE_fishnet, bounds], axis=1)
    # FAC_dataset = gdal.Open(FAC)
    # CE_fishnet = convert_coords_to_index(CE_fishnet, FAC_dataset)

    CP_fishnet = pd.concat([CP_fishnet, pd.DataFrame(columns=["maxFAC"],
                                                     index=CP_fishnet.index)], axis=1)
    columnsCP = CP_fishnet.columns.tolist()
    for index, CE in CE_fishnet.iterrows():
        # Get the index for all the subbasin features
        idx, = np.where(CP_fishnet["CEid"] == CE["CEid"])
        # Get all features inside the CE
        CE_features = CP_fishnet.iloc[idx]
        # Check if the CE is empty or not
        if CE_features.empty:
            continue
        # If there is not features to dissolve, get rid off
        stats = rs.zonal_stats(CE_features, FAC, stats=['max'])
        CP_fishnet.iloc[idx, columnsCP.index("maxFAC")] = [
            s['max'] for s in stats]
        # Update values
        CE_features = CP_fishnet.iloc[idx]
        # Find neighbors
        CE_features = find_neighbors(CE_features, "CPid")
        # print(CE_features)
        for i, CP in CE_features.iterrows():
            # Take only the CP labeled with dissolve
            # Here I delete the Isolated CP in the border
            if CP["maxFAC"] is None:
                CP_fishnet.at[i, "CPid"] = 0.0
                CP_fishnet.at[i, "Dissolve"] = 0
                CP_fishnet.at[i, "CEid"] = 0
            # Delete the CP less than 400 m2. DEM 20x20
            if CP["maxFAC"] is None and CP["Area"] <= 400:
                CP_fishnet.at[i, "CPid"] = 0.0
                CP_fishnet.at[i, "Dissolve"] = 0
                CP_fishnet.at[i, "CEid"] = 0
            # Check if any of the neighbors has none maxFAC
            # if isinstance(CE_features.loc[CP["NEIGHBORS"], "maxFAC"],list):
            # Here we check if the true value was taken from a list object
            if CE_features.loc[CP["NEIGHBORS"], "maxFAC"].isnull().values.any() and CP["Dissolve"] == 1:
                CP_fishnet.at[i, "CPid"] = 0.0
                CP_fishnet.at[i, "Dissolve"] = 0
                CP_fishnet.at[i, "CEid"] = 0
            # else:
            #     if CE_features.loc[CP["NEIGHBORS"], "maxFAC"] is None and CP["Dissolve"] == 1:
            #         CP_fishnet.at[i, "CPid"] = 0.0
            #         CP_fishnet.at[i, "Dissolve"] = 0
            #         CP_fishnet.at[i, "CEid"] = 0
            # Get the maximum index value of the neigbourhs in the subset
            # idx_max = CE_features["maxFAC"][CP["NEIGHBORS"]].isnull().values.any()

    CP_fishnet = CP_fishnet.dissolve(by="CPid", aggfunc="max")
    # Save file
    # CP_fishnet.index = range(len(CP_fishnet))
    CP_fishnet.index = CP_fishnet.index.rename("ind")
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values
    CP_fishnet.at[:, "Area"] = CP_fishnet.area

    return CP_fishnet, CE_fishnet


def remove_smallCP(CE_fishnet: gpd.GeoDataFrame,
                   CP_fishnet: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    CP_fishnet.index = CP_fishnet["CPid"].values

    for index, CE in CE_fishnet.iterrows():
        # Get the index for all the subbasin features
        idx, = np.where(CP_fishnet["CEid"] == CE["CEid"])
        # Get all features inside the CE
        CE_features = CP_fishnet.iloc[idx]
        CE_features = find_neighbors(CE_features, "CPid")
        for i, CP in CE_features.iterrows():
            # Do not ehck the already labaled CPs
            if CP["CPid"] == 0:
                continue

            # Check if need to dissolve
            if CP["Dissolve"]:
                # Check if there are no neighbors
                if not CP["NEIGHBORS"]:
                    CP_fishnet.at[i, "CPid"] = 0.0
                    CP_fishnet.at[i, "maxFAC"] = None
                    CP_fishnet.at[i, "Dissolve"] = 0
                else:
                    # There are specific cases for dissolving the CP
                    # Here those cases are depicte
                    # Columns in dataframe
                    # columns = CE_features.columns.tolist()
                    # Find all the CP to dissolve
                    # idx_dissolve, = np.where(tuple(CE_features["Dissolve"]==1))
                    # Get the neighbors and the condition
                    neighbors = CE_features.loc[i, "NEIGHBORS"]
                    dissolve = CE_features.loc[i, "KEEP"]
                    # Check if there are neighbors to dissolve also
                    if True in dissolve:
                        # Filter by dissole or not dissolve
                        to_dissolve = list(
                            itertools.compress(neighbors, dissolve))
                        not_dissolve = list(itertools.compress(
                            neighbors, np.logical_not(dissolve)))
                        # 1- There are only neighbors to dissolve:
                        if to_dissolve and not not_dissolve:
                            # idx_max = CE_features.loc[to_dissolve, "maxFAC"].idxmax()
                            idx_max = CE_features.loc[to_dissolve, "Area"].idxmax(
                            )
                            CP_fishnet.at[to_dissolve, "CPid"] = idx_max
                            CP_fishnet.at[to_dissolve, "Dissolve"] = 0
                            CE_features.at[to_dissolve, "Dissolve"] = 0
                            # CE_features.at[to_dissolve, "CPid"] = idx_max
                            # CE_features = find_neighbors(CE_features, "CPid")
                        # CE 545 - interesting
                        # 2- There are both, neighbors to dissolve and not to
                        elif to_dissolve and not_dissolve:
                            # idx_max = CE_features.loc[not_dissolve, "maxFAC"].idxmax()
                            idx_max = CE_features.loc[not_dissolve, "Area"].idxmin(
                            )
                            CE_features.at[to_dissolve, "Dissolve"] = 0
                            CP_fishnet.at[to_dissolve, "CPid"] = idx_max
                            CP_fishnet.at[to_dissolve, "Dissolve"] = 0
                            # CE_features.at[to_dissolve, "CPid"] = idx_max
                            # CE_features = find_neighbors(CE_features, "CPid")
                    else:
                        # Here the CPs with no neighbors to dissolve are processed
                        idx_max = CE_features.loc[CP["NEIGHBORS"], "maxFAC"].idxmax(
                        )
                        CP_fishnet.at[i, "CPid"] = idx_max
                        CP_fishnet.at[i, "CEid"] = CE["CEid"]
                        CP_fishnet.at[i, "Dissolve"] = 0

    # Save file
    CP_fishnet = CP_fishnet.dissolve(by="CPid", aggfunc="max")
    CP_fishnet.index = CP_fishnet.index.rename("index")
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values
    CP_fishnet.at[:, "Area"] = CP_fishnet.area
    return CP_fishnet


def dissolve_pixels(CE_fishnet: gpd.GeoDataFrame,
                    CP_fishnet: gpd.GeoDataFrame,
                    area_th) -> gpd.GeoDataFrame:
    # The ressult from the previous process leads to have multipolygon features
    # We need to  make sure this is going to be well dissolved. 
    # This part drops the CEs where there exist multipolygons in the main dataset
    # Drop the non data values
    CP_fishnet = CP_fishnet.drop(index=0)
    # Explode the values and reassing the index and CP values
    CP_fishnet = CP_fishnet.explode()
    CP_fishnet["Area"] = CP_fishnet.area
    CEarea = CE_fishnet.area.max()
    mask_CP = CP_fishnet["Area"] < area_th*CEarea
    CP_fishnet.loc[mask_CP.values, "Dissolve"] = 1
    CP_fishnet["CPid"] = range(1,len(CP_fishnet)+1)
    CP_fishnet.index = CP_fishnet["CPid"].values
    # Now, identify the leftover CPs that need to be dissolved
    idx_lefts = np.where(CP_fishnet.loc[:,"Dissolve"]==1)
    SubCP_fishnet = CP_fishnet.iloc[idx_lefts]
    CEsDrop = np.unique(SubCP_fishnet["CEid"].values)
    SubCP_fishnet = CP_fishnet.loc[CP_fishnet["CEid"].isin(CEsDrop)]
    # CP_fishnet = CP_fishnet.loc[~CP_fishnet["CEid"].isin(CEsDrop)]
    # Start looping in the CEs that need to be dissolved
    for CE in CEsDrop:
        # Get the index for all the subbasin features
        idx, = np.where(CP_fishnet["CEid"] == CE)
        CE_features = CP_fishnet.iloc[idx]
        # Drop the found values
        indexes = CP_fishnet.index[idx]
        CP_fishnet = CP_fishnet.drop(index=indexes)
        # Replace the index values. Select large values to avoid coinciding with the
        # CPs already storaged in the main dataset
        CP_vals = np.random.randint(99999, 999999, len(CE_features), dtype=int)
        # Compute the features of each CP to find the neighbors
        CE_features.index = CP_vals
        CE_features.at[:, "CPid"] = CP_vals
        CE_features = find_neighbors(CE_features, "CPid")
        columns = CE_features.columns.tolist()
        # Find features with an area less than 1000km2. This works for DEMS upto 90m
        idx_400km, = np.where(CE_features.loc[:, "Area"] <= 9000.0)
        CP_400km_neighs = CE_features.iloc[idx_400km, columns.index(
            "NEIGHBORS")].values
        if len(idx_400km) == 1:
            # CE_features.iloc[idx_400km,columns.index("CPid")] = CP_400km_neighs[0][0]
            CE_features.iloc[idx_400km, columns.index("Dissolve")] = 0
            # Loop to check multi polygons
            flag_neig = True
            count = 0
            # Check if the polygons are dissolved
            while flag_neig:
                # NEIGHBORS
                CE_features.iloc[idx_400km, columns.index(
                    "CPid")] = CP_400km_neighs[0][count]
                dissolve = CE_features.dissolve(by="CPid", aggfunc="max")
                count += 1
                if "MultiPolygon" not in dissolve.geom_type.values:
                    flag_neig = False
                # Check if the counter surpass the list of pixels to be dissolved
                elif count >= len(CP_400km_neighs[0]):
                    flag_neig = False

            if 'NEIGHBORS' in CE_features.columns:
                CE_features = CE_features.drop(columns=["NEIGHBORS", "KEEP"])
            CP_fishnet = pd.concat(
                [CP_fishnet, CE_features.iloc[idx_400km, :]], axis=0)
            # Drop the values
            CE_features = CE_features.drop(
                index=CE_features.iloc[idx_400km].index)
        elif len(idx_400km) > 1:
            for ind in range(len(idx_400km)):
                # CE_features.iloc[idx_400km[ind],columns.index("CPid")] = CP_400km_neighs[ind][0]
                CE_features.iloc[idx_400km[ind], columns.index("Dissolve")] = 0
                # Loop to check multi polygons
                flag_neig = True
                count = 0
                while flag_neig:
                    # NEIGHBORS
                    CE_features.iloc[idx_400km[ind], columns.index(
                        "CPid")] = CP_400km_neighs[ind][count]
                    dissolve = CE_features.dissolve(by="CPid", aggfunc="max")
                    count += 1
                    if "MultiPolygon" not in dissolve.geom_type.values:
                        flag_neig = False
                    # Check if the counter surpass the list of pixels to be dissolved
                    elif count >= len(CP_400km_neighs[ind]):
                        flag_neig = False
                    
            # Add only this CPS
            if 'NEIGHBORS' in CE_features.columns:
                CE_features = CE_features.drop(columns=["NEIGHBORS", "KEEP"])
            CP_fishnet = pd.concat(
                [CP_fishnet, CE_features.iloc[idx_400km[:], :]], axis=0)
            # Drop the values
            CE_features = CE_features.drop(
                index=CE_features.iloc[idx_400km].index)
        CP_fishnet = pd.concat([CP_fishnet, CE_features], axis=0)

    # Dissolve to make sure everything is restarted
    CP_fishnet = CP_fishnet.dissolve(by="CPid", aggfunc="max")
    CP_fishnet.index = CP_fishnet.index.rename("index")
    # Change the values of the index
    CP_fishnet.index = range(1,len(CP_fishnet)+1)
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values
    CP_fishnet = pd.concat([CP_fishnet, CE_features], axis=0)
    if 'NEIGHBORS' in CP_fishnet.columns:
        CP_fishnet = CP_fishnet.drop(columns=["NEIGHBORS", "KEEP"])
    # Update the mask of the already merged values
    CP_fishnet.index = range(1,len(CP_fishnet)+1)
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values
    mask_CP = CP_fishnet.loc[:, "Area"] < area_th*CEarea
    CP_fishnet.loc[mask_CP, "Dissolve"] = 1
    CP_fishnet.loc[np.logical_not(mask_CP), "Dissolve"] = 0
    # Treat the left-over cases
    if CP_fishnet["Dissolve"].any() == 1:
        idx_CES = np.where(CP_fishnet["Dissolve"] == 1)
        CEsDrop = np.unique(CP_fishnet.iloc[idx_CES]["CEid"].values)
        left_overs_CEs = CP_fishnet.loc[CP_fishnet["CEid"].isin(CEsDrop)]
        CP_fishnet = CP_fishnet.loc[~CP_fishnet["CEid"].isin(CEsDrop)]
        # Loop
        for CE in CEsDrop:
            idx, = np.where(left_overs_CEs["CEid"] == CE)
            CE_features = left_overs_CEs.iloc[idx]
            CE_features = find_neighbors(CE_features, "CPid")
            columns = CE_features.columns.tolist()
            # Check the cases.
            unique, counts = np.unique(
                CE_features.loc[:, "Dissolve"].values, return_counts=True)
            # 1- There is only one CP left to merge:
            if counts[0] == 1:
                # Find the CP to dissolve
                idx_dissolve, = np.where(
                    CE_features.loc[:, "Dissolve"].values == 1)
                # Find the neighbor with the maximum FAC
                neig_list = CE_features.iloc[idx_dissolve, columns.index(
                    "NEIGHBORS")].values[0]
                # It is possible to find pixels that have no neighbors.
                # Check that case here
                # if not neig_list:
                #     # Find index of the islated tiny CP
                #     idx_main, = np.where(CP_fishnet.loc[:,"CPid"] == CE_features.iloc[0,columns.index("CPid")]) 
                #     CP_fishnet = CP_fishnet.drop(index=CP_fishnet.index[idx_main])
                #     continue
                # find the index where the 
                idx_FAC = CE_features.loc[neig_list, "maxFAC"].idxmax()
                # Replace the CP values within the CE
                CE_features.iloc[idx_dissolve,
                                 columns.index("CPid")] = idx_FAC.real
                CE_features.iloc[idx_dissolve, columns.index("Dissolve")] = 0
                # Merge it with the main dataset
                if 'NEIGHBORS' in CE_features.columns:
                    CE_features = CE_features.drop(
                        columns=["NEIGHBORS", "KEEP"])
                CP_fishnet = pd.concat([CP_fishnet, CE_features], axis=0)
            else:
                # Sort by dissolve or not
                CE_features = CE_features.sort_values(
                    by='Dissolve', ascending=False)
                # Loop into each CP
                # for idx_CP in range(len(CE_features)):
                for index, CP in CE_features.iterrows():
                    # Create an exception here since at each iteration, the dataframe
                    # is being reduced. Just to make sure it does not crash in run time
                    try:
                        CE_features.loc[index, "Dissolve"] == 1
                    except:
                        # Continue to jump to te next CP
                        continue
                    # Check if this needs to be dissolve
                    if CE_features.loc[index, "Dissolve"] == 1:
                        # Check the neigbors cases also
                        neig_list = CE_features.loc[index, "NEIGHBORS"]
                        # Only one neigh.
                        if len(neig_list) == 1:
                            # Check if this neighboor need to be dissolved also
                            if CE_features.loc[neig_list, "KEEP"].values:
                                # Set the two dissolve values to zero
                                CE_features.loc[index, "Dissolve"] = 0
                                CE_features.loc[neig_list, "Dissolve"] = 0
                                # Now merge the two CPs and drop them
                                CE_features.loc[index, "CPid"] = neig_list[0]
                                CP_fishnet = pd.concat(
                                    [CP_fishnet, CE_features.loc[[index]]], axis=0)
                                CP_fishnet = pd.concat(
                                    [CP_fishnet, CE_features.loc[neig_list]], axis=0)
                                # Drop the values
                                CE_features = CE_features.drop(index=index)
                                CE_features = CE_features.drop(
                                    index=CE_features.loc[neig_list].index)
                    else:
                        CP_fishnet = pd.concat(
                            [CP_fishnet, CE_features], axis=0)
                        continue
    # Drop the unnecesary columns
    if 'NEIGHBORS' in CP_fishnet.columns:
        CP_fishnet = CP_fishnet.drop(columns=["NEIGHBORS", "KEEP"])
    CP_fishnet = CP_fishnet.dissolve(by="CPid", aggfunc="max")
    CP_fishnet.index = CP_fishnet.index.rename("index")
    CP_fishnet.index = range(1,len(CP_fishnet)+1)
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values

    return CP_fishnet


def force_4CP(CE_fishnet: gpd.GeoDataFrame,
              CP_fishnet: gpd.GeoDataFrame,
              area_th: float) -> gpd.GeoDataFrame:
    # Explode the fishnet
    CP_fishnet = CP_fishnet.explode()
    CP_fishnet.index = CP_fishnet["CPid"].values
    CE_area = CE_fishnet.area.max()
    mask_CP = CP_fishnet["Area"] < area_th*CE_area
    CP_fishnet.loc[mask_CP, "Dissolve"] = 1
    for index, CE in CE_fishnet.iterrows():

        # Get the index for all the subbasin features
        idx, = np.where(CP_fishnet["CEid"] == CE["CEid"])
        # Get all features inside the CE
        CE_features = CP_fishnet.iloc[idx]
        # Check if the CE is empty
        if CE_features.empty:
            continue
        # Get the CPid
        while len(CE_features) > 4:
            # Find neighbors
            CE_features = find_neighbors(CE_features, "CPid")
            CE_features.at[:, "Area"] = CE_features.area
            columns = CE_features.columns.tolist()
            # Find the smallest CP
            idx_small, = np.where(
                CE_features.loc[:, "Area"].values == np.amin(CE_features["Area"]))
            # Find the neighbor with the highest flow accumulation
            neighbors = CE_features.iloc[idx_small,
                                         columns.index("NEIGHBORS")].values[0]
            maxFAC = np.amax(CE_features.loc[neighbors, "maxFAC"])
            idx_maxFAC, = np.where(
                CE_features.loc[neighbors, "maxFAC"] == maxFAC)
            # Get the cpid of the replaced value
            idx_replaced = CE_features.iloc[idx_small, columns.index("CPid")]
            # Replace the value in the main dataframe
            CP_fishnet.loc[idx_replaced, "CPid"] = neighbors[idx_maxFAC[0]]
            # Replace the CPid value into the small CP
            CE_features.iloc[idx_small, columns.index(
                "CPid")] = neighbors[idx_maxFAC[0]]
            # Now dissolve all CE_features and rearange the things
            CE_features = CE_features.dissolve(by="CPid", aggfunc="max")
            CE_features.index = CE_features.index.rename("index")
            CE_features["CPid"] = CE_features.index.values

    # Save file
    CP_fishnet = CP_fishnet.dissolve(by="CPid", aggfunc="max")
    CP_fishnet.index = CP_fishnet.index.rename("index")
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values
    CP_fishnet.at[:, "Area"] = CP_fishnet.area
    
    # Given that this is the final dissolving step, here I will check for 
    # duplicate geometries and drop them all if that's the case
    CP_fishnet['geometry_str'] = CP_fishnet['geometry'].apply(lambda x: str(x))
    CP_fishnet = CP_fishnet.explode()
    CP_fishnet['normalized_geometry'] = CP_fishnet['geometry'].apply(lambda geom: str(Polygon(geom.exterior.coords)))
    CP_fishnet = CP_fishnet.dissolve(by='normalized_geometry')
    CP_fishnet.index = CP_fishnet.index.rename("index")
    CP_fishnet.index = range(1,len(CP_fishnet)+1)
    CP_fishnet.loc[:, "CPid"] = CP_fishnet.index.values
    CP_fishnet = CP_fishnet.drop(columns=["geometry_str"])
    # df1 = pd.DataFrame(CP_fishnet.drop(columns='geometry'))
    # indexes_to_skip,processed_indexes = u.drop_duplicated_geometries(CP_fishnet["geometry"])
    return CP_fishnet


# def compute_flow_path(flow_dir: np.ndarray,
#                       flow_acc: np.ndarray,
#                       flow_th: float) -> np.ndarray:
#     # Mask the flow accumulation based on the threshold
#     flow_accu_mask = (flow_acc > flow_th)
#     rows, cols = np.indices(flow_dir.shape)
#     stream_network = np.zeros_like(flow_dir)
#     counter = 1
#     for row in range(flow_dir.shape[0]):
#         for col in range(flow_dir.shape[1]):
#             if flow_accu_mask[row, col]:
#                 next_row = row
#                 next_col = col
#                 # counter = 1
#                 while True:
#                     direction = flow_accu_mask[next_row, next_col]
#                     if direction == 0:
#                         break
#                     next_row += [-1, -1, 0, 1, 1, 1, 0, -1][direction - 1]
#                     next_col += [0, 1, 1, 1, 0, -1, -1, -1][direction - 1]
#                     if stream_network[next_row, next_col] > 0:
#                         break
#                 stream_network[row, col] = counter
#         # counter +=1
#     return stream_network

# Compute the mean altitude within each CE and CP
def mean_altitudes(CE_fishnet: gpd.GeoDataFrame,
                   CP_fishnet: gpd.GeoDataFrame,
                   DEM: str):
    # Add altitude column to each dataset
    CE_fishnet = CE_fishnet.reindex(columns=CE_fishnet.columns.tolist() + ['altitude'])
    CE_fishnet["altitude"] = None
    CP_fishnet = CP_fishnet.reindex(columns=CP_fishnet.columns.tolist() + ['altitude'])
    CP_fishnet["altitude"] = None

    # Compute the zonal statistics
    stats_CE = rs.zonal_stats(CE_fishnet, DEM, stats=['mean'])
    CE_fishnet.loc[:, "altitude"] = [s['mean'] for s in stats_CE]
    stats_CP = rs.zonal_stats(CP_fishnet, DEM, stats=['mean'])
    CP_fishnet.loc[:, "altitude"] = [s['mean'] for s in stats_CP]
    return CP_fishnet, CE_fishnet

# def main_path(flow_dir: np.ndarray,
#               flow_acc: np.ndarray,
#               flow_th: float) -> np.ndarray:
#     # Create a mask to extract only the cells with flow accumulation greater than a certain threshold
#     flow_accumulation_mask = (flow_acc > flow_th)

#     # Find the outlet cell of the catchment (i.e., the cell with the minimum flow accumulation)
#     outlet_row, outlet_col = np.unravel_index(
#         np.argmin(flow_acc), flow_acc.shape)

#     # Create an empty list to store the cells in the main stream
#     main_stream_cells = []

#     # Trace the main stream from the outlet cell to the start of the main stream
#     next_row, next_col = outlet_row, outlet_col
#     while True:
#         main_stream_cells.append((next_row, next_col))
#         direction = flow_dir[next_row, next_col]
#         if direction == 0:  # Check if the current cell is a sink cell and exit the loop if it is
#             break
#         # Calculate the row and column indices of the next cell in the main stream
#         next_row += [-1, -1, 0, 1, 1, 1, 0, -1][direction - 1]
#         next_col += [0, 1, 1, 1, 0, -1, -1, -1][direction - 1]
#         # Check if the next row or column index is out of bounds and exit the loop if it is
#         if next_row < 0 or next_row >= flow_dir.shape[0] or next_col < 0 or next_col >= flow_dir.shape[1]:
#             break
#         # Check if the current cell has more than one upstream cell and exit the loop if it does
#         upstream_count = 0
#         for i, j in [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]:
#             row = next_row + i
#             col = next_col + j
#             if row >= 0 and row < flow_dir.shape[0] and col >= 0 and col < flow_dir.shape[1]:
#                 if flow_dir[row, col] == (8 - direction):
#                     upstream_count += 1
#                     if upstream_count > 1:
#                         break
#         if upstream_count > 1:
#             break

#     # Create a new raster to represent the main stream cells
#     main_stream_raster = np.zeros_like(flow_dir)
#     for cell in main_stream_cells:
#         main_stream_raster[cell[0], cell[1]] = 1


def routing_table(CP_fishnet: gpd.GeoDataFrame,
                  CE_fishnet: gpd.GeoDataFrame,
                  FAC: str,
                  CP_array: np.ndarray,
                  CE_array: np.ndarray) -> tuple:

    # Create dataframe to store the routing data
    routing = pd.DataFrame(columns=["CPid", "inCPid", "inCPid2", "outlet_row",
                                    "outlet_col", "inlet_row", "inlet_col"],
                           index=CP_fishnet.index.values)
    routing["CPid"] = CP_fishnet["CPid"]
    routing.index = CP_fishnet.index.values
    CP_fishnet = pd.concat([CP_fishnet,
                            CP_fishnet["geometry"].bounds], axis=1)
    CE_fishnet = pd.concat([CE_fishnet,
                            CE_fishnet["geometry"].bounds], axis=1)
    # Get the FAC array
    FAC_dataset = gdal.Open(FAC, gdal.GA_ReadOnly)
    band = FAC_dataset.GetRasterBand(1)
    FAC_array = band.ReadAsArray()
    FAC_array[FAC_array < 0] = 0
    # Get the DIR array
    CP_fishnet = convert_coords_to_index(CP_fishnet, FAC_dataset)
    CE_fishnet = convert_coords_to_index(CE_fishnet, FAC_dataset)
    # Get columns into each dataset
    CP_columns = CP_fishnet.columns.tolist()
    CE_columns = CE_fishnet.columns.tolist()
    df1 = pd.DataFrame(CP_fishnet.drop(columns='geometry'))
    df2 = pd.DataFrame(CE_fishnet.drop(columns='geometry'))
    # Loop into each CE
    
    for index, feat in CP_fishnet.iterrows():
        # Find the rows and cols where the CP value is stored
        # rows, cols = np.where(CP_array == feat["CPid"])
        # CP_fishnet.at[index,"row_min"] = np.amin(rows)
        # CP_fishnet.at[index,"row_max"] = np.amax(rows)
        # CP_fishnet.at[index,"col_min"] = np.amin(cols)
        # CP_fishnet.at[index,"col_max"] = np.amax(cols)
        # CP = CP_array[np.amin(rows)-1:np.amax(rows)+2,
        #               np.amin(cols)-1:np.amax(cols)+2]
        # subFAC = FAC_array[np.amin(rows)-1:np.amax(rows)+2,
        #                    np.amin(cols)-1:np.amax(cols)+2]
        # Test the Extent of the CE to check wheter the index need to be 
        # modified or not
        # Find the index which correspond to the CEid in the main dataframe
        idx_CE, = np.where(CE_fishnet["CEid"] == feat["CEid"])
        # Slice the CE array using the corners into the main dataset
        CEmin = CE_fishnet.iloc[idx_CE[0],CE_columns.index("row_max")]
        CE = CE_array[CE_fishnet.iloc[idx_CE[0],CE_columns.index("row_min")]:CE_fishnet.iloc[idx_CE[0],CE_columns.index("row_max")],
                      CE_fishnet.iloc[idx_CE[0],CE_columns.index("col_min")]:CE_fishnet.iloc[idx_CE[0],CE_columns.index("col_max")]]
        # substract the CE value to the right border to check if it changes
        correct = 0
        if np.amax(np.abs(feat["CEid"]-CE[:,-1])):
            correct = 1
        # while len(CE_uniques) > 1:
        #     # Get the borders
        #     upper_border = CE[0,:]
        #     lower_border = CE[-1,:]
        #     left_border = CE[:,0]
        #     right_border = CE[:,-1]
        #     # Analyze the borders
            
        #     CE = CE_array[CE_fishnet.iloc[idx_CE[0],CE_columns.index("row_min")]:CE_fishnet.iloc[idx_CE[0],CE_columns.index("row_max")],
        #               CE_fishnet.iloc[idx_CE[0],CE_columns.index("col_min")]:CE_fishnet.iloc[idx_CE[0],CE_columns.index("col_max")]]
        #     # Get the unique values in the CE to check the boundaires
        #     CE_uniques = np.unique(CE)
        CP = CP_array[feat["row_min"]-1:feat["row_max"]+1,
                           feat["col_min"]-1-correct:feat["col_max"]+1-correct]
        subFAC = FAC_array[feat["row_min"]-1:feat["row_max"]+1,
                           feat["col_min"]-1-correct:feat["col_max"]+1-correct]
        # Mask the FAC based on this CP
        mask_CP = (CP == feat['CPid']).astype('uint8')
        # Apply mask
        # mask_subFAC = subFAC*mask_CP
        # Get the outlet indexes for the CP
        outlet_row, outlet_col = np.unravel_index(
            np.argmax(subFAC*mask_CP), subFAC.shape)
        if isinstance(outlet_row, np.ndarray):
            sys.exit("There is more than one outlet point")

        # Create mask for subFAC
        mask_FAC = np.zeros(subFAC.shape).astype("uint8")
        mask_FAC[outlet_row - 1:outlet_row + 2,
                 outlet_col-1:outlet_col+2] = 1
        # masked_FAC = subFAC*mask_FAC
        # Get the FAC values on the mask
        # Find location of next pixel into which outlet flows
        inlet_row, inlet_col = np.unravel_index(
            np.argmax(subFAC*mask_FAC), subFAC.shape)
        # Add the CP where it discharges
        routing.at[index, "inCPid"] = CP[inlet_row, inlet_col]
        routing.at[index, "inCPid2"] = CP[inlet_row, inlet_col]
        # Add the coordinates into the dataframe
        routing.at[index, "outlet_col"] = outlet_col-1
        routing.at[index, "outlet_row"] = outlet_row-1
        routing.at[index, "inlet_row"] = inlet_row-1
        routing.at[index, "inlet_col"] = inlet_col-1
    # Create the rouring table here
    rtable = pd.DataFrame(columns=["oldCPid", "newCPid", "upstreamCPs","oldupstreams"],
                          index=range(1,len(CP_fishnet)+1))
    rtable.index = CP_fishnet.index.values
    routing["diff"] = routing["CPid"] - routing["inCPid"]
    # Find where the difference is zero
    # This will help us to find the outlet CP
    idx_outlet = routing.index[routing["diff"] == 0].values
    # Add this value in the rtable as the first value
    rtable.loc[[0], "oldCPid"] = routing.loc[idx_outlet, "CPid"].values[0]
    rtable.loc[[0], "newCPid"] = 1
    # Get column list
    columns = rtable.columns.tolist()
    # Set to zero the already tracked CP
    routing.loc[idx_outlet, "inCPid"] = -99999
    new_id_counter = 1
    for i, _ in routing.iterrows():
        # Find the upstream CPs
        index_routing = routing["inCPid"] == rtable.at[i, "oldCPid"]
        idx_outlet = routing.index[index_routing]
        # print(idx_outlet)
        # print(rtable.at[i,"oldCPid"],i)
        if not idx_outlet.empty:
            # Find the upstream CPs
            upstreams_cps = routing.loc[idx_outlet, "CPid"].values
            # Rename the upstream CPs
            upstreams_cps_newid = list(
                range(new_id_counter+1, new_id_counter+len(idx_outlet)+1))
            # Append the CPs vertically. +1 to step out the current index
            rtable.iloc[new_id_counter:new_id_counter +
                        len(idx_outlet), columns.index("newCPid")] = upstreams_cps_newid
            rtable.iloc[new_id_counter:new_id_counter +
                        len(idx_outlet), columns.index("oldCPid")] = upstreams_cps
            rtable.iloc[i, columns.index("upstreamCPs")] = upstreams_cps_newid
            rtable.iloc[i, columns.index("oldupstreams")] = upstreams_cps
            new_id_counter += len(idx_outlet)
            routing.loc[idx_outlet, "inCPid"] = -99999
        else:
            a = 1
            # routing.loc[idx_outlet, "inCPid"] = -99999
            pass
        # Set to nan the already tracked CP
        
    # *There are probably cases where a given CP drains into a non existence CP.
    # *So, here we make sure that we drop all the CP where this happens into the main data frame.
    # *This is because the CPs in the border can be so tiny that they do not account for the
    # *area threshold that we defined.
    idx_zero_inCP, = np.where(routing["inCPid"] == 0)
    # Find the index in the main dataset
    index_drop = CP_fishnet.index[idx_zero_inCP]
    # Drop this value from the main dataframe
    CP_fishnet = CP_fishnet.drop(index=index_drop)
    # Drop nan values in the rtable
    idx_nan_inCP, = np.where(rtable["oldCPid"].isnull())
    index_drop = rtable.index[idx_nan_inCP]
    rtable = rtable.drop(index=index_drop)
    CP_fishnet = CP_fishnet.drop(columns=["minx", "miny",
                                          "maxx", "maxy",
                                          "col_min", "row_min",
                                          "col_max", "row_max"])
    return rtable, CP_fishnet


def get_downstream_CP(rtable: pd.DataFrame) -> pd.DataFrame:
    # Create an empty table to store the values
    downstreamCPs = pd.DataFrame(columns=["downstreamCPs"],
                                 index=rtable.index)
    # Convert lists into an array.
    # Get the lenght of each list to get the maximum value
    lists_len = [len(i)
                 for i in rtable["upstreamCPs"].values if isinstance(i, list)]
    # Create a zero array to store the values
    list_upstream_array = np.zeros([len(rtable), max(lists_len)]).astype("uint16")
    # Fill up the array using the lists in the main dataframe
    for i in range(len(rtable)):
        if isinstance(rtable.loc[i, "upstreamCPs"], list):
            list_up = rtable.loc[i, "upstreamCPs"]
            list_upstream_array[i, 0:len(list_up)] = list_up
        rtable.loc[i, "upstreamCPs"] = list_upstream_array[i,:].tolist()
    # Now identify the Downstream CPs
    for i, table in rtable.iterrows():
        # Find the index of the CP in wwhich the current CP drains
        idx_down, _ = np.where(list_upstream_array == table["newCPid"])
        # Check if the list is empty
        if len(idx_down) == 0:
            downstreamCPs.loc[i, "downstreamCPs"] = 0
        else:
            downstreamCPs.loc[i,
                              "downstreamCPs"] = rtable.loc[idx_down[0], "newCPid"]
    # Concat to the rtable
    rtable = pd.concat([rtable, downstreamCPs], axis=1)
    return rtable


def outlet_routes(rtable: pd.DataFrame) -> pd.DataFrame:

    # Create the array to store the lists
    allroute_lists = pd.DataFrame(columns=["outletRoutes"],
                                  index=rtable.index)
    # Start looping the array
    columns_rtable = rtable.columns.tolist()
    for i, _ in rtable.iterrows():
        route_list = []
        # Set the upstream CP to
        up = rtable.loc[i, "newCPid"]
        # Set the number of the upstream CPs to 1
        nu = up
        while up != 1:
            # append Nth CP to list
            route_list.append(up)
            # go down from Nth position until end
            up = rtable.loc[up-1,"downstreamCPs"]
            nu -= 1
        route_list.append(1)
        allroute_lists.loc[i, "outletRoutes"] = route_list
    # Convert it into a numpy array
    lists_len = [
        len(i) for i in allroute_lists["outletRoutes"].values if isinstance(i, list)]
    # Create a zero array to store the values
    allroute_lists_array = np.zeros([len(allroute_lists), max(lists_len)])
    # Fill up the array using the lists in the main dataframe
    for i in range(len(allroute_lists)):
        if isinstance(allroute_lists.loc[i, "outletRoutes"], list):
            list_up = allroute_lists.loc[i, "outletRoutes"]
            allroute_lists_array[i, 0:len(list_up)] = list_up
    return allroute_lists_array


def cumulative_areas(CP_fishnet: gpd.GeoDataFrame,
                     CE_fishnet: gpd.GeoDataFrame,
                     outlet_routes: np.ndarray) -> pd.DataFrame:
    # Update areas of the CP and get the CE area
    CE_area = CE_fishnet.area[1]
    CP_fishnet["Area"] = CP_fishnet.area
    # Get the percentage of that area
    CP_fishnet["pctSurface"] = (CP_fishnet["Area"]/CE_area)*100
    # Cumulative areas
    CP_fishnet["cumulPctSurf"] = 0.0
    upstreamCPs = []
    for i in range(len(outlet_routes)):
        if i == 0:
            upstreamCPs.append(0)
            continue
        # Create a copy of the main dataframe
        temp_df = outlet_routes.copy()
        # find the row of the current CP
        idx_row , _ = np.where(temp_df == i)
        # Mask the downstreams values
        temp_df = temp_df[idx_row,:] 
        mask_df = temp_df < i
        # Drop the downstream values
        temp_df = temp_df[~mask_df]
        # Get the unique values
        temp_df = np.unique(temp_df).astype("uint16")
        party = CP_fishnet.loc[temp_df,"pctSurface"]
        sum = CP_fishnet.loc[temp_df,"pctSurface"].sum()
        upstreamCPs.append(temp_df.tolist())
        CP_fishnet.loc[i,"cumulPctSurf"] = CP_fishnet.loc[temp_df,"pctSurface"].sum()
    return CP_fishnet, upstreamCPs

def renumber_fishnets(CP_fishnet: gpd.GeoDataFrame,
                     CE_fishnet: gpd.GeoDataFrame,
                     rtable: pd.DataFrame) -> gpd.GeoDataFrame:
    # Renumbering the CPs
    CP_fishnet["newCPid"] = 0
    CP_fishnet["newCEid"] = 0
    CE_fishnet["newCEid"] = 0
    CE_track_list = []
    # get the columns to use them as indexers
    columns_rtable = rtable.columns.tolist()
    columns_CPfishnet = CP_fishnet.columns.tolist()
    columns_CEfishnet = CE_fishnet.columns.tolist()
    
    # Iterate over the dataframe to rename CP fishnet
    for i in range(len(CP_fishnet)):
        # Find the index of the old CPid in the main dataframe
        idx_old, = np.where(CP_fishnet["CPid"].values == rtable.iloc[i,columns_rtable.index("oldCPid")])
        # Replace the value with the new CPid value
        CP_fishnet.iloc[idx_old,columns_CPfishnet.index("newCPid")] = int(rtable.iloc[i,columns_rtable.index("newCPid")])
    # Change the index in the main dataframe
    CP_fishnet.index = CP_fishnet["newCPid"].values
    # Sort values
    CP_fishnet = CP_fishnet.sort_values(by=["newCPid"])
    # Renumbering the CEs. This is possible since the values are
    # already sorted in the main dataframe based on the new CPids
    CE_id = 0
    for i,_ in CP_fishnet.iterrows():
        # Check if the CE has already been tracked
        if CP_fishnet.loc[i,"CEid"] in CE_track_list:
            continue
        # Track the CE in the CP fishnet
        CE_track_list.append(CP_fishnet.loc[i,"CEid"])
        # Find the value in the dataset
        idx_new, = np.where(CE_fishnet["CEid"] == CE_track_list[CE_id])
        # Add the value in the column
        CE_fishnet.iloc[idx_new,columns_CEfishnet.index("newCEid")] = CE_id+1
        # Find the value in the CPfishnet dataset
        idx_new2, = np.where(CP_fishnet["CEid"] == CE_track_list[CE_id])
        CP_fishnet.iloc[idx_new2,columns_CPfishnet.index("newCEid")] = CE_id+1
        CE_id += 1
    CE_fishnet = CE_fishnet.sort_values(by=["newCEid"])
    CE_fishnet.index = CE_fishnet["newCEid"].values
    # Find where the CE is zero
    idx_drop_CE, = np.where(CE_fishnet["newCEid"] == 0)
    # Find index
    indexes_drop = CE_fishnet.index[idx_drop_CE]
    # Drop by  index
    CE_fishnet = CE_fishnet.drop(index=indexes_drop)
    return CP_fishnet, CE_fishnet


def get_atitudes():
    pass

# Add the data to the routing table

# subDIR = subDIR*mask_FAC
# Get the stream network
# stream = compute_flow_path(subDIR,subFAC,np.nanmean(subFAC))

# outlet_row, outlet_col = np.where(subFAC == np.amax(subFAC))
# if isinstance(outlet_row,np.ndarray) > 1:
#     print("AAA")
#     break
# else:
#     print(feat['CPid'],outlet_row, outlet_col)
#     print(subFAC[outlet_row, outlet_col])
# print(np.unique(CP))
# print(CP)
# print(subDIR)
# print(stream.astype("uint8"))
# print(subFAC)
# print(mask_FAC)
#     # Get the index for all the subbasin features
#     idx, = np.where(CP_fishnet["CEid"] == CE["CEid"])
#     # Get all features inside the CE
#     CE_features = CP_fishnet.iloc[idx]
#     # Check if the CE is empty
#     if CE_features.empty:
#         continue

# Get the ids for each subbasin
# id_sub = np.unique(CP_fishnet["OBJECTID"][mask_CP])
# id_CE = np.unique(CP_fishnet["id"][mask_CP])
# CP_fishnet = CP_fishnet.dissolve(by='CPid', aggfunc='first')
# Loop into each subbasin
# for CE in id_CE:
#     # Get the index for all the subbasin features
#     idx, = np.where(CP_fishnet["id"] == 527)
#     CE_features = CP_fishnet.iloc[idx]
#     for i in range(len(CE_features)):
#         # print(CE_features["Dissolve"])
#         if CE_features["Dissolve"].iloc[i]==1:
#             print(CE_features["geometry"].touches(CE_features["geometry"],align=True))
#     # print(CE_features["Dissolve"].iloc[1])
#     break
# for
# Mask nan
# mask_NaN = CP_fishnet[mask_CP]
# print(CP_fishnet[mask_CP])
# print(CP_area[mask_CP])
# print(len(CP_fishnet))
# print(len(CP_fishnet.explode()))
# mask = CP_fishnet['CPid']==759
# print(CP_fishnet[mask])
# return
