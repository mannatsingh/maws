# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import contextlib

import matplotlib
import numpy as np

import torch
from matplotlib import pyplot as plt

import gzip
import hashlib
import logging
import os
import tarfile
import zipfile

import torch
from filelock import FileLock

_STACK = None


def start_inference_mode(device="cpu"):
    global _STACK
    if _STACK is None:
        _STACK = contextlib.ExitStack()
        _STACK.enter_context(torch.inference_mode())


def reset_inference_mode():
    global _STACK
    _STACK.close()
    _STACK = None


def predict_probs_for_image(model, image_path, texts):
    image_path = get_asset_local_path(image_path)
    # cropped_image = model.get_cropped_images(image_path)
    return (model.classify(image_path, texts) * 100).tolist()[0]


def plot_probs(texts, probs, fig_ax, lang_type=None):
    # reverse the order to plot from top to bottom
    probs = probs[::-1]
    texts = texts[::-1]
    probs = np.array(probs)
    if fig_ax is None:
        fig, ax = plt.subplots(figsize=(6, 3))
    else:
        fig, ax = fig_ax

    font_prop = matplotlib.font_manager.FontProperties(
        fname=lang_type_to_font_path(lang_type)
    )

    ax.barh(texts, probs, color="darkslateblue", height=0.3)
    ax.barh(texts, 100 - probs, color="silver", height=0.3, left=probs)
    for bar, label, val in zip(ax.patches, texts, probs):
        ax.text(
            0,
            bar.get_y() - bar.get_height(),
            label,
            color="black",
            ha="left",
            va="center",
            fontproperties=font_prop,
        )
        ax.text(
            bar.get_x() + bar.get_width() + 1,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.2f} %",
            fontweight="bold",
            ha="left",
            va="center",
        )

    ax.axis("off")


def predict_probs_and_plot(
    model, image_path, texts, plot_image=True, fig_ax=None, lang_type=None
):
    if plot_image:
        fig, (ax_1, ax_2) = plt.subplots(1, 2, figsize=(12, 6))
        ax_1.imshow(model.get_cropped_images(image_path))
        ax_1.axis("off")
    probs = predict_probs_for_image(model, image_path, texts)
    plot_probs(texts, probs, (fig, ax_2), lang_type=lang_type)


def lang_type_to_font_path(lang_type):
    mapping = {
        None: "https://cdn.jsdelivr.net/gh/notofonts/notofonts.github.io/fonts/NotoSans/hinted/ttf/NotoSans-Regular.ttf",
        "cjk": "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
        "devanagari": "https://cdn.jsdelivr.net/gh/notofonts/notofonts.github.io/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Regular.ttf",
        "emoji": "https://github.com/MorbZ/OpenSansEmoji/raw/master/OpenSansEmoji.ttf",
    }
    return get_asset_local_path(mapping[lang_type])


from ._download_hooks import _DATASET_DOWNLOAD_MANAGER

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 600
from torch.hub import _get_torch_home

_CACHE_DIR = os.path.expanduser(os.path.join(_get_torch_home(), "text"))


def get_lock_dir():
    lock_dir = os.path.join(_CACHE_DIR, "locks")
    if not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
    return lock_dir


def reporthook(t):
    """
    https://github.com/tqdm/tqdm.
    """
    last_b = [0]

    def inner(b=1, bsize=1, tsize=None):
        """
        b: int, optional
        Number of blocks just transferred [default: 1].
        bsize: int, optional
        Size of each block (in tqdm units) [default: 1].
        tsize: int, optional
        Total size (in tqdm units). If [default: None] remains unchanged.
        """
        if tsize is not None:
            t.total = tsize
        t.update((b - last_b[0]) * bsize)
        last_b[0] = b

    return inner


def validate_file(file_obj, hash_value, hash_type="sha256"):
    """Validate a given file object with its hash.
    Args:
        file_obj: File object to read from.
        hash_value (str): Hash for url.
        hash_type (str, optional): Hash type, among "sha256" and "md5" (Default: ``"sha256"``).
    Returns:
        bool: return True if its a valid file, else False.
    """

    if hash_type in ("sha256", "md5"):
        hash_func = hashlib.new(hash_type, usedforsecurity=False)
    else:
        raise ValueError

    while True:
        # Read by chunk to avoid filling memory
        chunk = file_obj.read(1024**2)
        if not chunk:
            break
        hash_func.update(chunk)
    return hash_func.hexdigest() == hash_value


def _check_hash(path, hash_value, hash_type):
    logger.info("Validating hash {} matches hash of {}".format(hash_value, path))
    with open(path, "rb") as file_obj:
        if not validate_file(file_obj, hash_value, hash_type):
            raise RuntimeError(
                "The hash of {} does not match. Delete the file manually and retry.".format(
                    os.path.abspath(path)
                )
            )


def download_from_url(
    url, path=None, root=".data", overwrite=False, hash_value=None, hash_type="sha256"
):
    """Download file, with logic (from tensor2tensor) for Google Drive. Returns
    the path to the downloaded file.
    Args:
        url: the url of the file from URL header. (None)
        path: path where file will be saved
        root: download folder used to store the file in (.data)
        overwrite: overwrite existing files (False)
        hash_value (str, optional): hash for url (Default: ``None``).
        hash_type (str, optional): hash type, among "sha256" and "md5" (Default: ``"sha256"``).
    Examples:
        >>> url = 'http://www.quest.dcs.shef.ac.uk/wmt16_files_mmt/validation.tar.gz'
        >>> torchtext.utils.download_from_url(url)
        >>> url = 'http://www.quest.dcs.shef.ac.uk/wmt16_files_mmt/validation.tar.gz'
        >>> torchtext.utils.download_from_url(url)
        >>> '.data/validation.tar.gz'
    """
    # figure out filename and root
    if path is None:
        _, filename = os.path.split(url)
        root = os.path.abspath(root)
        path = os.path.join(root, filename)
    else:
        path = os.path.abspath(path)
        root, filename = os.path.split(os.path.abspath(path))

    # In a concurrent setting, adding a file lock ensures the first thread to acquire will actually download the model
    # and the other ones will just use the existing path (which will not contain a partially downloaded model).
    lock_dir = get_lock_dir()
    lock = FileLock(os.path.join(lock_dir, filename + ".lock"), timeout=LOCK_TIMEOUT)
    with lock:
        # skip download if path exists and overwrite is not True
        if os.path.exists(path):
            logger.info("File %s already exists." % path)
            if not overwrite:
                if hash_value:
                    _check_hash(path, hash_value, hash_type)
                return path

        # make root dir if does not exist
        if not os.path.exists(root):
            try:
                os.makedirs(root)
            except OSError:
                raise OSError("Can't create the download directory {}.".format(root))

        # download data and move to path
        _DATASET_DOWNLOAD_MANAGER.get_local_path(url, destination=path)

        logger.info("File {} downloaded.".format(path))

        # validate
        if hash_value:
            _check_hash(path, hash_value, hash_type)

        # all good
        return path


def extract_archive(from_path, to_path=None, overwrite=False):
    """Extract archive.
    Args:
        from_path: the path of the archive.
        to_path: the root path of the extracted files (directory of from_path)
        overwrite: overwrite existing files (False)
    Returns:
        List of paths to extracted files even if not overwritten.
    Examples:
        >>> url = 'http://www.quest.dcs.shef.ac.uk/wmt16_files_mmt/validation.tar.gz'
        >>> from_path = './validation.tar.gz'
        >>> to_path = './'
        >>> torchtext.utils.download_from_url(url, from_path)
        >>> torchtext.utils.extract_archive(from_path, to_path)
        >>> ['.data/val.de', '.data/val.en']
        >>> torchtext.utils.download_from_url(url, from_path)
        >>> torchtext.utils.extract_archive(from_path, to_path)
        >>> ['.data/val.de', '.data/val.en']
    """

    if to_path is None:
        to_path = os.path.dirname(from_path)

    if from_path.endswith((".tar.gz", ".tgz")):
        logger.info("Opening tar file {}.".format(from_path))
        with tarfile.open(from_path, "r") as tar:
            files = []
            for file_ in tar:
                file_path = os.path.join(to_path, file_.name)
                if file_.isfile():
                    files.append(file_path)
                    if os.path.exists(file_path):
                        logger.info("{} already extracted.".format(file_path))
                        if not overwrite:
                            continue
                tar.extract(file_, to_path)
            logger.info("Finished extracting tar file {}.".format(from_path))
            return files

    elif from_path.endswith(".zip"):
        assert zipfile.is_zipfile(from_path), from_path
        logger.info("Opening zip file {}.".format(from_path))
        with zipfile.ZipFile(from_path, "r") as zfile:
            files = []
            for file_ in zfile.namelist():
                file_path = os.path.join(to_path, file_)
                files.append(file_path)
                if os.path.exists(file_path):
                    logger.info("{} already extracted.".format(file_path))
                    if not overwrite:
                        continue
                zfile.extract(file_, to_path)
        files = [f for f in files if os.path.isfile(f)]
        logger.info("Finished extracting zip file {}.".format(from_path))
        return files

    elif from_path.endswith(".gz"):
        logger.info("Opening gz file {}.".format(from_path))
        default_block_size = 65536
        filename = from_path[:-3]
        files = [filename]
        with gzip.open(from_path, "rb") as gzfile, open(filename, "wb") as d_file:
            while True:
                block = gzfile.read(default_block_size)
                if not block:
                    break
                else:
                    d_file.write(block)
            d_file.write(block)
        logger.info("Finished extracting gz file {}.".format(from_path))
        return files

    else:
        raise NotImplementedError(
            "We currently only support tar.gz, .tgz, .gz and zip achives."
        )


def _log_class_usage(klass):
    identifier = "torchtext"
    if klass and hasattr(klass, "__name__"):
        identifier += f".{klass.__name__}"
    torch._C._log_api_usage_once(identifier)


def get_asset_local_path(asset_path: str, overwrite=False) -> str:
    """Get local path for assets. Download if path does not exist locally
    Args:
        asset_path: Local path to asset or remote URL
        overwrite: Indicate whether to overwrite the file when downloading from URL (default: False)
    Returns:
        bool: local path of the asset after downloading or reading from cache
    Examples:
        >>> url = 'http://<HOST>/file.txt'
        >>> torchtext.utils.get_asset_local_path(url)
        >>> '.data/file.txt'
        >>> torchtext.utils.get_asset_local_path('/home/user/file.txt')
        >>> '/home/user/file.txt'
    """
    if os.path.exists(asset_path):
        local_path = asset_path
    else:
        local_path = download_from_url(
            url=asset_path, root=_CACHE_DIR, overwrite=overwrite
        )
    return local_path
