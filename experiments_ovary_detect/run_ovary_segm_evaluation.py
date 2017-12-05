"""
Evaluate the resulting individual object / egg segmentation

SAMPLE run:
>> python run_ovary_segm_evaluation.py \
    --images "~/Medical-drosophila/ovary_selected_slices/png2/*.png" \
    --annots "~/Medical-drosophila/Ovary-eggs/mask_2d_slice_complete_ind_egg/*.png" \
    --segments "~/Medical-drosophila/RESULTS/segment_ovary_slices_selected/*.png" \
    --centers "~/Medical-drosophila/RESULTS/detect_ovary_centers_detect/*.csv" \
    --results ~/Medical-drosophila/RESULTS/experiment_egg-segment_ovary

Copyright (C) 2016 Jiri Borovec <jiri.borovec@fel.cvut.cz>
"""

import os
import sys
import glob
import logging
import argparse
import multiprocessing as mproc
from functools import partial

import matplotlib
if os.environ.get('DISPLAY', '') == '':
    logging.warning('No display found. Using non-interactive Agg backend')
matplotlib.use('Agg')

import tqdm
import numpy as np
import pandas as pd
from sklearn import metrics
import matplotlib.pyplot as plt

sys.path += [os.path.abspath('.'), os.path.abspath('..')]  # Add path to root
import segmentation.utils.experiments as tl_expt
import segmentation.utils.data_io as tl_io
import segmentation.utils.drawing as tl_visu
import segmentation.labeling as seg_lbs

EXPORT_VUSIALISATION = True
NB_THREADS = max(1, int(mproc.cpu_count() * 0.9))

NAME_DIR_VISUAL_1 = 'ALL_visualisation-1'
NAME_DIR_VISUAL_2 = 'ALL_visualisation-2'
NAME_DIR_VISUAL_3 = 'ALL_visualisation-3'
SKIP_DIRS = ['input', 'simple',
             NAME_DIR_VISUAL_1, NAME_DIR_VISUAL_2, NAME_DIR_VISUAL_3]
NAME_CSV_STAT = 'segmented-eggs_%s.csv'
PATH_IMAGES = tl_io.update_path(os.path.join('images', 'drosophila_ovary_slice'))
PATH_RESULTS = tl_io.update_path('results', absolute=True)
PATHS = {
    'images': os.path.join(PATH_IMAGES, 'image', '*.jpg'),
    'annots': os.path.join(PATH_IMAGES, 'annot_eggs', '*.png'),
    'segments': os.path.join(PATH_IMAGES, 'segm', '*.png'),
    'centers': os.path.join(PATH_IMAGES, 'center_levels', '*.csv'),
    'results': os.path.join(PATH_RESULTS, 'experiment_egg-segment_ovary'),
}
LUT_COLOR = np.array([
    (1., 1., 1.),
    (0.75, 0.75, 0.75),
    (0.5, 0.5, 0.5),
    (0.3, 0.3, 0.3)
])


def arg_parse_params(paths=PATHS):
    """
    SEE: https://docs.python.org/3/library/argparse.html
    :return: {str: str}, int
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--images', type=str, required=False,
                        help='path to directory & name pattern for images',
                        default=paths['images'])
    parser.add_argument('--annots', type=str, required=False,
                        help='path to directory & name pattern for annotation',
                        default=paths['annots'])
    parser.add_argument('--segments', type=str, required=False,
                        help='path to directory & name pattern for segmentation',
                        default=paths['segments'])
    parser.add_argument('--centers', type=str, required=False,
                        help='path to directory & name pattern for centres',
                        default=paths['centers'])
    parser.add_argument('--results', type=str, required=False,
                        help='path to the result directory',
                        default=paths['results'])
    parser.add_argument('--nb_jobs', type=int, required=False,
                        default=NB_THREADS,
                        help='number of processes in parallel')
    parser.add_argument('--visual', type=int, required=False,
                        default=EXPORT_VUSIALISATION,
                        help='export visualisations')
    arg_params = vars(parser.parse_args())
    export_visual = bool(arg_params['visual'])
    for k in (k for k in arg_params if k != 'nb_jobs' and k != 'visual'):
        if not isinstance(arg_params[k], str) or arg_params[k].lower() == 'none':
            paths[k] = None
            continue
        paths[k] = tl_io.update_path(arg_params[k], absolute=True)
        p = os.path.dirname(paths[k]) if '*' in paths[k] else paths[k]
        assert os.path.exists(p), '%s' % p
    logging.info('ARG PARAMETERS: \n %s', repr(paths))
    return paths, export_visual, arg_params['nb_jobs']


def compute_metrics(row):
    """ load segmentation and compute similarity metrics

    :param {str: ...} row:
    :return {str: float}:
    """
    logging.debug('loading annot "%s"\n and segm "%s"',
                  row['path_annot'], row['path_egg-segm'])
    annot, _ = tl_io.load_image_2d(row['path_annot'])
    segm, _ = tl_io.load_image_2d(row['path_egg-segm'])
    assert annot.shape == segm.shape, 'dimension do mot match %s - %s' % \
                                      (repr(annot.shape), repr(segm.shape))
    list_jacob = []
    segm = seg_lbs.relabel_max_overlap_unique(annot, segm, keep_bg=True)
    for lb in np.unique(annot)[1:]:
        annot_obj = (annot == lb)
        segm_obj = (segm == lb)
        # label_hist = seg_lb.histogram_regions_labels_counts(segm, annot_obj)
        # segm_obj = np.argmax(label_hist, axis=1)[segm]
        jacoby = np.sum(np.logical_and(annot_obj, segm_obj)) \
                 / float(np.sum(np.logical_or(annot_obj, segm_obj)))
        list_jacob.append(jacoby)
    if len(list_jacob) == 0:
        list_jacob.append(0)

    # avg_weight = 'samples' if len(np.unique(annot)) > 2 else 'binary'
    y_true, y_pred = annot.ravel(), segm.ravel()
    dict_eval = {
        'name': os.path.basename(row['path_annot']),
        'ARS': metrics.adjusted_rand_score(y_true, y_pred),
        'Jaccard': np.mean(list_jacob),
        'f1': metrics.f1_score(y_true, y_pred, average='micro'),
        'accuracy': metrics.accuracy_score(y_true, y_pred),
        'precision': metrics.precision_score(y_true, y_pred, average='micro'),
        'recall': metrics.recall_score(y_true, y_pred, average='micro'),
    }

    return dict_eval


def expert_visual(row, method_name, path_out, max_fig_size=10):
    """ export several visualisation segmentation and annotation

    :param {str: ...} row:
    :param str method_name:
    :param str path_out:
    :param int max_fig_size:
    :return:
    """
    im_name = os.path.splitext(os.path.basename(row['path_image']))[0]
    img, _ = tl_io.load_image_2d(row['path_image'])
    # annot = tl_io.load_image(row['path_annot'])
    egg_segm, _ = tl_io.load_image_2d(row['path_egg-segm'])
    in_segm, _ = tl_io.load_image_2d(row['path_in-segm'])
    centers = tl_io.load_landmarks_csv(row['path_centers'])
    centers = np.array(tl_io.swap_coord_x_y(centers))

    fig_size = max_fig_size * np.array(img.shape[:2]) / float(np.max(img.shape))
    fig_name = '%s_%s.jpg' % (im_name, method_name)

    fig, ax = plt.subplots(figsize=fig_size[::-1])
    ax.imshow(img[:, :, 0], cmap=plt.cm.gray)
    ax.imshow(egg_segm, alpha=0.15)
    ax.contour(egg_segm, levels=np.unique(egg_segm), linewidths=(3,))
    ax.plot(centers[:, 1], centers[:, 0], 'ob')
    tl_visu.figure_image_adjustment(fig, img.shape)
    path_fig = os.path.join(path_out, NAME_DIR_VISUAL_1, fig_name)
    fig.savefig(path_fig, bbox_inches='tight', pad_inches=0)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=fig_size[::-1])
    # ax.imshow(np.max(in_segm) - in_segm, cmap=plt.cm.gray)
    ax.imshow(LUT_COLOR[in_segm], vmin=0., vmax=1., alpha=0.5)
    ax.contour(in_segm, levels=np.unique(in_segm), colors='k')
    ax.imshow(egg_segm, alpha=0.3)
    ax.contour(egg_segm, levels=np.unique(egg_segm), linewidths=(5,))
    ax.plot(centers[:, 1], centers[:, 0], 'or')
    tl_visu.figure_image_adjustment(fig, img.shape)
    path_fig = os.path.join(path_out, NAME_DIR_VISUAL_2, fig_name)
    fig.savefig(path_fig, bbox_inches='tight', pad_inches=0)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=fig_size[::-1])
    ax.imshow(img[:, :, 0], cmap=plt.cm.gray, alpha=1.)
    ax.contour(in_segm, levels=np.unique(in_segm), colors='w')
    ax.imshow(egg_segm, alpha=0.3)
    ax.contour(egg_segm, levels=np.unique(egg_segm), linewidths=(5,))
    ax.plot(centers[:, 1], centers[:, 0], 'og')
    tl_visu.figure_image_adjustment(fig, img.shape)
    path_fig = os.path.join(path_out, NAME_DIR_VISUAL_3, fig_name)
    fig.savefig(path_fig, bbox_inches='tight', pad_inches=0)
    plt.close(fig)


def evaluate_folder(path_dir, dict_paths, export_visual=EXPORT_VUSIALISATION):
    """ take a single folder with segmentation and compute statistic
    against annotation and export some visualisations, return computed stat.

    :param str path_dir:
    :param {str, str} dict_paths:
    :param bool export_visual:
    :return {str: float}:
    """
    logging.info('evaluate folder: %s', path_dir)
    name = os.path.basename(path_dir)

    list_paths = [dict_paths['images'], dict_paths['annots'],
                  dict_paths['segments'], dict_paths['centers'],
                  os.path.join(path_dir, '*.png')]
    df_paths = tl_io.find_files_match_names_across_dirs(list_paths)

    if len(df_paths) == 0:
        return {'method': name, 'count': 0}

    if dict_paths['annots'] is not None:
        df_paths.columns = ['path_image', 'path_annot', 'path_in-segm',
                            'path_centers', 'path_egg-segm']
    else:
        df_paths.columns = ['path_image', 'path_in-segm',
                            'path_centers', 'path_egg-segm']
    df_paths.index = range(1, len(df_paths) + 1)
    df_paths.to_csv(os.path.join(dict_paths['results'], NAME_CSV_STAT % name))

    if export_visual:
        for _, row in df_paths.iterrows():
            expert_visual(row, name, path_out=dict_paths['results'])

    if dict_paths['annots'] is None:
        logging.info('no Annotation given')
        return {'method': name, 'count': 0}

    df_eval = pd.DataFrame()
    for _, row in df_paths.iterrows():
        dict_seg = compute_metrics(row)
        df_eval = df_eval.append(dict_seg, ignore_index=True)

    df_eval.set_index(['name'], inplace=True)
    df_eval.to_csv(os.path.join(dict_paths['results'], NAME_CSV_STAT % name))

    df_summary = df_eval.describe()
    cols = df_eval.columns.tolist()
    dict_eval = {'method': name, 'count': len(df_eval)}
    for n in ['mean', 'std']:
        names = ['%s (%s)' % (c, n) for c in cols]
        dict_eval.update(zip(names, df_summary.T[n].values.tolist()))
    dict_eval.update(zip(['%s (median)' % (c) for c in cols],
                         df_eval.median(axis=0).values.tolist()))

    return dict_eval


def main(dict_paths, export_visual=EXPORT_VUSIALISATION, nb_jobs=NB_THREADS):
    """ evaluate all segmentations in experiment folder

    :param {str: str} paths: path to all required directories
    :param bool export_visual: export visualisations
    :param int nb_jobs: number threads in parralel
    """
    logging.info('running in %i jobs...', nb_jobs)
    logging.info(tl_expt.string_dict(dict_paths, desc='PATHS'))

    list_results = sorted(glob.glob(os.path.join(dict_paths['results'], '*')))
    list_results = sorted([p for p in list_results
                            if os.path.isdir(p)
                             and '___' not in os.path.basename(p)
                             and os.path.basename(p) not in SKIP_DIRS])

    tl_expt.create_subfolders(dict_paths['results'],
                    [NAME_DIR_VISUAL_1, NAME_DIR_VISUAL_2, NAME_DIR_VISUAL_3])

    df_all = pd.DataFrame()
    tqdm_bar = tqdm.tqdm(total=len(list_results))
    wrapper_eval = partial(evaluate_folder, dict_paths=dict_paths,
                           export_visual=export_visual)
    if nb_jobs > 1:
        mproc_pool = mproc.Pool(nb_jobs)
        for dict_eval in mproc_pool.imap_unordered(wrapper_eval, list_results):
            df_all = df_all.append(dict_eval, ignore_index=True)
            tqdm_bar.update()
        mproc_pool.close()
        mproc_pool.join()
    else:
        for dict_eval in map(wrapper_eval, list_results):
            df_all = df_all.append(dict_eval, ignore_index=True)
            tqdm_bar.update()

    df_all.set_index(['method'], inplace=True)
    df_all.sort_index(inplace=True)
    logging.info('STATISTIC: \n %s', repr(df_all))
    df_all.to_csv(os.path.join(dict_paths['results'], NAME_CSV_STAT % 'OVERALL'))

    logging.info('Done :]')


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    paths, nb_jobs, export_visual = arg_parse_params()
    main(paths, export_visual, nb_jobs)