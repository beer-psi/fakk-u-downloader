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

import curl_cffi
import lxml.builder
import lxml.etree
from bs4 import BeautifulSoup
from PIL import Image
from tqdm import tqdm

from consts import (
    BASE_URL,
    LANG_MAP,
    API_URL,
    URLS_FILE,
    DONE_FILE,
    COOKIES_FILE,
    ROOT_RESPONSE_DIR,
    ROOT_MANGA_DIR,
    TIMEOUT,
    WAIT,
    ZIP,
    OPTIMIZE,
)
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
E = lxml.builder.ElementMaker()


class DescrambleDownloader:
    """Class for downloading galleries.

    The idea is simple, we download the images and descramble locally, removing the need
    for any selenium bullshit.
    """

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
        self.urls, self.done_urls = get_urls_list(urls_file, done_file)
        self.root_manga_dir = root_manga_dir
        self.root_response_dir = root_response_dir

        self.save_metadata = save_metadata

        self.timeout = timeout
        self.wait = wait

        self.zip = _zip

        self.keep_response = response

        if optimize:
            if shutil.which("pingo") is not None:
                self.optimize = "pingo"
            elif shutil.which("ect") is not None:
                self.optimize = "ect"
            else:
                log.warning("Pingo/ECT not found, disabling optimization")
                self.optimize = None

        self.cookie_jar = cookiejar.MozillaCookieJar(cookies_file)
        self.cookie_jar.load()

        self.session = curl_cffi.Session(
            cookies=self.cookie_jar, proxy=proxy, impersonate="chrome"
        )
        self.session.headers.update(
            {
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
                "DNT": "1",
            }
        )

    def add_done_url(self, url: str):
        self.done_urls.add(url)

        with open(self.done_file, "a") as done_file_obj:
            done_file_obj.write(f"{url}\n")

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

        manga_folder = os.path.join(self.root_manga_dir, folder_title)
        if not os.path.exists(manga_folder):
            os.mkdir(manga_folder)
        log.debug(manga_folder)

        response_folder = os.path.join(self.root_response_dir, folder_title)
        if not os.path.exists(response_folder):
            os.mkdir(response_folder)

        return metadata_api, manga_folder, response_folder, direction

    def _download_page(
        self,
        url: str,
        key: list[int] | None = None,
    ) -> tuple[bytes, str, bytes, str]:
        resp = self.session.get(
            url,
            headers={
                "accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
                "connection": "keep-alive",
                "sec-fetch-dest": "image",
                "sec-fetch-mode": "no-cors",
                "sec-fetch-site": "same-site",
            },
        )

        content = resp.content

        with Image.open(BytesIO(content)) as image:
            if image.format is None:
                log.warning(f"Image is of unknown type: {url}")
                raw_ext = "bin"
            elif image.format == "JPEG":
                raw_ext = "jpg"
            else:
                raw_ext = image.format.lower()

            if key is None or len(key) == 0:
                return content, raw_ext, content, raw_ext

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

            return content, raw_ext, out_bytes.read(), "png"

    def _is_gallery_available(self, doc) -> str | None:
        for elem in doc.select('a[class^="button-green"]'):
            if "Start Reading" in elem.text:
                return elem["href"]

        return None

    def _build_comicinfo_xml(self, metadata: dict) -> bytes:
        if isinstance(metadata["Artist"], list):
            artist = ", ".join(metadata["Artist"])
        else:
            artist = metadata["Artist"]

        doc: lxml.etree.Element = E.ComicInfo(
            E.Title(metadata["Title"]),
            E.Penciller(artist),
            E.Summary(metadata["Description"]),
            E.LanguageISO(LANG_MAP[metadata["Language"]]),
            E.PageCount(metadata["Pages"]),
            E.Web(metadata["URL"]),
            E.Genre(", ".join(metadata["Tags"])),
            E.Publisher(metadata["Publisher"]),
            E.Manga("Yes"),
        )

        if "Full Color" in metadata["Tags"]:
            doc.append(E.BlackAndWhite("No"))
        else:
            doc.append(E.BlackAndWhite("Yes"))

        if "Hentai" in metadata["Tags"] or "Ecchi" in metadata["Tags"]:
            doc.append(E.AgeRating("R18+"))

        return b'<?xml version="1.0" encoding="utf-8"?>\n' + lxml.etree.tostring(
            doc, pretty_print=True
        )

    def load_all(self):
        log.debug("Starting main downloader function")

        if not os.path.exists(self.root_manga_dir):
            os.mkdir(self.root_manga_dir)
        if not os.path.exists(self.root_response_dir):
            os.mkdir(self.root_response_dir)

        urls_processed = 0
        for url in self.urls:
            log.info(url)

            resp = self.session.get(
                url,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "connection": "keep-alive",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-user": "?1",
                },
            )

            doc = BeautifulSoup(resp.text, "lxml")

            log.debug("Checking if gallery is available, green button")

            href = self._is_gallery_available(doc)

            if href is None:
                log.info(f"Gallery is not available: {url}")
                urls_processed += 1
                continue

            metadata = self.get_page_metadata(doc)

            href_parts = href.split("/")

            # /hentai/{chapter_id}/read
            if href.endswith("/"):
                chapter_id = href_parts[-3]
            else:
                chapter_id = href_parts[-2]

            if f"https://www.fakku.net/hentai/{chapter_id}" in self.done_urls:
                log.info(
                    "URL redirects to a done hentai: https://www.fakku.net/hentai/%s",
                    chapter_id,
                )
                urls_processed += 1
                self.add_done_url(url)
                continue

            log.info(f'Downloading "{chapter_id}" manga.')

            resp = self.session.get(
                f"{url}/read",
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "referer": url,
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-user": "?1",
                },
            )

            if "You do not have access to this content." in resp.text:
                log.info(f"You do not have access to this content: {url}")
                urls_processed += 1
                continue

            resp = self.session.get(
                f"{API_URL}/hentai/{chapter_id}/read",
                headers={
                    "accept": "*/*",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                },
            )

            try:
                api_data = resp.json()
            except json.decoder.JSONDecodeError:
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
                fakku_zid = self.session.cookies.get(
                    name="fakku_zid", domain=".fakku.net"
                )

                if fakku_zid is None:
                    log.error(
                        "Failed to retrieve fakku_zid cookie for descrambling pages"
                    )
                    urls_processed += 1
                    continue

                data = decode_xor_cipher(
                    calculate_decryption_key(api_data["key_hash"], fakku_zid),
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

            with (
                tqdm(
                    total=len(api_data["pages"].values()),
                    desc="Working...",
                    unit="page",
                    leave=False,
                    position=0,
                ) as pbar,
                ThreadPoolExecutor(max_workers=5) as executor,
            ):

                def worker(idx: str, page: dict):
                    num = page["page"]
                    image_url = page["image"]

                    raw, raw_ext, image, ext = self._download_page(
                        image_url, keys.get(idx)
                    )

                    raw_filename = f"{num:0{padd}d}.{raw_ext}"
                    filename = f"{num:0{padd}d}.{ext}"

                    if self.keep_response:
                        resp_dest = os.path.join(response_folder, raw_filename)
                        with open(resp_dest, "wb") as f:
                            f.write(raw)

                    dest = os.path.join(manga_folder, filename)
                    with open(dest, "wb") as f:
                        f.write(image)

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

                if left not in api_data["pages"] or right not in api_data["pages"]:
                    log.warning(
                        "Requested to join non-existent pages (%s, %s), ignoring",
                        left,
                        right,
                    )
                    continue

                fin_img = [
                    api_data["pages"][left]["image_path"],
                    api_data["pages"][right]["image_path"],
                ]
                im_l = fin_img[0]
                im_r = fin_img[1]

                nam_l, ext_l = os.path.splitext(os.path.basename(im_l))
                nam_r, ext_r = os.path.splitext(os.path.basename(im_r))

                spread_name = nam_l + "-" + nam_r
                destination_file_spread = os.path.join(
                    manga_folder, f"{spread_name}a.png"
                )

                combo = append_images(
                    fin_img,
                    direction="horizontal",
                    alignment="none",
                    src_type="scrambled" if "key_hash" in api_data else "unscrambled",
                    dirc=direction,
                )
                combo.save(destination_file_spread)

                api_data["pages"][left]["image_path"] = destination_file_l = (
                    os.path.join(manga_folder, f"{nam_l}b.{ext_l}")
                )
                api_data["pages"][right]["image_path"] = destination_file_r = (
                    os.path.join(manga_folder, f"{nam_r}c.{ext_r}")
                )

                shutil.move(im_l, destination_file_l)
                shutil.move(im_r, destination_file_r)

            if self.optimize == "pingo":
                log.info("Optimizing images using pingo")
                subprocess.call(
                    [
                        "pingo",
                        "-lossless",
                        "-nostrip",
                        "-notime",
                        manga_folder,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif self.optimize == "ect":
                log.info("Optimizing images using ect")
                subprocess.call(
                    ["ect", "--mt-file", "--mt-deflate", "--strict", manga_folder],
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

                log.debug("Dumping metadata in info.json/ComicInfo.xml file")
                json_info_file = os.path.join(
                    manga_folder,
                    "info.json",
                )
                with open(json_info_file, "w", encoding="utf-8") as f:
                    json.dump(metd, f, indent=4, ensure_ascii=False)

                log.debug("Dumping ComicInfo.xml")
                comicinfo_file = os.path.join(manga_folder, "ComicInfo.xml")
                with open(comicinfo_file, "wb") as f:
                    f.write(self._build_comicinfo_xml(metd))

            if self.zip:
                log.debug("Creating a cbz and deleting the image folder after creation")
                shutil.make_archive(manga_folder, "cbz", manga_folder)
                shutil.rmtree(manga_folder)

            if not self.keep_response:
                shutil.rmtree(response_folder)

            self.add_done_url(url)
            urls_processed += 1

            log.debug("Finished parsing page")
            sleep(self.wait)

        log.info(f"Urls processed: {urls_processed}")
        self.cookie_jar.save()
