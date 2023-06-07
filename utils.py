import logging
import os
import shutil
import sys
from math import floor
from typing import TypeVar

from PIL import Image

from uheprng import UHEPRNG

T = TypeVar("T")


log = logging.getLogger(__name__)


def get_urls_list(urls_file, done_file):
    """
    Get list of urls from .txt file
    --------------------------
    param: urls_file -- string
        Name or path of .txt file with manga urls
    param: done_file -- string
        Name or path of .txt file with successfully downloaded manga urls
    return: urls -- list
        Urls from urls_file
    """
    log.debug("Parsing list of urls")
    done = set()
    with open(done_file, "r") as donef:
        for line in donef:
            done.add(line.replace("\n", ""))
    log.debug(f"Done: {len(done)}")

    urls = []
    with open(urls_file, "r") as f:
        for line in f:
            clean_line = line.replace("\n", "")
            if "fakku.net/hentai/" not in clean_line:
                continue
            if clean_line.startswith("#"):
                continue
            elif "#" in clean_line:
                clean_line = clean_line.split("#")[0]
            if clean_line not in done and clean_line not in urls:
                urls.append(clean_line)
    log.debug(f"Urls: {len(urls)}")
    if len(urls) == 0:
        log.info("Nothing to rip")
        exit()
    return urls


def fix_filename(filename):
    """
    Removes illegal characters from filename.
    """
    log.debug("Fixing string")
    filename = filename.replace("\n", "")
    filename = filename.replace("\r", "")
    filename = filename.replace("\t", "")
    filename = filename.replace("/", "⁄")
    if sys.platform == "win32":
        filename = filename.replace("?", "？")
        filename = filename.replace("\\", "⧹")
        filename = filename.replace(":", "꞉")
        filename = filename.replace("*", "＊")
        filename = filename.replace('"', "＂")
        filename = filename.replace("<", "＜")
        filename = filename.replace(">", "＞")
        filename = filename.replace("|", "｜")
    return filename


def decode_xor_cipher(key: bytes | str, content: bytes) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")

    key_length = len(key)
    content_length = len(content)
    decoded = bytearray(content_length)

    for i in range(content_length):
        decoded[i] = content[i] ^ key[i % key_length]

    return bytes(decoded)


def calculate_decryption_key(key_hash: str, fakku_zid: str) -> str:
    zid = "13fafbe11a72969c2464696efd553940f6a45c1c4801b19c3445e033f38b0e7e"

    if fakku_zid == zid:
        extra = "b3d90ea3cc794be5e74013880c4519aae1b8fbe3108f2bbe60c5dc3f6e807ff1"
    else:
        extra = "0a10f3bd42587ad70fc96886d8e5e7b3614ce69529b238a1c690cb9b51d4868f"

    return fakku_zid + key_hash + extra


def randomize(ls: list[T], seed) -> list[T]:
    instance = UHEPRNG()
    instance.seed(seed)
    copy = ls.copy()

    i = len(copy)
    while i:
        new_location = floor(instance.random() * i)
        i -= 1
        copy[i], copy[new_location] = copy[new_location], copy[i]
    return copy


def shuffle_array(ls: list[T], seed) -> list[T]:
    copy = ls.copy()
    for i, j in enumerate(randomize(list(range(len(ls))), seed)):
        copy[j] = ls[i]
    return copy


def append_images(
    imgs,
    direction="horizontal",
    bg_color=(255, 255, 255),
    alignment="center",
    src_type="unscrambled",
    dirc=None,
):
    """
    Appends images in horizontal/vertical direction. Used for joining spreads.

    Args:
        imgs: List of PIL images
        direction: direction of concatenation, 'horizontal' or 'vertical'
        bg_color: Background color (default: white)
        alignment: alignment mode if images need padding;
           'left', 'right', 'top', 'bottom', or 'center'
        src_type: image type, scrambled or not (default: 'unscrambled')
        dirc: reading direction, "Left to Right" or none (default: None)

    Returns:
        Concatenated image as a new PIL image object.
    """
    log.debug("Joining spreads")
    if dirc != "Left to Right":
        imgs.reverse()
    if type(imgs[0]) is str:
        images = map(Image.open, imgs)
    else:
        images = imgs

    widths, heights = zip(*(i.size for i in images))

    if direction == "horizontal":
        new_width = sum(widths)
        new_height = max(heights)
    else:
        new_width = max(widths)
        new_height = sum(heights)

    if type(imgs[0]) is str:
        images = list(map(Image.open, imgs))
    else:
        images = imgs

    if src_type == "scrambled":
        new_im = Image.new("RGBA", (new_width, new_height), color=bg_color)
    else:
        if images[0].mode == "L" and images[1].mode == "L":
            new_im = Image.new("L", (new_width, new_height), color=255)
        else:
            new_im = Image.new("RGB", (new_width, new_height), color=bg_color)

    offset = 0
    for im in images:
        if direction == "horizontal":
            y = 0
            if alignment == "center":
                y = int((new_height - im.size[1]) / 2)
            elif alignment == "bottom":
                y = new_height - im.size[1]
            new_im.paste(im, (offset, y))
            offset += im.size[0]
        else:
            x = 0
            if alignment == "center":
                x = int((new_width - im.size[0]) / 2)
            elif alignment == "right":
                x = new_width - im.size[0]
            new_im.paste(im, (x, offset))
            offset += im.size[1]

    return new_im


def many_to_one(data: list[str]) -> str | None:
    for i, v in enumerate(data):
        data[i] = fix_filename(v)
    if len(data) > 2:
        return "Various"
    elif len(data) == 2:
        return ", ".join(data)
    elif len(data) == 1:
        return data[0]
    else:
        return None


def _make_cbzfile(
    base_name, base_dir, verbose=0, dry_run=0, logger=None, owner=None, group=None
):
    """Create a cbz file from all the files under 'base_dir'.

    The output cbz file will be named 'base_name' + ".cbz".  Returns the
    name of the output cbz file.
    """
    import zipfile  # late import for breaking circular dependency

    cbz_filename = base_name + ".cbz"
    archive_dir = os.path.dirname(base_name)

    if archive_dir and not os.path.exists(archive_dir):
        if logger is not None:
            logger.info("creating %s", archive_dir)
        if not dry_run:
            os.makedirs(archive_dir)

    if logger is not None:
        logger.info("creating '%s' and adding '%s' to it", cbz_filename, base_dir)

    if not dry_run:
        with zipfile.ZipFile(cbz_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            path = os.path.normpath(base_dir)
            if path != os.curdir:
                zf.write(path, path)
                if logger is not None:
                    logger.info("adding '%s'", path)
            for dirpath, dirnames, filenames in os.walk(base_dir):
                for name in sorted(dirnames):
                    path = os.path.normpath(os.path.join(dirpath, name))
                    zf.write(path, path)
                    if logger is not None:
                        logger.info("adding '%s'", path)
                for name in filenames:
                    path = os.path.normpath(os.path.join(dirpath, name))
                    if os.path.isfile(path):
                        zf.write(path, path)
                        if logger is not None:
                            logger.info("adding '%s'", path)

    return cbz_filename


shutil.register_archive_format("cbz", _make_cbzfile, [], "CBZ file")
