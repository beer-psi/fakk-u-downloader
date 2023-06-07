import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
from base64 import b64decode
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http import cookiejar
from io import BytesIO
from math import ceil
from time import sleep

import requests
import requests.utils
from bs4 import BeautifulSoup
from dateutil.parser import parse
from PIL import Image
from requests.cookies import RequestsCookieJar
from tqdm import tqdm

from consts import *
from utils import (
    append_images,
    calculate_decryption_key,
    decode_xor_cipher,
    fix_filename,
    get_urls_list,
    many_to_one,
    randomize,
    shuffle_array,
)

log = logging.getLogger(__name__)


class DescrambleDownloader:
    """Class for downloading galleries.

    The idea is simple, we download the images and descramble locally, removing the need
    for any selenium bullshit.
    """

    cookies: RequestsCookieJar

    def __init__(
        self,
        urls_file=URLS_FILE,
        done_file=DONE_FILE,
        cookies_file=COOKIES_FILE,
        root_manga_dir=ROOT_MANGA_DIR,
        root_response_dir=ROOT_RESPONSE_DIR,
        timeout=TIMEOUT,
        wait=WAIT,
        _zip=ZIP,
        save_metadata="none",
        proxy=None,
        response=False,
        optimize=OPTIMIZE,
    ):
        self.done_file = done_file
        self.urls = get_urls_list(urls_file, done_file)
        self.root_manga_dir = root_manga_dir
        self.root_response_dir = root_response_dir

        self.save_metadata = save_metadata

        self.timeout = timeout
        self.wait = wait

        self.zip = _zip

        self.keep_response = response

        self.optimize = optimize
        if optimize and shutil.which("pingo") is None:
            log.warning("Pingo not found, disabling optimization")
            self.optimize = False

        cookies_ext = os.path.splitext(cookies_file)[1]

        self.session = requests.Session()
        if proxy is not None:
            self.session.proxies.update({"https": proxy})

        if cookies_ext == ".json":
            with open(cookies_file, "r") as f:
                data = json.load(f)
            requests.utils.cookiejar_from_dict(data, cookiejar=self.session.cookies)
        elif cookies_ext == ".txt":
            cookies = cookiejar.MozillaCookieJar(cookies_file)
            cookies.load()

            for cookie in cookies:
                self.session.cookies.set_cookie(cookie)
        else:
            raise ValueError("Unknown cookies file format")

        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "DNT": "1",
                "Pragma": "no-cache",
            }
        )

    def get_page_metadata(self, doc: BeautifulSoup) -> OrderedDict:
        metadata = OrderedDict()

        if self.save_metadata == "basic":
            return metadata

        log.debug("Parsing right side for metadata")

        meta_rows = doc.select(
            'div[class^="block md:table-cell relative w-full align-top"] div[class^="table text-sm w-full"]'
        )

        log.debug("Parsing right side rows")
        for row in meta_rows:
            meta_row_left = row.select_one(
                'div[class^="inline-block w-24 text-left align-top"]'
            )
            left_text = meta_row_left.text.strip() if meta_row_left is not None else ""

            if not left_text or left_text in [
                "Artist",
                "Parody",
                "Publisher",
                "Language",
                "Pages",
                "Direction",
            ]:
                continue

            log.debug(f"Parsing {left_text}")
            meta_row_right = row.select_one(
                'div[class^="table-cell w-full align-top text-left"]'
            )
            if meta_row_right is not None:
                a_tags = meta_row_right.select("a")
                if len(a_tags) > 0:
                    metadata[left_text] = [
                        a_tag.text.strip() for a_tag in a_tags if a_tag.text != "+"
                    ]
                else:
                    if left_text in ["Favorites"]:
                        metadata[left_text] = int(
                            "".join(
                                meta_row_right.text.strip().split(" ")[0].split(",")
                            )
                        )
                    else:
                        metadata[left_text] = meta_row_right.text.strip()

        log.debug("Parsing left side for metadata")
        price_elem = doc.select_one(
            'div[class^="block sm:inline-block relative w-full align-top"] div[class^="rounded cursor-pointer right"] div[class^="table w-auto text-right opacity-90 hover:opacity-100 js-purchase-product"] div'
        )
        if price_elem is not None:
            price = float(price_elem.text[1:])
            metadata["Price"] = price

        # log.debug("Parsing bottom")
        return metadata

    def get_api_metadata(self, metadata: dict, api_data: dict):
        metadata_api = OrderedDict()
        log.debug("Parsing API metadata")

        content = api_data["content"]

        metadata_api["URL"] = content["content_url"]
        metadata_api["Title"] = content["content_name"]
        metadata_api["Artist"] = [x["attribute"] for x in content["content_artists"]]
        metadata_api["Parody"] = [x["attribute"] for x in content["content_series"]]
        metadata_api["Language"] = content["content_language"]
        metadata_api["Pages"] = content["content_pages"]
        metadata_api["Description"] = content["content_description"]
        metadata_api["Tags"] = [x["attribute"] for x in content["content_tags"]]
        metadata_api["Thumb"] = [x["thumb"] for x in api_data["pages"].values()]

        if "content_publishers" in content:
            metadata_api["Publisher"] = [
                x["attribute"] for x in content["content_publishers"]
            ]

        if "content_direction" in content:
            metadata_api["Direction"] = content["content_direction"]

        if "Artist" in metadata_api:
            artist = many_to_one(metadata_api["Artist"])
        else:
            artist = None
        log.debug(artist)

        if "Title" in metadata_api:
            title = fix_filename(metadata_api["Title"])
        else:
            title = None
        log.debug(title)

        if "Circle" in metadata:
            circle = many_to_one(metadata["Circle"])
        else:
            circle = None
        log.debug(circle)

        if "Magazine" in metadata:
            extra = metadata["Magazine"]
            # remove New Illustration from name because it's not a magazine
            if "New Illustration" in extra:
                extra.remove("New Illustration")
            extra = many_to_one(extra)
        else:
            extra = None

        if extra:
            extra = f" ({extra})"
        else:
            extra = " (FAKKU)"
        log.debug(extra)

        if "Direction" in metadata_api:
            direction = metadata_api["Direction"]
        else:
            direction = "Right to Left"
        log.debug(direction)

        if artist:
            if circle:
                folder_title = f"[{circle} ({artist})] {title}{extra}"
            else:
                folder_title = f"[{artist}] {title}{extra}"
        elif circle:
            folder_title = f"[{circle}] {title}{extra}"
        else:
            folder_title = f"{title}{extra}"

        manga_folder = os.sep.join([self.root_manga_dir, folder_title])
        if not os.path.exists(manga_folder):
            os.mkdir(manga_folder)
        log.debug(manga_folder)

        response_folder = os.sep.join([self.root_response_dir, folder_title])
        if not os.path.exists(response_folder):
            os.mkdir(response_folder)

        return metadata_api, manga_folder, response_folder, direction

    def _download_page(
        self,
        url: str,
        key: list[int] | None = None,
    ) -> tuple[bytes, bytes, float, str]:
        resp = self.session.get(
            url,
            timeout=self.timeout,
            headers={
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        lmt = parse(resp.headers["Last-Modified"]).astimezone().timestamp()
        file_ext = resp.headers["Content-Type"].split("/")[-1].replace("jpeg", "jpg")

        if key is None:
            return resp.content, resp.content, lmt, file_ext

        reordered = shuffle_array(key, key.pop())

        xor = reordered[2]
        width = reordered[0] ^ xor
        height = reordered[1] ^ xor

        log.debug(f"Image: {width}x{height}, seed {xor}")

        is_horizontal = width > height
        if is_horizontal:
            smaller_edge = height
        else:
            smaller_edge = width
        offset = 128 * ceil(smaller_edge / 128) - smaller_edge
        width_pieces = ceil(width / 128)
        height_pieces = ceil(height / 128)

        with Image.open(BytesIO(resp.content)) as image:
            out = Image.new("RGB", (width, height))

            piece_order = randomize(list(range(width_pieces * height_pieces)), xor)
            log.debug(f"Piece order: {piece_order}")

            for index, value in enumerate(piece_order):
                sx_piece = value % width_pieces
                sy_piece = (value - sx_piece) // width_pieces
                dx_piece = index % width_pieces
                dy_piece = (index - dx_piece) // width_pieces

                if is_horizontal:
                    last_piece = dy_piece == height_pieces - 1
                else:
                    last_piece = dx_piece == width_pieces - 1

                dx = dx_piece * 128
                dy = dy_piece * 128

                if last_piece:
                    dx -= 0 if is_horizontal else offset
                    dy -= offset if is_horizontal else 0

                sx = sx_piece * 128
                sy = sy_piece * 128

                out.paste(image.crop((sx, sy, sx + 128, sy + 128)), (dx, dy))

            out_bytes = BytesIO()
            out.save(out_bytes, "PNG", quality=100, optimize=True)
            out_bytes.seek(0)

            return resp.content, out_bytes.read(), lmt, "png"

    def _is_gallery_available(self, doc) -> bool:
        elem = doc.select_one('a[class^="button-green"]')
        if elem is None or "Start Reading" not in elem.text:
            return False
        return True

    def load_all(self):
        log.debug("Starting main downloader function")

        if not os.path.exists(self.root_manga_dir):
            os.mkdir(self.root_manga_dir)
        if not os.path.exists(self.root_response_dir):
            os.mkdir(self.root_response_dir)

        urls_processed = 0
        for url in self.urls:
            log.info(url)

            resp = self.session.get(url, timeout=self.timeout)
            doc = BeautifulSoup(resp.text, "lxml")

            log.debug("Checking if gallery is available, green button")
            if not self._is_gallery_available(doc):
                log.info(f"Gallery is not available: {url}")
                urls_processed += 1
                continue

            metadata = self.get_page_metadata(doc)

            chapter_id = url.split("/")
            if url.endswith("/"):
                chapter_id = chapter_id[-2]
            else:
                chapter_id = chapter_id[-1]

            log.info(f'Downloading "{chapter_id}" manga.')

            # Needed for cookies
            read_resp = self.session.get(f"{url}/read/page/1", timeout=self.timeout)
            if "You do not have access to this content." in read_resp.text:
                log.info(f"You do not have access to this content: {url}")
                urls_processed += 1
                continue

            api_resp = self.session.get(
                f"{API_URL}/hentai/{chapter_id}/read",
                timeout=self.timeout,
                headers={
                    "Accept": "*/*",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-site",
                },
            )

            try:
                api_data = api_resp.json()
            except requests.exceptions.JSONDecodeError:
                log.info(f"Failed to decode JSON: {url}")
                urls_processed += 1
                continue

            (
                metadata_api,
                manga_folder,
                response_folder,
                direction,
            ) = self.get_api_metadata(metadata, api_data)

            for k, v in metadata_api.items():
                metadata[k] = v
            log.debug(metadata)

            if self.keep_response:
                api_dest = os.path.join(response_folder, "api.json")
                with open(api_dest, "w", encoding="utf-8") as f:
                    json.dump(api_data, f, indent=True, ensure_ascii=False)

            keys: dict[str, list[int]] = {}
            if "key_hash" in api_data:
                data = decode_xor_cipher(
                    calculate_decryption_key(
                        api_data["key_hash"], self.session.cookies.get("fakku_zid")
                    ),
                    b64decode(api_data["key_data"]),
                ).decode("utf-8")
                keys = json.loads(data)

            page_digits = len(str(metadata["Pages"]))
            padd = max(2, page_digits)

            spreads = dict()
            for spread in api_data["spreads"]:
                left = str(spread[0])
                right = str(spread[-1])
                if left == right:
                    continue
                else:
                    spreads[right] = (left, right)

            with tqdm(
                total=len(api_data["pages"].values()),
                desc="Working...",
                unit="page",
                leave=False,
                position=0,
            ) as pbar, ThreadPoolExecutor(max_workers=5) as executor:

                def worker(idx: str, page: dict):
                    num = page["page"]
                    image_url = page["image"]

                    raw, image, lmt, ext = self._download_page(image_url, keys.get(idx))

                    filename = f"{num:0{padd}d}.{ext}"

                    if self.keep_response:
                        resp_dest = os.path.join(response_folder, filename)
                        with open(resp_dest, "wb") as f:
                            f.write(raw)
                        os.utime(resp_dest, (lmt, lmt))

                    dest = os.path.join(manga_folder, filename)
                    with open(dest, "wb") as f:
                        f.write(image)
                    os.utime(dest, (lmt, lmt))

                    page["image_path"] = dest

                futures = [
                    executor.submit(worker, idx, page)
                    for idx, page in api_data["pages"].items()
                ]

                for future in concurrent.futures.as_completed(futures):
                    pbar.update()
                    future.result()

            for spread in tqdm(spreads.values(), desc="Joining spreads", unit="spread"):
                left, right = spread

                fin_img = [
                    api_data["pages"][left]["image_path"],
                    api_data["pages"][right]["image_path"],
                ]
                im_l = fin_img[0]
                im_r = fin_img[1]

                nam_l = im_l.split(os.sep)[-1].split(".")[0]
                ext_l = im_l.split(os.sep)[-1].split(".")[-1]
                nam_r = im_r.split(os.sep)[-1].split(".")[0]
                ext_r = im_r.split(os.sep)[-1].split(".")[-1]

                spread_name = nam_l + "-" + nam_r
                destination_file_spread = os.sep.join(
                    [manga_folder, f"{spread_name}a.png"]
                )

                combo = append_images(
                    fin_img,
                    direction="horizontal",
                    alignment="none",
                    src_type="scrambled" if "key_hash" in api_data else "unscrambled",
                    dirc=direction,
                )
                combo.save(destination_file_spread)
                im_r_mt = os.path.getmtime(im_r)
                os.utime(destination_file_spread, (im_r_mt, im_r_mt))

                api_data["pages"][left][
                    "image_path"
                ] = destination_file_l = os.sep.join(
                    [manga_folder, f"{nam_l}b.{ext_l}"]
                )
                api_data["pages"][right][
                    "image_path"
                ] = destination_file_r = os.sep.join(
                    [manga_folder, f"{nam_r}c.{ext_r}"]
                )

                shutil.move(im_l, destination_file_l)
                shutil.move(im_r, destination_file_r)

            if self.optimize:
                log.info("Optimizing images")
                subprocess.call(
                    [
                        "pingo",
                        "-s9",
                        "-strip",
                        "-noconversion",
                        "-notime",
                        manga_folder,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            if self.save_metadata != "none":
                metd = OrderedDict()
                sorted_d = sorted(metadata.items(), key=lambda x: x[0])
                for sd in sorted_d:
                    sdd = sd[1]
                    if type(sdd) is list and len(sdd) == 1:
                        sdd = sd[1][0]
                    metd[sd[0]] = sdd

                log.debug("Dumping metadata in info.json file")
                json_info_file = os.sep.join(
                    [
                        manga_folder,
                        "info.json",
                    ]
                )
                with open(json_info_file, "w", encoding="utf-8") as f:
                    json.dump(metd, f, indent=True, ensure_ascii=False)

            if self.zip:
                log.debug("Creating a cbz and deleting the image folder after creation")
                archive_name = shutil.make_archive(chapter_id, "cbz", manga_folder)
                shutil.move(archive_name, self.root_manga_dir)
                shutil.rmtree(manga_folder)

            if not self.keep_response:
                shutil.rmtree(response_folder)

            with open(self.done_file, "a") as done_file_obj:
                done_file_obj.write(f"{url}\n")
            urls_processed += 1
            log.debug("Finished parsing page")
            sleep(self.wait)
        log.info(f"Urls processed: {urls_processed}")
