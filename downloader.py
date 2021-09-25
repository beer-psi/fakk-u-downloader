import datetime
import json
import logging
import os
import re
import secrets
import shutil
import string
import sys
import xml.etree.cElementTree as ET
from binascii import a2b_base64
from collections import OrderedDict
from gzip import decompress
from pickle import UnpicklingError
from time import sleep, time

import undetected_chromedriver as uc
from PIL import Image
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire.webdriver import Chrome
from tqdm import tqdm

log = logging.getLogger()

BASE_URL = "https://www.fakku.net"
LOGIN_URL = f"{BASE_URL}/login/"
# Initial display settings for browser. Used for grahic mode
MAX_DISPLAY_SETTINGS = [800, 600]
# Path to headless driver
if sys.platform == "win32":
    EXEC_PATH = "chromedriver.exe"
    sp_c = "\\"
else:
    EXEC_PATH = "chromedriver"
    sp_c = "/"
# File with manga urls
URLS_FILE = "urls.txt"
# File with completed urls
DONE_FILE = "done.txt"
# File with prepared cookies
COOKIES_FILE = "cookies.json"  # easy to read and edit
# Root directory for manga downloader
ROOT_MANGA_DIR = "manga"
# Root directory for original files from server response
ROOT_RESPONSE_DIR = "response"
# Timeout to page loading in seconds
TIMEOUT = 10
# Wait between page loading in seconds
WAIT = 0.1
# Max manga to download in one session (-1 == no limit)
MAX = None
# User agent for web browser
USER_AGENT = None
# Should a cbz archive file be created
ZIP = False
# script version
version = "v0.0.9"

# create script tag to put in html body/head
js_name_todata = secrets.choice(string.ascii_letters) + "".join(
    secrets.choice(string.ascii_letters + string.digits) for _ in range(9)
)
js_script_in = """
<script>var s = document.createElement('script');
s.type = 'text/javascript';
var code = "HTMLCanvasElement.%s = HTMLCanvasElement.prototype.toDataURL;";
try {
      s.appendChild(document.createTextNode(code));
    } catch (e) {
      s.text = code;
}
s.onload = function() {
    this.remove();
};
(document.body || document.documentElement).appendChild(s);
(document.head || document.documentElement).appendChild(s);</script>
""" % (
    js_name_todata,
)
js_script_in = js_script_in.encode()


def append_images(
    imgs,
    direction="horizontal",
    bg_color=(255, 255, 255),
    aligment="center",
    src_type=None,
    dirc=None,
):
    """
    Appends images in horizontal/vertical direction. Used for joining spreads.

    Args:
        imgs: List of PIL images
        direction: direction of concatenation, 'horizontal' or 'vertical'
        bg_color: Background color (default: white)
        aligment: alignment mode if images need padding;
           'left', 'right', 'top', 'bottom', or 'center'

    Returns:
        Concatenated image as a new PIL image object.
    """
    log.debug("Joining spreads")
    if dirc == "Left to Right":
        pass
    else:
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

    if src_type == "scrambled":
        new_im = Image.new("RGBA", (new_width, new_height), color=bg_color)
    else:
        new_im = Image.new("RGB", (new_width, new_height), color=bg_color)

    if type(imgs[0]) is str:
        images = map(Image.open, imgs)
    else:
        images = imgs
    offset = 0
    for im in images:
        if direction == "horizontal":
            y = 0
            if aligment == "center":
                y = int((new_height - im.size[1]) / 2)
            elif aligment == "bottom":
                y = new_height - im.size[1]
            new_im.paste(im, (offset, y))
            offset += im.size[0]
        else:
            x = 0
            if aligment == "center":
                x = int((new_width - im.size[0]) / 2)
            elif aligment == "right":
                x = new_width - im.size[0]
            new_im.paste(im, (x, offset))
            offset += im.size[1]

    return new_im


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
        rstr = r'[\\:*?"<>|]+'
        filename = re.sub(rstr, "_", filename)  # Replace with underscore
    return filename


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


def comicinfo_writer(info_meta, api_meta, file_path):
    log.debug("Creating xml tree")
    r = ET.Element("ComicInfo")

    ET.SubElement(r, "Title").text = api_meta["content"]["content_name"]
    ET.SubElement(r, "Web").text = api_meta["content"]["content_url"]
    ET.SubElement(r, "PageCount").text = str(api_meta["content"]["content_pages"])
    ET.SubElement(r, "Summary").text = api_meta["content"]["content_description"]
    if "Circle" in info_meta:
        if type(info_meta["Circle"]) is list:
            info_meta["Circle"] = ", ".join(info_meta["Circle"])
        if type(info_meta["Artist"]) is list:
            info_meta["Artist"] = ", ".join(info_meta["Artist"])
        ET.SubElement(r, "Writer").text = ", ".join(
            (info_meta["Artist"], info_meta["Circle"])
        )
    else:
        if type(info_meta["Artist"]) is list:
            info_meta["Artist"] = ", ".join(info_meta["Artist"])
        ET.SubElement(r, "Writer").text = info_meta["Artist"]
    # there is no good way of getting gallery date (ok there is but it's rss)
    # use timestamp from thumb if it fails use current time
    try:
        timestamp = int(api_meta["pages"]["1"]["thumb"].split("/")[-3].split("_")[-1])
    except:
        timestamp = None
    try:
        timestamp = int(api_meta["pages"]["1"]["thumb"].split("/")[-3].split("-")[-1])
    except:
        timestamp = None
    if not timestamp:
        timestamp = int(time())
    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    ET.SubElement(r, "Year").text = dt.strftime("%Y")
    ET.SubElement(r, "Month").text = dt.strftime("%m")
    ET.SubElement(r, "Day").text = dt.strftime("%d")

    tgs = []
    for t in api_meta["content"]["content_tags"]:
        tgs.append(t["attribute"])
    # it should be Tags but Komga lacks support for Tags element
    ET.SubElement(r, "Genre").text = ", ".join(tgs)
    # needed when using Genre
    ET.SubElement(r, "Series").text = api_meta["content"]["content_name"]
    # ET.SubElement(r, "Series").text = 'FAKKU! Unlimited'
    # ET.SubElement(r,"Genre").text = 'Hentai'

    if type(info_meta["Publisher"]) is list:
        info_meta["Publisher"] = ", ".join(info_meta["Publisher"])
    ET.SubElement(r, "Publisher").text = info_meta["Publisher"]
    ET.SubElement(r, "Manga").text = "YesAndRightToLeft"
    ET.SubElement(r, "LanguageISO").text = "en"
    ET.SubElement(r, "AgeRating").text = "X18+"
    sa = []
    if "Event" in info_meta:
        if type(info_meta["Event"]) is list:
            info_meta["Event"] = ", ".join(info_meta["Event"])
        sa.append(info_meta["Event"])
    if "Parody" in info_meta:
        if type(info_meta["Parody"]) is list:
            info_meta["Parody"] = ", ".join(info_meta["Parody"])
        sa.append(info_meta["Parody"])
    if "Magazine" in info_meta:
        if type(info_meta["Magazine"]) is list:
            info_meta["Magazine"] = ", ".join(info_meta["Magazine"])
        sa.append(info_meta["Magazine"])
    if "Collections" in info_meta:
        for col in info_meta["Collections"].keys():
            sa.append(col)
    if len(sa) > 0:
        # it should be SeriesGroup but Komga treats that field as single string instead of ',' separated list
        ET.SubElement(r, "StoryArc").text = ", ".join(sa)
    log.debug("Writing xml to file")
    a = ET.ElementTree(r)
    ET.indent(a, space="\t", level=0)
    a.write(file_path, encoding="utf-8", xml_declaration=True)


class JewcobDownloader:
    """
    Class which allows download manga.
    The main idea of download - using headless browser and saving both
    original images from fakku server responses and images rendered on html canvas
    with simple javascript injection and .toDataURL method
    ignoring shitty low quality screenshots
    ignoring bloated jpgs pretending to be png
    (original server jpg with quality 80-90 is better than bloated png created from that jpg)
    """

    def __init__(
        self,
        urls_file=URLS_FILE,
        done_file=DONE_FILE,
        cookies_file=COOKIES_FILE,
        root_manga_dir=ROOT_MANGA_DIR,
        root_response_dir=ROOT_RESPONSE_DIR,
        driver_path=EXEC_PATH,
        default_display=MAX_DISPLAY_SETTINGS,
        timeout=TIMEOUT,
        wait=WAIT,
        login=None,
        password=None,
        _max=MAX,
        _zip=ZIP,
        save_metadata=True,
        comicinfo=False,
    ):
        """
        param: urls_file -- string name of .txt file with urls
            Contains list of manga urls, that's to be downloaded
        param: done_file -- string name of .txt file with urls
            Contains list of manga urls that have successfully been downloaded
        param: cookies_file -- string name of .picle file with cookies
            Contains binary data with cookies
        param: driver_path -- string
            Path to the browser driver
        param: default_display -- list of two int (width, height)
            Initial display settings. After loading the page, they will be changed
        param: timeout -- float
            Timeout upon waiting for first page to load
        param: wait -- float
            Wait in seconds beetween pages downloading.
        param: login -- string
            Login or email for authentication
        param: password -- string
            Password for authentication
        """
        self.urls_file = urls_file
        self.urls = self.__get_urls_list(urls_file, done_file)
        self.done_file = done_file
        self.cookies_file = cookies_file
        self.root_manga_dir = root_manga_dir
        self.root_response_dir = root_response_dir
        self.driver_path = driver_path
        self.browser = None
        self.default_display = default_display
        self.timeout = timeout
        self.wait = wait
        self.login = login
        self.password = password
        self.max = _max
        self.zip = _zip
        self.save_metadata = save_metadata
        self.comicinfo = comicinfo
        self.fakku_json = {}
        self.type = None
        self.done = 0

    def init_browser(self, auth=False, gui=False):
        """
        Initializing browser and authenticate if necessary
        Obfuscation with undetected-chromedriver
        ---------------------
        param: auth -- bool
            If True: launch browser with GUI (for first time authentication)
            If False: skip authentication
        param: headless -- bool
            If True: launch browser in headless mode
            If False: launch browser with GUI
        """
        log.info("Initializing browser")
        if auth:
            self.__auth()

        uc._Chrome = Chrome
        # don't check for chromedriver update at every run
        if os.path.exists(self.driver_path):
            uc.TARGET_VERSION = 1
        Chromed = uc.Chrome
        ChromeOptions = uc.ChromeOptions

        options = ChromeOptions()
        if not gui:
            options.headless = True
            options.add_argument("--headless")
        else:
            options.headless = False
        # set options to avoid cors and other bullshit
        options.add_argument("disable-web-security")

        if log.level == 10:
            sce = False
        else:
            sce = True
        seleniumwire_options = {
            "suppress_connection_errors": sce,
        }

        self.browser = Chromed(
            executable_path=self.driver_path,
            chrome_options=options,
            seleniumwire_options=seleniumwire_options,
        )
        self.browser.header_overrides = {"Accept-Encoding": "identity"}

        self.browser.set_script_timeout(self.timeout)
        self.browser.set_page_load_timeout(self.timeout)

        if gui:
            self.browser.set_window_size(*self.default_display)
        self.browser.response_interceptor = self.interceptor
        self.browser.scopes = [
            ".*books.fakku.net/.*",
            ".*fakku.net/hentai/.*/read/page/.*",
        ]

        self.__set_cookies()
        log.info("Browser initialized")

    def __set_cookies(self):
        """
        Changes local storage reader options and loads cookies from json file
        """
        log.debug("Loading cookies")
        self.waiting_loading_page(LOGIN_URL, is_reader_page=False)
        # set fakku local storage options
        # UI Control Direction for Right to Left Content: Right to Left
        # Read in Either Direction on First Page: Unticked
        # Page Display Mode: Singles Pages Only
        # Page Scaling: Original Size
        # Fit to Width if Overwidth: Unticked
        # Background Color: Gray
        # But Not When Viewing Two Pages: Unticked
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-uiControlDirection','rtl');"
            "window.localStorage.setItem('fakku-uiFirstPageControlDirectionFlip','false');"
            "window.localStorage.setItem('fakku-twoPageMode','0');"
            "window.localStorage.setItem('fakku-pageScalingMode','none');"
            "window.localStorage.setItem('fakku-fitIfOverWidth','false');"
            "window.localStorage.setItem('fakku-backgroundColor','#7F7B7B');"
            "window.localStorage.setItem('fakku-suppressWidthFitForSpreads','false');"
        )
        time_now = time()
        with open(self.cookies_file, "rb") as f:
            cookies = json.load(f)
            for cookie in cookies:
                cookie["expiry"] = int(cookie["expiry"])
                if cookie["name"] in {"fakku_sid", "fakku_zid"}:
                    if cookie["expiry"] < time_now:
                        logging.info("Expired cookies")
                        logging.info("Remove cookies.json and try again")
                        self.program_exit()
                self.browser.add_cookie(cookie)

    def __auth(self):
        """
        Authentication in browser with GUI for saving cookies in first time
        """
        log.debug("Authentication")
        uc._Chrome = Chrome
        Chromed = uc.Chrome
        ChromeOptions = uc.ChromeOptions
        options = ChromeOptions()
        options.headless = False
        self.browser = Chrome(executable_path=self.driver_path, chrome_options=options)
        self.browser.set_window_size(*self.default_display)
        self.browser.get(LOGIN_URL)
        # it probably doesn't work but at least it won't throw an exception
        try:
            h1 = self.browser.find_element(By.CSS_SELECTOR, "h1")
            h1 = h1.get_property("textContent")
            if "One more step" in h1:
                ready = input("Tab Enter to continue after you solved the captcha...")
        except NoSuchElementException:
            pass

        if not self.login is None:
            self.browser.find_element(By.ID, "username").send_keys(self.login)
        if not self.password is None:
            self.browser.find_element(By.ID, "password").send_keys(self.password)
        self.browser.find_element(By.CSS_SELECTOR, 'button[class*="js-submit"]').click()

        ready = input("Tab Enter to continue after you login...")
        with open(self.cookies_file, "w") as f:
            json.dump(self.browser.get_cookies(), f, indent=True)

        self.browser.quit()

    def program_exit(self):
        logging.info("Program exit.")
        self.browser.quit()
        exit()

    def interceptor(self, request, response):  # A response interceptor takes two args
        """
        Modifies response body by adding script tag, javascript injection
        """
        if "fakku.net/hentai/" in request.url and "/read/page/" in request.url:
            # various checks to make sure we're only injecting the script on appropriate responses
            # we check that the content type is HTML and that the status code is 200
            if (
                response.headers.get_content_subtype() != "html"
                or response.status_code != 200
            ):
                logging.debug(response.headers.get_content_subtype())
                logging.debug(response.status_code)
                logging.debug(response.headers)
                logging.debug(request.url)
                return None

            if "Content-Encoding" in response.headers:
                try:
                    gzip_response = decompress(response.body)
                    response.body = gzip_response
                except Exception as err:
                    log.debug(err)
                del response.headers["Content-Encoding"]

            # modify response body
            parsed_html = response.body
            f = b"<head>"
            h_index = parsed_html.find(f) + len(f)
            html2 = parsed_html[:h_index] + js_script_in + parsed_html[h_index:]
            response.body = html2
            log.debug("Response body modified")
        elif "books.fakku.net" in request.url and "/images/" not in request.url:
            if not self.fakku_json:

                resp_body = response.body
                if "Content-Encoding" in response.headers:
                    try:
                        gzip_response = decompress(response.body)
                        resp_body = gzip_response
                    except Exception as err:
                        log.debug(err)
                body = resp_body.decode("utf-8")
                self.fakku_json = json.loads(body)

    def get_response_images(self, page, save_path, zpad):
        """
        Saves original images sended by fakku server, scrambled and unscrambled
        """
        if "response_path" not in self.fakku_json["pages"][page]:
            num = self.fakku_json["pages"][page]["page"]
            resp_url = self.fakku_json["pages"][page]["image"]
            image_path = None
            log.debug("Get response images")
            while not image_path:
                all_requests = None
                while not all_requests:
                    try:
                        all_requests = self.browser.requests
                    except UnpicklingError:
                        sleep(self.wait)
                    except EOFError:
                        sleep(self.wait)
                    except FileNotFoundError:
                        sleep(self.wait)
                for request in all_requests:
                    if request.response:
                        if request.url == resp_url:
                            resp_file_name = request.url.split("/")[-1]
                            resp_file_type = request.response.headers[
                                "Content-Type"
                            ].split("/")[-1]
                            resp_file_type = resp_file_type.replace("jpeg", "jpg")
                            resp_data = request.response.body
                            resp_destination_file = os.sep.join(
                                [
                                    save_path,
                                    f"{num:0{zpad}d}.{resp_file_type}",
                                ]
                            )
                            with open(resp_destination_file, "wb") as file:
                                file.write(resp_data)
                            image_path = resp_destination_file
                sleep(self.wait)
            self.fakku_json["pages"][page]["response_path"] = image_path

    def load_all(self):
        """
        Just main function
        open main page and first reader page, click the rest
        dumps images data urls from html canvas as .png
        """
        log.debug("Starting main downloader function")
        if not os.path.exists(self.root_manga_dir):
            os.mkdir(self.root_manga_dir)
        if not os.path.exists(self.root_response_dir):
            os.mkdir(self.root_response_dir)

        ignore_size = {
            (300, 150)
        }  # size of some hidden fakku canvas, probably top menu
        ignore_size.update(
            {(792, 515), (792, 471)}
        )  # in graphic mode, default viewport size (800x600 - stuff)
        window_size = (
            self.browser.get_window_size()
        )  # in headless mode viewport size == window size, default 800x600
        ignore_size.add((window_size["width"], window_size["height"]))

        urls_processed = 0
        for url in self.urls:
            if "fakku.net/anime/" in url or "fakku.net/games/" in url:
                logging.info(f"{url.split('fakku.net/')[-1].split('/')[0]}: {url}")
                urls_processed += 1
                continue

            logging.info(url)
            self.timeout = TIMEOUT
            self.wait = WAIT
            self.fakku_json = {}
            self.type = None
            self.done = 0

            self.waiting_loading_page(url, is_reader_page="main")

            log.debug("Checking if user is logged")
            try:
                login_check = self.browser.find_element(
                    By.CSS_SELECTOR,
                    "span.inline-block.text-base.text-white.font-normal.select-none.hover\:text-red-300",
                )
                # todo check if my account in cn
                cn = login_check.get_property("textContent")
            except:
                logging.info("You aren't logged in")
                logging.info("Probably expired cookies")
                logging.info("Remove cookies.json and try again")
                self.program_exit()

            log.debug("Checking if gallery is available, green button")
            try:
                bt = self.browser.find_element(
                    By.CSS_SELECTOR, 'a[class^="button-green"]'
                )
                if "Start Reading" not in bt.text:
                    logging.info(f"{bt.text}: {url}")
                    urls_processed += 1
                    continue
            except NoSuchElementException as err:
                logging.info(f"No green button: {url}")
                urls_processed += 1
                continue

            metadata = OrderedDict()
            if self.save_metadata != "basic":
                log.debug("Parsing right side for metadata")
                try:
                    meta0 = self.browser.find_element(
                        By.CSS_SELECTOR,
                        'div[class^="block sm:table-cell relative w-full align-top"]',
                    )
                    meta_title = meta0.find_element(By.CSS_SELECTOR, "h1")
                    meta_rows = meta0.find_elements(
                        By.CSS_SELECTOR, 'div[class^="table text-sm w-full"]'
                    )
                    log.debug("Parsing right side rows")
                    for meta_row in meta_rows:
                        try:
                            meta_row_left = meta_row.find_element(
                                By.CSS_SELECTOR,
                                'div[class^="inline-block w-24 text-left align-top"]',
                            )
                            left_text = meta_row_left.text
                        except NoSuchElementException as err:
                            continue

                        if left_text in [
                            "Artist",
                            "Parody",
                            "Publisher",
                            "Language",
                            "Pages",
                            "Direction",
                        ]:
                            continue

                        log.debug(f"Parsing {left_text}")
                        meta_row_right = meta_row.find_element(
                            By.CSS_SELECTOR,
                            'div[class^="table-cell w-full align-top text-left"]',
                        )
                        a_tags = meta_row_right.find_elements(By.CSS_SELECTOR, "a")
                        if a_tags:
                            values = []
                            for a in a_tags:
                                if a.text == "+":
                                    continue
                                values.append(a.text)
                            metadata[left_text] = values
                        else:
                            if left_text in {"Favorites"}:
                                metadata[left_text] = int(
                                    "".join(
                                        meta_row_right.text.split(" ")[0].split(",")
                                    )
                                )
                            else:
                                metadata[left_text] = meta_row_right.text
                except Exception as meta_err:
                    log.info(
                        f"Metadata parser issue right side, please report url: {url}"
                    )
                    log.info(str(meta_err))

                log.debug("Parsing left side")
                try:
                    meta1 = self.browser.find_element(
                        By.CSS_SELECTOR,
                        'div[class^="block sm:inline-block relative w-full align-top p-4 text-center space-y-4"]',
                    )
                    price_container = meta1.find_element(
                        By.CSS_SELECTOR,
                        'div[class^="rounded cursor-pointer right-0 bottom-0 m-1 sm:m-0 sm:right-2 sm:bottom-2 sm:left-auto"]',
                    )
                    try:
                        price_left = price_container.find_element(
                            By.CSS_SELECTOR,
                            'div[class^="table w-auto text-right opacity-90 hover:opacity-100 js-purchase-product"]',
                        )
                        price = price_left.find_element(By.CSS_SELECTOR, "div").text
                        price = float(price[1:])
                        log.debug(price)
                        metadata["Price"] = price
                    except NoSuchElementException as err:
                        price = None
                except Exception as meta_err:
                    log.info(
                        f"Metadata parser issue left side, please report url: {url}"
                    )
                    log.info(str(meta_err))

                log.debug("Parsing bottom")
                try:
                    meta2 = self.browser.find_element(
                        By.CSS_SELECTOR,
                        'div[class^="col-span-full block js-tab-targets"]',
                    )
                    meta2d = self.browser.find_elements(By.CSS_SELECTOR, "div")
                    for meta_rest in meta2d:
                        meta_id = meta_rest.get_attribute("id")
                        if "/related" in meta_id:
                            log.debug("Parsing Related")
                            div_book_titles = meta_rest.find_elements(
                                By.CSS_SELECTOR,
                                'div[class^="overflow-hidden relative rounded shadow-lg"]',
                            )
                            values = []
                            for dbt in div_book_titles:
                                a = dbt.find_element(By.CSS_SELECTOR, "a")
                                ah = a.get_attribute("href")
                                if ah not in values:
                                    values.append(ah)
                            if len(values) == 1:
                                values = values[0]
                            metadata["Related"] = values
                        elif "/collections" in meta_id:
                            log.debug("Parsing Collections")
                            col = meta_rest.find_element(By.CSS_SELECTOR, "em")
                            cola = col.find_element(By.CSS_SELECTOR, "a")
                            colu = cola.get_attribute("href")
                            colt = cola.get_property("textContent")
                            a_tags = meta_rest.find_element(
                                By.CSS_SELECTOR,
                                'div[class^="col-span-full w-full relative space-y-2"]',
                            )
                            a_tags = a_tags.find_elements(By.CSS_SELECTOR, "a")
                            col_dict = dict()
                            values = []
                            for a in a_tags:
                                ah = a.get_attribute("href")
                                values.append(ah)
                            if len(values) == 1:
                                values = values[0]
                            col_dict[colt] = (colu, values)
                            metadata["Collections"] = col_dict
                        elif "/chapters" in meta_id:
                            log.debug("Parsing Chapters")
                            div_chapters = meta_rest.find_elements(
                                By.CSS_SELECTOR,
                                'div[class^="table relative w-full bg-white py-2 px-4 rounded dark:bg-gray-900"]',
                            )
                            cd = OrderedDict()
                            for dc in div_chapters:
                                dcn = dc.find_element(
                                    By.CSS_SELECTOR,
                                    'div[class^="inline-block pr-2 text-right w-8 align-top text-sm"]',
                                )
                                cn = int(dcn.get_property("innerHTML"))
                                dct = dc.find_element(
                                    By.CSS_SELECTOR,
                                    'div[class^="table-cell w-full align-top text-left text-sm"]',
                                )
                                a = dct.find_element(By.CSS_SELECTOR, "a")
                                ah = a.get_attribute("href")
                                cd[cn] = ah
                            metadata["Chapters"] = cd
                        else:
                            pass
                            """
                            if self.save_metadata == "extra":
                                comments = []
                                chain = []
                                div_comments = meta_rest.find_elements(
                                    By.CSS_SELECTOR, 'div[class^="bg-white table p-4 w-full rounded space-y-2 dark:bg-gray-900"]'
                                )
                                for comment in div_comments:
                                    comment_dict = OrderedDict()
                                    comment_class = comment.get_attribute("class")
                                    #if comment_class == "comment-reply-textarea":
                                    #    continue
                                    #log.debug(comment_class)

                                    try:
                                        comment_id = comment.find_element(
                                            By.CSS_SELECTOR, "a"
                                        )
                                    except NoSuchElementException:
                                        continue
                                    comment_id = int(comment_id.get_attribute("id"))
                                    log.debug(comment_id)
                                    comment_dict["id"] = comment_id

                                    comment_rank = int(
                                        comment.find_element(
                                            By.CSS_SELECTOR, "div.rank"
                                        ).text
                                    )
                                    log.debug(comment_rank)
                                    comment_dict["rank"] = comment_rank

                                    comment_post_top = comment.find_element(
                                        By.CSS_SELECTOR, "div.post-row-top"
                                    )
                                    comment_post_username = (
                                        comment_post_top.find_element(
                                            By.CSS_SELECTOR, "a"
                                        ).text
                                    )
                                    log.debug(comment_post_username)
                                    comment_dict["username"] = comment_post_username
                                    try:
                                        comment_post_alias = (
                                            comment_post_top.find_element(
                                                By.CSS_SELECTOR, "strong"
                                            ).text
                                        )
                                        log.debug(comment_post_alias)
                                        comment_dict["alias"] = comment_post_alias
                                    except NoSuchElementException:
                                        pass
                                    comment_posted = comment_post_top.find_element(
                                        By.CSS_SELECTOR, "span"
                                    )
                                    comment_posted = comment_posted.get_attribute(
                                        "title"
                                    )
                                    log.debug(comment_posted)
                                    comment_dict["posted"] = comment_posted

                                    comment_post_body = comment.find_element(
                                        By.CSS_SELECTOR, "div.post-row-body"
                                    )
                                    try:
                                        comment_review_title = (
                                            comment_post_body.find_element(
                                                By.CSS_SELECTOR, "strong"
                                            ).text
                                        )
                                        log.debug(comment_review_title)
                                        comment_dict[
                                            "review_title"
                                        ] = comment_review_title
                                    except NoSuchElementException:
                                        pass
                                    try:
                                        comment_star_rating = (
                                            comment_post_body.find_element(
                                                By.CSS_SELECTOR, "div.star-rating"
                                            )
                                        )
                                        fs = 0
                                        for (
                                            fas
                                        ) in comment_star_rating.find_elements(
                                            By.CSS_SELECTOR, "i.fas.fa-star"
                                        ):
                                            fs += 1
                                        es = 0
                                        for (
                                            far
                                        ) in comment_star_rating.find_elements(
                                            By.CSS_SELECTOR, "i.far.fa-star"
                                        ):
                                            es += 1
                                        comment_star_rating = f"{fs}/{fs + es}"
                                        log.debug(comment_star_rating)
                                        comment_dict[
                                            "star_rating"
                                        ] = comment_star_rating
                                    except NoSuchElementException:
                                        pass
                                    try:
                                        comment_post_text = comment.find_element(
                                            By.CSS_SELECTOR,
                                            f"div[id=comment-{str(comment_id)}]",
                                        ).text
                                        log.debug(comment_post_text)
                                        comment_dict["text"] = comment_post_text
                                    except NoSuchElementException:
                                        pass

                                    try:
                                        comment_edit_time = comment.find_element(
                                            By.CSS_SELECTOR, "p"
                                        ).text
                                        log.debug(comment_edit_time)
                                        comment_dict["edited"] = comment_edit_time
                                    except NoSuchElementException:
                                        pass

                                    if (
                                        comment_class
                                        == "comment- comment-row comment-visible"
                                    ):
                                        if not chain:
                                            chain = [0, 0, 0]
                                        else:
                                            chain[0] += 1
                                            chain[1] = 0
                                            chain[2] = 0
                                    elif (
                                        comment_class
                                        == "comment-reply comment-row comment-visible"
                                    ):
                                        if not chain:
                                            chain = [0, 0, 0]
                                        else:
                                            chain[1] += 1
                                            chain[2] = 0
                                    elif (
                                        comment_class
                                        == "comment-tree comment-row comment-visible"
                                    ):
                                        if not chain:
                                            chain = [0, 0, 0]
                                        else:
                                            chain[2] += 1
                                    chain2 = tuple(chain)
                                    log.debug(chain2)
                                    comment_dict["chain"] = chain2
                                    comments.append(comment_dict)
                                metadata["Comments"] = comments
                            """
                except Exception as meta_err:
                    log.info(f"Metadata parser issue bottom, please report url: {url}")
                    log.info(str(meta_err))
                log.debug(metadata)

            page_count = 2
            log.debug(page_count)

            folder_title = url.split("/")
            if url.endswith("/"):
                folder_title = folder_title[-2]
            else:
                folder_title = folder_title[-1]

            logging.info(f'Downloading "{folder_title}" manga.')

            log.debug("First page, testing injection")
            js_test = False
            while not js_test:
                self.waiting_loading_page(f"{url}/read/page/1", is_reader_page=True)
                js_script_test = """
                var dataURL = HTMLCanvasElement.%s;
                return dataURL;
                """ % (
                    js_name_todata,
                )
                try:
                    jt = self.browser.execute_script(js_script_test)
                    # jt result should be empty dict {}
                    if type(jt) is dict:
                        js_test = True
                    else:
                        js_test = False
                        logging.info("retry")
                except JavascriptException:
                    pass
                sleep(self.wait)

            log.debug(self.fakku_json["content"].keys())

            metadata_api = OrderedDict()
            log.debug("Parsing api response for metadata")

            metadata_api["URL"] = self.fakku_json["content"]["content_url"]
            metadata_api["Title"] = self.fakku_json["content"]["content_name"]

            content_artists = []
            for a in self.fakku_json["content"]["content_artists"]:
                content_artists.append(a["attribute"])
            metadata_api["Artist"] = content_artists

            content_series = []
            for s in self.fakku_json["content"]["content_series"]:
                content_series.append(s["attribute"])
            metadata_api["Parody"] = content_series

            content_publishers = []
            for p in self.fakku_json["content"]["content_publishers"]:
                content_publishers.append(p["attribute"])
            metadata_api["Publisher"] = content_publishers

            metadata_api["Language"] = self.fakku_json["content"]["content_language"]
            metadata_api["Pages"] = self.fakku_json["content"]["content_pages"]

            content_description = self.fakku_json["content"]["content_description"]
            metadata_api["Description"] = content_description
            if "content_direction" in self.fakku_json["content"]:
                metadata_api["Direction"] = self.fakku_json["content"][
                    "content_direction"
                ]

            content_tags = []
            for t in self.fakku_json["content"]["content_tags"]:
                content_tags.append(t["attribute"])
            metadata_api["Tags"] = content_tags

            metadata_api["Thumb"] = self.fakku_json["pages"]["1"]["thumb"]

            log.debug(metadata_api)

            if "key_data" in self.fakku_json:
                self.type = "scrambled"
            else:
                self.type = "unscrambled"

            if "Artist" in metadata_api:
                artist = metadata_api["Artist"]
                for i, v in enumerate(artist):
                    artist[i] = fix_filename(v)
                if len(artist) > 2:
                    artist = "Various"
                elif len(artist) == 2:
                    artist = ", ".join(artist)
                elif len(artist) == 1:
                    artist = artist[0]
                else:
                    artist = None
            else:
                artist = None
            log.debug(artist)

            if "Title" in metadata_api:
                title = metadata_api["Title"]
                title = fix_filename(title)
            else:
                title = None
            log.debug(title)

            if "Circle" in metadata:
                circle = metadata["Circle"]
                for i, v in enumerate(circle):
                    circle[i] = fix_filename(v)
                if len(circle) > 2:
                    circle = "Various"
                elif len(circle) == 2:
                    circle = ", ".join(circle)
                elif len(circle) == 1:
                    circle = circle[0]
                else:
                    circle = None
            else:
                circle = None
            log.debug(circle)

            if "Magazine" in metadata:
                extra = metadata["Magazine"]
                # remove New Illustration from name because it's not a magazine
                if "New Illustration" in extra:
                    extra.remove("New Illustration")
                for i, v in enumerate(extra):
                    extra[i] = fix_filename(v)
                if len(extra) > 2:
                    extra = "Various"
                elif len(extra) == 2:
                    extra = ", ".join(extra)
                elif len(extra) == 1:
                    extra = extra[0]
                else:
                    extra = None
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
                    folder_title = (
                        "[" + circle + " (" + artist + ")" + "] " + title + extra
                    )
                else:
                    folder_title = "[" + artist + "] " + title + extra
            elif circle:
                folder_title = "[" + circle + "] " + title + extra
            else:
                folder_title = title + extra
            manga_folder = os.sep.join([self.root_manga_dir, folder_title])
            if not os.path.exists(manga_folder):
                os.mkdir(manga_folder)
            log.debug(manga_folder)
            manga_abs_path = os.path.abspath(manga_folder)
            response_folder = os.sep.join([self.root_response_dir, folder_title])
            if not os.path.exists(response_folder):
                os.mkdir(response_folder)

            for k, v in metadata_api.items():
                metadata[k] = v
            log.debug(metadata)

            page_count = metadata["Pages"]
            if page_count > 1000:
                padd = 4
            elif page_count > 100:
                padd = 3
            elif page_count > 10:
                padd = 2
            else:
                padd = 2

            spreads = dict()
            for spread in self.fakku_json["spreads"]:
                left = str(spread[0])
                right = str(spread[-1])
                if left == right:
                    continue
                else:
                    spreads[right] = (left, right)

            progress_bar = tqdm(
                total=page_count, desc="Working...", leave=False, position=0
            )

            for page in self.fakku_json["pages"]:

                # get page response image
                self.get_response_images(page, response_folder, padd)

                # wait untill loader hides itsefl
                WebDriverWait(self.browser, self.timeout).until(
                    EC.invisibility_of_element_located((By.CLASS_NAME, "loader"))
                )

                # wait until read notification hides
                WebDriverWait(self.browser, self.timeout).until(
                    EC.invisibility_of_element_located(
                        (By.CSS_SELECTOR, 'div[class^="ui notify-container large"]')
                    )
                )

                page_num = self.fakku_json["pages"][page]["page"]

                if self.type == "scrambled":
                    log.debug("Parsing PageView layer for canvas")
                    canvas_found = None
                    while not canvas_found:
                        try:
                            page_view = self.browser.find_element(
                                By.CSS_SELECTOR, 'div.layer[data-name="PageView"]'
                            )
                            images_canvas = page_view.find_elements(
                                By.CSS_SELECTOR, "canvas"
                            )
                            if len(images_canvas) > 0:
                                for canvas in images_canvas:
                                    widthc = canvas.size["width"]
                                    heightc = canvas.size["height"]
                                    if (widthc, heightc) not in ignore_size:
                                        canvas_found = canvas
                        except StaleElementReferenceException as err:
                            pass
                        sleep(self.wait)

                    log.debug("Get image from canvas")
                    destination_file = os.sep.join(
                        [manga_abs_path, f"{page_num:0{padd}d}.png"]
                    )
                    js_script = f"""
                    var dataURL = HTMLCanvasElement.%s.call(arguments[0], \"image/png\");
                    return dataURL;
                    """ % (
                        js_name_todata,
                    )

                    rendered_image_data_url = self.browser.execute_script(
                        js_script, canvas_found
                    )

                    response_data = a2b_base64(rendered_image_data_url.split(",")[1])

                    with open(destination_file, "wb") as f:
                        f.write(response_data)
                else:
                    log.debug("Copy image from server response")
                    resp_img = self.fakku_json["pages"][page]["response_path"]
                    ext = resp_img.split(".")[-1]
                    destination_file = os.sep.join(
                        [manga_abs_path, f"{page_num:0{padd}d}.{ext}"]
                    )
                    shutil.copy(resp_img, destination_file)
                self.fakku_json["pages"][page]["image_path"] = destination_file
                log.debug(destination_file)

                if page in spreads:
                    log.debug("Creating spread")
                    left = spreads[page][0]
                    right = spreads[page][-1]
                    fin_img = []
                    imL = self.fakku_json["pages"][left]["image_path"]
                    fin_img.append(imL)
                    imR = self.fakku_json["pages"][right]["image_path"]
                    fin_img.append(imR)

                    namL = imL.split(sp_c)[-1].split(".")[0]
                    extL = imL.split(sp_c)[-1].split(".")[-1]
                    namR = imR.split(sp_c)[-1].split(".")[0]
                    extR = imR.split(sp_c)[-1].split(".")[-1]

                    spread_name = namL + "-" + namR
                    destination_file_spread = os.sep.join(
                        [manga_abs_path, f"{spread_name}a.png"]
                    )

                    combo = append_images(
                        fin_img,
                        direction="horizontal",
                        aligment="none",
                        src_type=self.type,
                        dirc=direction,
                    )
                    combo.save(destination_file_spread)

                    destination_file_L = os.sep.join(
                        [manga_abs_path, f"{namL}b.{extL}"]
                    )
                    destination_file_R = os.sep.join(
                        [manga_abs_path, f"{namR}c.{extR}"]
                    )

                    shutil.move(imL, destination_file_L)
                    self.fakku_json["pages"][left]["image_path"] = destination_file_L
                    shutil.move(imR, destination_file_R)
                    self.fakku_json["pages"][right]["image_path"] = destination_file_R

                    log.debug(destination_file_spread)

                progress_bar.update()

                if self.done < len(self.fakku_json["pages"]):
                    log.debug("Clicking next page")
                    ui = self.browser.find_element(
                        By.CSS_SELECTOR, 'div.layer[data-name="UI"]'
                    )
                    ui.click()
                    self.done += 1
                    sleep(self.wait)
            progress_bar.close()

            # delete old requests
            del self.browser.requests

            if self.done > 0:
                if log.level == 10:
                    resp_info_file = os.sep.join([response_folder, f"fakku_data.json"])
                    cks = self.browser.get_cookies()
                    for cookie in cks:
                        if cookie["name"] in {"_c", "fakku_zid"}:
                            self.fakku_json[cookie["name"]] = cookie["value"]

                    json.dump(
                        self.fakku_json,
                        open(resp_info_file, "w", encoding="utf-8"),
                        indent=True,
                        ensure_ascii=False,
                    )

            if self.save_metadata != "none":
                log.debug("Dumping metadata in info.json file")
                json_info_file = os.sep.join(
                    [
                        manga_folder,
                        "info.json",
                    ]
                )

                metd = OrderedDict()
                sorted_d = sorted(metadata.items(), key=lambda x: x[0])
                for sd in sorted_d:
                    sdd = sd[1]
                    if type(sdd) is list:
                        if len(sdd) == 1:
                            sdd = sd[1][0]
                    metd[sd[0]] = sdd

                with open(json_info_file, "w", encoding="utf-8") as f:
                    json.dump(metd, f, indent=True, ensure_ascii=False)

                if self.comicinfo and self.save_metadata != "basic":
                    log.debug("Dumping metadata in ComicInfo.xml file")
                    comic_info_file = os.sep.join(
                        [
                            manga_folder,
                            "ComicInfo.xml",
                        ]
                    )
                    comicinfo_writer(metd, self.fakku_json, comic_info_file)

            if self.zip:
                log.debug("Creating a cbz and deleting the image folder after creation")
                archive_name = shutil.make_archive(folder_title, "cbz", manga_folder)
                shutil.move(archive_name, self.root_manga_dir)
                shutil.rmtree(manga_folder)

            if log.level != 10:
                shutil.rmtree(response_folder)

            logging.info(">> manga done!")
            with open(self.done_file, "a") as done_file_obj:
                done_file_obj.write(f"{url}\n")
            urls_processed += 1
            if self.max is not None and urls_processed >= self.max:
                break
            log.debug("Finished parsing page")
            sleep(self.wait)
        self.program_exit()

    '''
    def load_urls_from_collection(self, collection_url):
        """
        Function which records the manga URLs inside a collection
        """
        log.debug("Loading urls from collection")
        self.waiting_loading_page(collection_url, is_reader_page=False)
        page_count = self.__get_page_count_in_collection()
        with open(self.urls_file, "a") as f:
            for page_num in tqdm(range(1, page_count + 1)):
                if (
                    page_num != 1
                ):  # Fencepost problem, the first page of a collection is already loaded
                    logging.info(f"{collection_url}/page/{page_num}")
                    self.waiting_loading_page(
                        f"{collection_url}/page/{page_num}", is_reader_page=False
                    )

                try:
                    all_pages_book = self.browser.find_elements(
                        By.CSS_SELECTOR, "a.book-title"
                    )
                    for a in all_pages_book:
                        href = a.get_attribute("href")
                        f.write(f"{href}\n")
                except NoSuchElementException as err:
                    pass
                try:
                    all_pages_content = self.browser.find_elements(
                        By.CSS_SELECTOR, "a.content-title"
                    )
                    for a in all_pages_content:
                        href = a.get_attribute("href")
                        f.write(f"{href}\n")
                except NoSuchElementException as err:
                    pass

    def __get_page_count_in_collection(self):
        """
        Get count of collection pages from html code
        ----------------------------
        param: page_source -- string
            String that contains html code
        return: int
            Number of collection pages
        """
        log.debug("Getting page count in collection")
        page_count = None
        while not page_count:
            try:
                pagination = self.browser.find_element(By.CLASS_NAME, "pagination-meta")
                pagination_text = pagination.text
                page_count = int(
                    re.search(r"Page\s+\d+\s+of\s+(\d+)", pagination_text).group(1)
                )
            except NoSuchElementException:
                pass
            sleep(self.wait)

        return page_count
    '''

    def __get_urls_list(self, urls_file, done_file):
        """
        Get list of urls from .txt file
        --------------------------
        param: urls_file -- string
            Name or path of .txt file with manga urls
        param: done_file -- string
            Name or path of .txt file with successfully downloaded manga urls
        return: urls -- list
            List of urls from urls_file
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
                if clean_line not in done and clean_line not in urls:
                    urls.append(clean_line)
        log.debug(f"Urls: {len(urls)}")
        if len(urls) == 0:
            log.info("Nothing to rip")
            exit()
        return urls

    def waiting_loading_page(self, url, is_reader_page=None):
        """
        Awaiting while page will load
        ---------------------------
        param: is_non_reader_page -- bool
            False -- awaiting of main manga page
            True -- awaiting of others manga pages
        """
        log.debug("Loading url")
        while True:
            try:
                self.browser.get(url)
                break
            except TimeoutException as err:
                sleep(self.wait)

        if not is_reader_page:
            elem_xpath = "//link[@type='image/x-icon']"
        else:
            if is_reader_page == "main":
                elem_xpath = "//div[contains(@class, 'group flex-1 relative align-top px-4 hidden sm:inline-block')]"
            else:
                elem_xpath = "//div[@data-name='PageView']"
        elm_found = False
        tried = 0
        tried_2 = 0
        log.debug("Waiting for element")
        while not elm_found:
            try:
                element = EC.presence_of_element_located((By.XPATH, elem_xpath))
                elm_found = WebDriverWait(self.browser, self.timeout).until(element)
            except TimeoutException as err:
                try:
                    title = self.browser.find_element(By.TAG_NAME, "h1")
                    title = title.text
                    if "FAKKU is temporarily down for maintenance." in title:
                        logging.info("FAKKU is temporarily down for maintenance.")
                        self.program_exit()
                except NoSuchElementException as err2:
                    pass
                logging.info(
                    "\nError: timed out waiting for page to load. Timeout increased +10 for more delaying."
                )
                self.timeout += 10
                self.browser.refresh()
            if tried_2 > 10:
                logging.info("Connection issues, Try again")
                self.program_exit()
            elif tried > 2:
                logging.info("\nSome connection issues, refreshing page")
                self.browser.refresh()
                tried = 0
            else:
                tried += 1
                tried_2 += 1
            logging.debug("Sleeping")
            sleep(self.wait)
