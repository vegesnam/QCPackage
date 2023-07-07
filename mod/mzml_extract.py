import pymzml
import statistics
import pandas as pd
import numpy as np
import os
import threading
import logging
import xlsxwriter
import plotly.express as px
import plotly
import plotly.graph_objects as go
import plotly.offline as offline
from jinja2 import Environment, FileSystemLoader
from scipy.stats import shapiro

from mod.general_functions import cv, cv_status, check_threshold, groupname, label_outlier, get_idfree_sample_qc_status, only_outlier_status

#-------------------------------------------------------------------------- FUNCTIONS ---------------------------------------------------------------------------

def get_mzml_list(mzml_dir):

    mzml_list = os.listdir(mzml_dir)

    final_mzml_list = []

    logging.info(f"Getting list of mzML files under provided directory path : {mzml_dir}")
    for mzml_file in mzml_list:
        if not mzml_file.endswith(".mzML"):
            logging.info(f"{mzml_file} is not a mzML file and will not be used for data extraction")
            continue
        full_filename = f"{mzml_dir}/{mzml_file}"
        final_mzml_list.append(full_filename)

    logging.info(f"{len(final_mzml_list)} mzML files have been found under {mzml_dir}")

    return final_mzml_list

def mzml_extract(mzml_path, mzml_data):

    logging.info(f"Extracting file: {mzml_path}")

    basepeak_intensity_list = []
    ms1_spectra = 0
    ms2_spectra = 0
    ms1_tic = 0
    ms2_tic = 0

    msrun = pymzml.run.Reader(mzml_path)

    for spectrum in msrun:

        #getting basepeak intensity
        basepeak_intensity_value = spectrum['base peak intensity']
        basepeak_intensity_list.append(basepeak_intensity_value)

        #getting spectra count + tic (check the for the exception in tic parser - why??)
        if spectrum['ms level'] == 1:
            ms1_spectra += 1
            ms1_tic += spectrum['total ion current']

        if spectrum['ms level'] == 2:
            ms2_spectra += 1
            ms2_tic += spectrum['total ion current']

    data_dict = {"Filename": os.path.split(mzml_path)[1], "MS1 TIC": ms1_tic, "MS2 TIC": ms2_tic, "MS1 Spectra": ms1_spectra, "MS2 Spectra": ms2_spectra, "MS2/MS1 Spectra": (ms2_spectra/ms1_spectra),"Max Basepeak Intensity": max(basepeak_intensity_list)}
    #print(f"File: {mzml_path}, data {data_dict}")

    mzml_data.append(data_dict)

    return mzml_data

def get_mzml_info_dataframe(mzml_list):

    mzml_data = []
    threads = []

    for filename in mzml_list:

        job = threading.Thread(target=mzml_extract, args=(filename, mzml_data))
        threads.append(job)
        job.start()

    #Finish all threads
    for job in threads:
        job.join()

    mzml_dataframe = pd.DataFrame(mzml_data)

    return mzml_dataframe

def apply_idfree_thresholds(mzml_df, mzml_threshold_dict):

    if mzml_threshold_dict['MS1 TIC Threshold']:
        mzml_df[f"MS1TIC QC Threshold = {mzml_threshold_dict['MS1 TIC Threshold']}"] = mzml_df['MS1 TIC'].apply(check_threshold, args=[mzml_threshold_dict['MS1 TIC Threshold'],])

    if mzml_threshold_dict['MS2 TIC Threshold']:
        mzml_df[f"MS2TIC QC Threshold = {mzml_threshold_dict['MS2 TIC Threshold']}"] = mzml_df['MS2 TIC'].apply(check_threshold, args=[mzml_threshold_dict['MS2 TIC Threshold'],])

    if mzml_threshold_dict['MS1 Spectra Threshold']:
        mzml_df[f"MS1Spectra QC Threshold = {mzml_threshold_dict['MS1 Spectra Threshold']}"] = mzml_df['MS1 Spectra'].apply(check_threshold, args=[mzml_threshold_dict['MS1 Spectra Threshold'],])

    if mzml_threshold_dict['MS2 Spectra Threshold']:
        mzml_df[f"MS2Spectra QC Threshold = {mzml_threshold_dict['MS2 Spectra Threshold']}"] = mzml_df['MS2 Spectra'].apply(check_threshold, args=[mzml_threshold_dict['MS2 Spectra Threshold'],])

    if mzml_threshold_dict['Max Basepeak Intensity Threshold']:
        mzml_df[f"Max Basepeak Intensity QC Threshold = {mzml_threshold_dict['Max Basepeak Intensity Threshold']}"] = mzml_df['Max Basepeak Intensity'].apply(check_threshold, args=[mzml_threshold_dict['Max Basepeak Intensity Threshold'],])

    return mzml_df

def check_normality(data):

    stat, p_value = shapiro(data)
    alpha = 0.05

    if p_value > alpha:
        return "z-score"
    else:
        return "iqr"

def zscore_outliers(df, colname, zscore_threshold = 2):

    #calculating mean and standard deviation of the data
    mean = df[colname].mean()
    std = df[colname].std()

    #getting outliers based on z-score outlier
    outliers = []

    for value in df[colname].tolist():
        zscore = abs((value-mean)/std)
        if zscore > zscore_threshold:
            outliers.append(value)

    df[f"{colname} Outliers"] = df[colname].apply(label_outlier, args=[outliers, ])

    return (df, len(outliers))

def iqr_outliers(df, colname):

    #calculating quantiles
    q1=df[colname].quantile(0.25)
    q3=df[colname].quantile(0.75)

    #iqr
    IQR=q3-q1

    #can set it to 1.5 for more sensitivity
    outliers = df[((df[colname]<(q1-3*IQR)) | (df[colname]>(q3+3*IQR)))][colname].tolist()

    df[f"{colname} Outliers"] = df[colname].apply(label_outlier, args=[outliers, ])

    return (df, len(outliers))

def outlier_detection(mzml_df, zscore_threshold = 2):

    cols = ['MS1 TIC', 'MS2 TIC', 'MS2/MS1 Spectra', 'Max Basepeak Intensity']

    for colname in cols:

        #check normality and decide outlier method
        logging.info(f"Checking outliers for {colname}")

        if check_normality(mzml_df[colname].tolist()) == "z-score":

            logging.info(f"{colname} values are normally distributed, z-score outlier detection will be used")
            logging.info(f"Z-score Threshold has been set to {zscore_threshold}")

            mzml_df, num_outliers = zscore_outliers(mzml_df, colname, zscore_threshold)

            if num_outliers == 0:
                logging.info(f"No outliers found for {colname}")
            else:
                logging.info(f"{num_outliers} outliers were found for {colname}")

        elif check_normality(mzml_df[colname].tolist()) == "iqr":

            logging.info(f"{colname} values are not normally distributed, iqr outlier detection will be used")
            mzml_df, num_outliers = iqr_outliers(mzml_df, colname)

            if num_outliers == 0:
                logging.info(f"No outliers found for {colname}")
            else:
                logging.info(f"{num_outliers} outliers were found for {colname}")

    return mzml_df

def calculate_tic_cv(mzml_df, groups, tic_cv_threshold):

    tic_cv = mzml_df[['Filename','MS1 TIC','MS2 TIC']]

    group_cv = {}

    for group in groups:
        group_subset = tic_cv[tic_cv['Filename'].isin(groups[group])]
        ms1tic_cv = round(cv(group_subset['MS1 TIC'].tolist()),2)
        ms2tic_cv = round(cv(group_subset['MS2 TIC'].tolist()),2)
        group_cv[group] = {"MS1 TIC CV%": ms1tic_cv, "MS2 TIC CV%": ms2tic_cv}

    tic_cv = pd.DataFrame.from_dict(group_cv, orient="index")
    tic_cv.index = tic_cv.index.set_names(['Group'])
    tic_cv.reset_index(drop=False, inplace=True)

    tic_cv[f'MS1 TIC CV% Threshold = {int(tic_cv_threshold)}'] = tic_cv['MS1 TIC CV%'].apply(cv_status, args=[tic_cv_threshold,])
    tic_cv[f'MS2 TIC CV% Threshold = {int(tic_cv_threshold)}'] = tic_cv['MS2 TIC CV%'].apply(cv_status, args=[tic_cv_threshold,])

    return tic_cv

def get_sample_qc(mzml_df, mzml_threshold_dict):

    if mzml_threshold_dict['MS1 TIC Threshold']:
        mzml_df['MS1 TIC Sample QC Status'] = mzml_df[['MS1 TIC Outliers',f"MS1TIC QC Threshold = {mzml_threshold_dict['MS1 TIC Threshold']}"]].apply(get_idfree_sample_qc_status, axis=1)
    else:
        mzml_df['MS1 TIC Sample QC Status'] =  mzml_df['MS1 TIC Outliers'].apply(only_outlier_status)

    if mzml_threshold_dict['MS2 TIC Threshold']:
        mzml_df['MS2 TIC Sample QC Status'] = mzml_df[['MS2 TIC Outliers',f"MS2TIC QC Threshold = {mzml_threshold_dict['MS2 TIC Threshold']}"]].apply(get_idfree_sample_qc_status, axis=1)
    else:
        mzml_df['MS2 TIC Sample QC Status'] = mzml_df['MS2 TIC Outliers'].apply(only_outlier_status)

    if mzml_threshold_dict['MS1 Spectra Threshold']:
        mzml_df['MS1 Spectra QC Status'] = mzml_df[['MS2/MS1 Spectra Outliers',f"MS1Spectra QC Threshold = {mzml_threshold_dict['MS1 Spectra Threshold']}"]].apply(get_idfree_sample_qc_status, axis=1)
    else:
        mzml_df['MS1 Spectra QC Status'] = mzml_df['MS2/MS1 Spectra Outliers'].apply(only_outlier_status)

    if mzml_threshold_dict['MS2 Spectra Threshold']:
        mzml_df['MS2 Spectra QC Status'] = mzml_df[['MS2/MS1 Spectra Outliers',f"MS2Spectra QC Threshold = {mzml_threshold_dict['MS2 Spectra Threshold']}"]].apply(get_idfree_sample_qc_status, axis=1)
    else:
        mzml_df['MS2 Spectra QC Status'] = mzml_df['MS2/MS1 Spectra Outliers'].apply(only_outlier_status)

    if mzml_threshold_dict['Max Basepeak Intensity Threshold']:
        mzml_df['Max Basepeak Intensity QC Status'] = mzml_df[['Max Basepeak Intensity Outliers', f"Max Basepeak Intensity QC Threshold = {mzml_threshold_dict['Max Basepeak Intensity Threshold']}"]].apply(get_idfree_sample_qc_status, axis=1)
    else:
        mzml_df['Max Basepeak Intensity QC Status'] = mzml_df['Max Basepeak Intensity Outliers'].apply(only_outlier_status)

    mzml_df = mzml_df[['Filename', 'MS1 TIC Sample QC Status', 'MS2 TIC Sample QC Status', 'MS1 Spectra QC Status', 'MS2 Spectra QC Status', 'Max Basepeak Intensity QC Status']]

    return mzml_df

def get_idfree_grouped_df(mzml_sample_df, tic_cv, tic_cv_threshold, groups):

    tic_group_df = tic_cv[['Group',f'MS1 TIC CV% Threshold = {int(tic_cv_threshold)}', f'MS2 TIC CV% Threshold = {int(tic_cv_threshold)}']]
    idfree_status_params = ['MS1 TIC Sample QC Status', 'MS2 TIC Sample QC Status', 'MS1 Spectra QC Status', 'MS2 Spectra QC Status', 'Max Basepeak Intensity QC Status']
    mzml_sample_df['Group'] = mzml_sample_df['Filename'].apply(groupname, args=[groups, ])

    group_status_dict = {}

    for group in list(set(mzml_sample_df['Group'].tolist())):
        group_subset = mzml_sample_df[mzml_sample_df['Group'] == group]
        col_dict = {}

        for colname in ['MS1 TIC Sample QC Status', 'MS2 TIC Sample QC Status', 'MS1 Spectra QC Status', 'MS2 Spectra QC Status', 'Max Basepeak Intensity QC Status']:
            group_colname = colname.replace("Sample", "Group")
            if not list(set(group_subset[colname].tolist())) == "PASS":
                col_dict[group_colname] = "FAIL"
            else:
                col_dict[group_colname] = "PASS"

        group_status_dict[group] = col_dict

    grouped_df = pd.DataFrame.from_dict(group_status_dict, orient="columns")
    grouped_df = grouped_df.T
    grouped_df.reset_index(drop=False, inplace=True)
    grouped_df.rename(columns={'index':'Group'}, inplace=True)

    grouped_df = pd.merge(grouped_df, tic_group_df, on='Group')

    return grouped_df

#------------------------------------------------------------------------ PLOT FUNCTIONS ----------------------------------------------------------------------------

def tic_plots(mzml_df, tic_cv, ms1_tic_threshold, ms2_tic_threshold, tic_cv_threshold, groupwise_comparison):

    df = mzml_df[['Filename','MS1 TIC','MS2 TIC']]

    df = df.melt(id_vars=["Filename"],
        var_name="Label",
        value_name="TIC")

    tic_line = px.line(df, x='Filename', y="TIC", title="Total Ion Current", color="Label", line_shape="spline")
    tic_line.update_xaxes(tickfont_size=8)
    tic_line.update_layout(
            margin=dict(l=20, r=20, t=20, b=20)
    )

    if ms1_tic_threshold:
        tic_line.add_hline(y=ms1_tic_threshold, line_dash="dot", annotation_text=f"MS1 TIC Threshold = {ms1_tic_threshold}")

    if ms2_tic_threshold:
        tic_line.add_hline(y=ms2_tic_threshold, line_dash="dot", annotation_text=f"MS2 TIC Threshold = {ms2_tic_threshold}")

    tic_plot = plotly.io.to_html(tic_line, include_plotlyjs=False, full_html=False)
    #tic_plot = offline.plot(tic_line, output_type='div', include_plotlyjs=False)

    tic_report_params = {'total_ion_current': True,
                    'tic_plot': tic_plot,
                    'tic_ms_plot_description': 'MS1 and MS2 Total Ion Current Values extracted from given mzML files'}

    if not list(set(mzml_df['MS1 TIC Outliers'].tolist())) == [0]:
        ms1_outliers = mzml_df[mzml_df['MS1 TIC Outliers'] == 1]['Filename'].tolist()
        ms1_outliers_filenames = ", ".join(ms1_outliers)
        tic_report_params['tic_ms1_outlier_description'] = f"{mzml_df['MS1 TIC Outliers'].tolist().count(1)} outliers were found. The following files have been detected as outliers: {ms1_outliers_filenames}"

        tic_ms1_outlier = px.scatter(mzml_df, x='Filename', y='MS1 TIC', color='MS1 TIC Outliers')
        tic_ms1_outlier.update_xaxes(tickfont_size=8)
        tic_ms1_outlier.update_layout(
                margin=dict(l=20, r=20, t=20, b=20)
        )
        tic_ms1_outlier_plot = plotly.io.to_html(tic_ms1_outlier, include_plotlyjs=False, full_html=False)

        tic_report_params['tic_ms1_outlier_plot'] = tic_ms1_outlier_plot

    if not list(set(mzml_df['MS2 TIC Outliers'].tolist())) == [0]:
        ms2_outliers = mzml_df[mzml_df['MS2 TIC Outliers'] == 1]['Filename'].tolist()
        ms2_outliers_filenames = ", ".join(ms2_outliers)
        tic_report_params['tic_ms2_outlier_description'] = f"{mzml_df['MS2 TIC Outliers'].tolist().count(1)} outliers were found. The following files have been detected as outliers: {ms2_outliers_filenames}"

        tic_ms2_outlier = px.scatter(mzml_df, x='Filename', y='MS2 TIC', color='MS2 TIC Outliers')
        tic_ms2_outlier.update_xaxes(tickfont_size=8)
        tic_ms2_outlier.update_layout(
                margin=dict(l=20, r=20, t=20, b=20)
        )
        tic_ms2_outlier_plot = plotly.io.to_html(tic_ms2_outlier, include_plotlyjs=False, full_html=False)

        tic_report_params['tic_ms2_outlier_plot'] = tic_ms2_outlier_plot

    if groupwise_comparison:
        tic_report_params['tic_ms_cv_description'] = "CV is calculated across samples in each given group"

        ms1tic_bar = px.bar(tic_cv, x='Group', y="MS1 TIC CV%", title="MS1 Total Ion Current", color="Group")
        ms1tic_bar.update_xaxes(tickfont_size=8)
        ms1tic_bar.add_hline(y=tic_cv_threshold, line_dash="dot", annotation_text=f"TIC CV Threshold = {tic_cv_threshold}")
        ms1tic_bar.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
        )

        ms1_tic = plotly.io.to_html(ms1tic_bar, include_plotlyjs=False, full_html=False)
        tic_report_params['tic_ms1_cv_plot'] = ms1_tic

        if list(set(tic_cv[f'MS1 TIC CV% Threshold = {int(tic_cv_threshold)}'].tolist())) == ['PASS']:
            tic_report_params['tic_ms1_cv_description'] = 'All groups have passed the CV Threshold'
        else:
            failed_ms1_groups = ", ".join(tic_cv[tic_cv[f'MS1 TIC CV% Threshold = {int(tic_cv_threshold)}'] == 'FAIL']['Group'].tolist())
            tic_report_params['tic_ms1_cv_description'] = f'The following groups have not met the CV Threshold: {failed_ms1_groups}. This indicates that ...'

        ms2tic_bar = px.bar(tic_cv, x='Group', y="MS2 TIC CV%", title="MS2 Total Ion Current", color="Group")
        ms2tic_bar.update_xaxes(tickfont_size=8)
        ms2tic_bar.add_hline(y=tic_cv_threshold, line_dash="dot", annotation_text=f"TIC CV Threshold = {tic_cv_threshold}")
        ms2tic_bar.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
        )

        ms2_tic = plotly.io.to_html(ms2tic_bar, include_plotlyjs=False, full_html=False)
        tic_report_params['tic_ms2_cv_plot'] = ms2_tic

        if list(set(tic_cv[f'MS2 TIC CV% Threshold = {int(tic_cv_threshold)}'].tolist())) == ['PASS']:
            tic_report_params['tic_ms2_cv_description'] = 'All groups have passed the CV Threshold'
        else:
            failed_ms2_groups = ", ".join(tic_cv[tic_cv[f'MS2 TIC CV% Threshold = {int(tic_cv_threshold)}'] == 'FAIL']['Group'].tolist())
            tic_report_params['tic_ms2_cv_description'] = f'The following groups have not met the CV Threshold: {failed_ms2_groups}. This indicates that ...'

    return tic_report_params

def spectral_plot(mzml_df):

    df = mzml_df[['Filename','MS2/MS1 Spectra', 'MS2/MS1 Spectra Outliers']]

    count_line = px.line(df, x='Filename', y="MS2/MS1 Spectra", title="MS2/MS1 Spectra Count", line_shape="spline")
    count_line.update_xaxes(tickfont_size=8)
    count_line.update_layout(
        margin=dict(l=20, r=20, t=20, b=20),
    )

    spectral_count = plotly.io.to_html(count_line, include_plotlyjs=False, full_html=False)
    spectra_report_params = {'ms2_ms1_spectral_ratio': True,
                            'ms2_ms1_spectral_ratio_plot': spectral_count,
                            'ms2_ms1_spectral_ratio_description': 'MS2/MS1 Spectra Count Ratio extracted from given mzML files'}

    if not list(set(mzml_df['MS2/MS1 Spectra Outliers'].tolist())) == [0]:
        spectra_outliers = mzml_df[mzml_df['MS2/MS1 Spectra Outliers'] == 1]['Filename'].tolist()
        spectra_outliers_filenames = ", ".join(spectra_outliers)
        spectra_report_params['ms2_ms1_spectral_ratio_outlier_description'] = f"{mzml_df['MS2/MS1 Spectra Outliers'].tolist().count(1)} outliers were found. The following files have been detected as outliers: {spectra_outliers_filenames}"

        ms2_ms1_spectral_ratio_outlier = px.scatter(mzml_df, x='Filename', y='MS2/MS1 Spectra', color='MS2/MS1 Spectra Outliers')
        ms2_ms1_spectral_ratio_outlier.update_xaxes(tickfont_size=8)
        ms2_ms1_spectral_ratio_outlier.update_layout(
                margin=dict(l=20, r=20, t=20, b=20)
        )
        ms2_ms1_spectral_ratio_plot = plotly.io.to_html(ms2_ms1_spectral_ratio_outlier, include_plotlyjs=False, full_html=False)

        spectra_report_params['ms2_ms1_spectral_ratio_outlier_plot'] = ms2_ms1_spectral_ratio_plot

    return spectra_report_params

def basepeak_graph(mzml_df, max_basepeak_intensity_threshold, groups, groupwise_comparison):

    if groupwise_comparison:
        mzml_df['Group'] = mzml_df['Filename'].apply(groupname, args=[groups,])
        bp_bar = px.bar(mzml_df, x='Filename', y="Max Basepeak Intensity", title="Max Basepeak Intensity", color="Group")
    else:
        bp_bar = px.bar(mzml_df, x='Filename', y="Max Basepeak Intensity", title="Max Basepeak Intensity")

    bp_bar.update_xaxes(tickfont_size=8)
    bp_bar.update_layout(
        margin=dict(l=20, r=20, t=20, b=20),
    )

    if max_basepeak_intensity_threshold:
        bp_bar.add_hline(y=max_basepeak_intensity_threshold, line_dash="dot", annotation_text=f"Max Basepeak Intensity Threshold = {max_basepeak_intensity_threshold}")

    bp_plot = plotly.io.to_html(bp_bar, include_plotlyjs=False, full_html=False)

    basepeak_report_params = {'max_basepeak_intensity' : True,
                              'max_basepeak_intensity_plot': bp_plot,
                              'max_basepeak_intensity_description': 'Maxiumum Basepeak Intensities identified from given mzML files'}

    if not list(set(mzml_df['Max Basepeak Intensity Outliers'].tolist())) == [0]:
        bp_outliers = mzml_df[mzml_df['Max Basepeak Intensity Outliers'] == 1]['Filename'].tolist()
        bp_outliers_filenames = ", ".join(bp_outliers)
        basepeak_report_params['max_basepeak_intensity_outlier_description'] = f"{mzml_df['Max Basepeak Intensity Outliers'].tolist().count(1)} outliers were found. The following files have been detected as outliers: {bp_outliers_filenames}"

        max_basepeak_intensity_outlier = px.scatter(mzml_df, x='Filename', y='Max Basepeak Intensity', color='Max Basepeak Intensity Outliers')
        max_basepeak_intensity_outlier.update_xaxes(tickfont_size=8)
        max_basepeak_intensity_outlier.update_layout(
                margin=dict(l=20, r=20, t=20, b=20)
        )
        max_basepeak_intensity_outlier_plot = plotly.io.to_html(max_basepeak_intensity_outlier, include_plotlyjs=False, full_html=False)

        basepeak_report_params['max_basepeak_intensity_outlier_plot'] = max_basepeak_intensity_outlier_plot

    return basepeak_report_params

def create_graphs(mzml_df, tic_cv, groupwise_comparison, groups, mzml_threshold_dict):

    tic_report_params = tic_plots(mzml_df, tic_cv, mzml_threshold_dict['MS1 TIC Threshold'], mzml_threshold_dict['MS2 TIC Threshold'], mzml_threshold_dict['TIC CV Threshold'], groupwise_comparison)
    spectra_report_params = spectral_plot(mzml_df)
    basepeak_report_params = basepeak_graph(mzml_df, mzml_threshold_dict['Max Basepeak Intensity Threshold'], groups, groupwise_comparison)

    idfree_report_parameters = dict(tuple(tic_report_params.items()) + tuple(spectra_report_params.items()) + tuple(basepeak_report_params.items()))

    return idfree_report_parameters

#---------------------------------------------------------------------- MAIN FUNCTION CALL -------------------------------------------------------------------------

def calculate_idfree_metrics(out_dir, reportname, mzml_dir, groupwise_comparison, groups, mzml_threshold_dict):

    #getting list of mzML files
    mzml_list = get_mzml_list(mzml_dir)

    #extracting data from mzml files
    mzml_df = get_mzml_info_dataframe(mzml_list)

    #applying thresholds + outlier detection
    mzml_df = apply_idfree_thresholds(mzml_df, mzml_threshold_dict)
    mzml_df = outlier_detection(mzml_df)

    if groupwise_comparison:
        tic_cv = calculate_tic_cv(mzml_df, groups, mzml_threshold_dict['TIC CV Threshold'])
    else:
        tic_cv = ""

    #saving dataframes to excel document
    writer = pd.ExcelWriter(f"{out_dir}/{reportname}_ID-Free_QC_Report.xlsx", engine='xlsxwriter')
    mzml_df.to_excel(writer, index=False, sheet_name="ID-Free Metrics Summary")
    if groupwise_comparison:
        tic_cv.to_excel(writer, index=False, sheet_name='Group TIC CV')
    writer.save()

    idfree_report_parameters = create_graphs(mzml_df, tic_cv, groupwise_comparison, groups, mzml_threshold_dict)

    mzml_sample_df = get_sample_qc(mzml_df, mzml_threshold_dict)
    if groupwise_comparison:
        idfree_grouped_df = get_idfree_grouped_df(mzml_sample_df, tic_cv, mzml_threshold_dict['TIC CV Threshold'], groups)
    else:
        idfree_grouped_df = ""

    return (mzml_sample_df, idfree_grouped_df, idfree_report_parameters)
