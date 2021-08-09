import json
import logging
import os
import re
import secrets
import shutil
import string
import sys
from binascii import a2b_base64
from collections import OrderedDict
from io import BytesIO
from pickle import UnpicklingError
from time import sleep

import undetected_chromedriver as uc
import urllib3.response
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

log = logging.getLogger(__name__)

BASE_URL = "https://www.fakku.net"
LOGIN_URL = f"{BASE_URL}/login/"
# Initial display settings for browser. Used for grahic mode
MAX_DISPLAY_SETTINGS = [800, 600]
# Path to headless driver
if sys.platform == "win32":
    EXEC_PATH = "chromedriver.exe"
else:
    EXEC_PATH = "chromedriver"
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
version = "v0.0.4"

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


def program_exit():
    logging.info("Program exit.")
    exit()


def append_images(
    imgs, direction="horizontal", bg_color=(255, 255, 255), aligment="center"
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
    log.debug("Fixing filename")
    filename = filename.replace("\n", "")
    filename = filename.replace("\r", "")
    filename = filename.replace("\t", "")
    filename = filename.replace("/", "⁄")
    if sys.platform == "win32":
        rstr = r'[\\:*?"<>|]+'
        filename = re.sub(rstr, "_", filename)  # Replace with underscore
    return filename


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

    def init_browser(self, auth=False, headless=True):
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
        log.debug("Initializing browser")
        if auth:
            self.__auth()

        uc._Chrome = Chrome
        # don't check for chromedriver update at every run
        if os.path.exists(self.driver_path):
            uc.TARGET_VERSION = 1
        Chromed = uc.Chrome
        ChromeOptions = uc.ChromeOptions

        options = ChromeOptions()
        if headless:
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
        self.browser.set_script_timeout(self.timeout)
        self.browser.set_page_load_timeout(self.timeout)

        if not headless:
            self.browser.set_window_size(*self.default_display)
        self.browser.response_interceptor = self.interceptor
        self.browser.scopes = [
            ".*books.fakku.net/images/.*",
            ".*fakku.net/hentai/.*/read/page/.*",
        ]

        self.__set_cookies()

    def __set_cookies(self):
        """
        Changes local storage reader options and loads cookies from json file
        """
        log.debug("Loading cookies")
        self.waiting_loading_page(LOGIN_URL, is_reader_page=False)
        # set fakku local storage options
        # Page Display Mode: Singles with Spreads
        # Page Scaling: Original Size
        # Fit to Width if Overwidth: Unticked
        # Background Color: Gray
        # But Not When Viewing Two Pages: Unticked
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-twoPageMode','1');"
            "window.localStorage.setItem('fakku-pageScalingMode','none');"
            "window.localStorage.setItem('fakku-fitIfOverWidth','false');"
            "window.localStorage.setItem('fakku-backgroundColor','#7F7B7B');"
            "window.localStorage.setItem('fakku-suppressWidthFitForSpreads','false');"
        )
        with open(self.cookies_file, "rb") as f:
            cookies = json.load(f)
            for cookie in cookies:
                if "expiry" in cookie:
                    cookie["expiry"] = int(cookie["expiry"])
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
        if not self.login is None:
            self.browser.find_element(By.ID, "username").send_keys(self.login)
        if not self.password is None:
            self.browser.find_element(By.ID, "password").send_keys(self.password)
        self.browser.find_element(By.CSS_SELECTOR, 'button[class*="js-submit"]').click()

        ready = input("Tab Enter to continue after you login...")
        with open(self.cookies_file, "w") as f:
            json.dump(self.browser.get_cookies(), f, indent=True)

        self.browser.quit()

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
                # let urlib3.response take care of response.body encoding
                urlib3_response = urllib3.response.HTTPResponse(
                    reason=response.reason,
                    headers=response.headers,
                    body=BytesIO(response.body),
                )
                response.body = urlib3_response.data
                del response.headers["Content-Encoding"]

            # modify response body
            parsed_html = response.body
            f = b"<head>"
            h_index = parsed_html.find(f) + len(f)
            html2 = parsed_html[:h_index] + js_script_in + parsed_html[h_index:]
            response.body = html2
            log.debug("Response body modified")

    def get_response_images(self, img_num, save_path, zpad):
        """
        Saves original images sended by fakku server, scrambled and unscrambled
        """
        log.debug("Get response images")
        while len(list(self.resp_done.values())) < int(img_num):
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
                    if request.url.startswith("https://books.fakku.net/images/manga"):
                        if request.url not in self.resp_done:
                            resp_file_name = request.url.split("/")[-1]
                            resp_file_type = request.response.headers[
                                "Content-Type"
                            ].split("/")[-1]
                            resp_file_type = resp_file_type.replace("jpeg", "jpg")
                            resp_data = request.response.body
                            resp_destination_file = os.sep.join(
                                [
                                    save_path,
                                    f"{self.resp_page:0{zpad}d}.{resp_file_type}",
                                ]
                            )
                            with open(resp_destination_file, "wb") as file:
                                file.write(resp_data)
                            self.resp_done[request.url] = resp_destination_file
                            self.resp_page += 1
            sleep(self.wait)

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

        with open(self.done_file, "a") as done_file_obj:
            urls_processed = 0
            for url in self.urls:
                if "fakku.net/anime/" in url or "fakku.net/games/" in url:
                    logging.info(f"{url.split('fakku.net/')[-1].split('/')[0]}: {url}")
                    urls_processed += 1
                    continue

                logging.info(url)
                self.timeout = TIMEOUT
                self.wait = WAIT

                self.waiting_loading_page(url, is_reader_page=False)

                # before parsing the main page check if user is logged
                try:
                    login_check = self.browser.find_element(
                        By.CSS_SELECTOR, "div.my-account.header-drop-down"
                    )
                except:
                    logging.info("You aren't logged in")
                    logging.info("Probably expired cookies")
                    logging.info("Remove cookies.json and try again")
                    program_exit()

                page_count = self.__get_page_count()
                log.debug(page_count)

                # green button under thumbnail
                try:
                    bt = self.browser.find_element(
                        By.CSS_SELECTOR, "a.button.icon.green"
                    )
                    if "Start Reading" not in bt.text:
                        logging.info(f"{bt.text}: {url}")
                        urls_processed += 1
                        continue
                except NoSuchElementException as err:
                    logging.info(f"No green button: {url}")
                    urls_processed += 1
                    continue

                try:
                    artist = self.browser.find_element(
                        By.CSS_SELECTOR, "a[href*=artist]"
                    )
                    artist = artist.find_element(By.XPATH, "./..")
                    artist = fix_filename(artist.text)
                except NoSuchElementException:
                    artist = None
                except AttributeError:
                    artist = None
                log.debug(artist)

                self.resp_done = OrderedDict()
                self.resp_page = 1
                resized_to_response = False
                cropped = False

                title = self.browser.find_element(By.TAG_NAME, "h1")
                title = fix_filename(title.text)
                log.debug(title)

                try:
                    circle = self.browser.find_element(
                        By.CSS_SELECTOR, "a[href*=circles]"
                    )
                    circle = circle.find_element(By.XPATH, "./..")
                    circle = fix_filename(circle.text)
                except NoSuchElementException:
                    circle = None
                except AttributeError:
                    circle = None
                log.debug(circle)

                try:
                    extra = self.browser.find_element(
                        By.CSS_SELECTOR, "a[href*=magazines]"
                    )
                    extra = extra.find_element(By.XPATH, "./..")
                    extra = fix_filename(extra.text)
                    extra = f" ({extra})"
                except NoSuchElementException:
                    extra = " (FAKKU)"
                except AttributeError:
                    extra = " (FAKKU)"
                log.debug(extra)

                try:
                    direction = self.browser.find_element(
                        By.XPATH,
                        "//*[@class='row-left' and contains(text(),'Direction')]/following-sibling::div",
                    )
                    direction = direction.text
                except NoSuchElementException:
                    direction = "Right to Left"
                except AttributeError:
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

                logging.info(f'Downloading "{folder_title}" manga.')

                progress_bar = tqdm(
                    total=page_count, desc="Working...", leave=False, position=0
                )

                page_num = 1

                while page_num <= page_count:
                    if page_num == 1:
                        # injection test
                        log.debug("First page, testing injection")
                        js_test = False
                        while not js_test:
                            self.waiting_loading_page(
                                f"{url}/read/page/{page_num}", is_reader_page=True
                            )
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

                    else:
                        log.debug("Clicking next page")
                        ui = self.browser.find_element(
                            By.CSS_SELECTOR, 'div.layer[data-name="UI"]'
                        )
                        ui.click()

                    if page_count > 1000:
                        padd = 4
                    elif page_count > 100:
                        padd = 3
                    elif page_count > 10:
                        padd = 2
                    else:
                        padd = 2

                    # get first page response image
                    self.get_response_images(page_num, response_folder, padd)

                    # waiting for bottom layer menu, gallery control, detecting spreads
                    spread = False
                    first_spread = False
                    second_spread = False
                    page_js_page = self.browser.find_element(
                        By.CSS_SELECTOR, ".page.js-page"
                    )
                    divider = self.browser.find_element(
                        By.CSS_SELECTOR, ".divider.js-divider"
                    )
                    count_js_count = self.browser.find_element(
                        By.CSS_SELECTOR, ".count.js-count"
                    )
                    divider = divider.get_property("innerHTML")
                    if divider == "-":
                        spread = True
                        first_spread = page_js_page.get_property("innerHTML")
                        first_spread = int(first_spread)
                        second_spread = count_js_count.get_property("innerHTML")
                        second_spread = int(second_spread)
                        pages = [first_spread, second_spread]
                    else:
                        page_count_3 = count_js_count.get_property("innerHTML")
                        page_count_3 = int(page_count_3)
                        if page_count_3 != page_count:
                            page_count = page_count_3
                            progress_bar.total = page_count
                            progress_bar.refresh()
                        pages = [page_num]
                    log.debug(page_count)

                    if page_count > 1000:
                        padd = 4
                    elif page_count > 100:
                        padd = 3
                    elif page_count > 10:
                        padd = 2
                    else:
                        padd = 2

                    for page_num in pages:
                        # get next page response image
                        self.get_response_images(page_num, response_folder, padd)

                    # wait untill loader hides itsefl
                    WebDriverWait(self.browser, self.timeout).until(
                        EC.invisibility_of_element_located((By.CLASS_NAME, "loader"))
                    )

                    # wait until read notification hides
                    WebDriverWait(self.browser, self.timeout).until(
                        EC.invisibility_of_element_located(
                            (By.CSS_SELECTOR, 'div[class*="ui notify-container large"]')
                        )
                    )

                    canvas_found = []
                    images_found = []

                    log.debug("Parsing PageView layer for canvas/images")
                    while len(canvas_found) != len(pages) and len(images_found) != len(
                        pages
                    ):
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
                                        canvas_found.append(canvas)
                                        if len(canvas_found) == len(pages):
                                            break
                            else:
                                images = page_view.find_elements(By.CSS_SELECTOR, "img")
                                for img_url in images:
                                    img_url = img_url.get_attribute("src")
                                    if img_url:
                                        if img_url in self.resp_done:
                                            images_found.append(img_url)
                                        elif "/thumbs/" in img_url:
                                            pass
                                        else:
                                            logging.info(img_url)
                                            logging.info(
                                                "Issue when image not in response"
                                            )
                        except StaleElementReferenceException as err:
                            pass
                        sleep(self.wait)

                    fin_img = []

                    if direction == "Left to Right":
                        pass
                    elif direction == "Right to Left":
                        images_found.reverse()
                        canvas_found.reverse()
                        pass

                    log.debug("Copy image from server response")
                    for img_url, page_num in zip(images_found, pages):
                        ext = self.resp_done[img_url].split(".")[-1]
                        destination_file = os.sep.join(
                            [manga_abs_path, f"{page_num:0{padd}d}.{ext}"]
                        )
                        if spread:
                            if pages.index(page_num) == 0:
                                destination_file = os.sep.join(
                                    [manga_abs_path, f"{page_num:0{padd}d}b.{ext}"]
                                )
                            elif pages.index(page_num) == 1:
                                destination_file = os.sep.join(
                                    [manga_abs_path, f"{page_num:0{padd}d}c.{ext}"]
                                )
                        shutil.copy(self.resp_done[img_url], destination_file)
                        fin_img.append(destination_file)
                        progress_bar.update(1)

                    log.debug("Get all images from canvas")
                    for c, page_num in zip(canvas_found, pages):
                        destination_file = os.sep.join(
                            [manga_abs_path, f"{page_num:0{padd}d}.png"]
                        )
                        if spread:
                            if pages.index(page_num) == 0:
                                destination_file = os.sep.join(
                                    [manga_abs_path, f"{page_num:0{padd}d}b.png"]
                                )
                            elif pages.index(page_num) == 1:
                                destination_file = os.sep.join(
                                    [manga_abs_path, f"{page_num:0{padd}d}c.png"]
                                )

                        js_script = f"""
                        var dataURL = HTMLCanvasElement.%s.call(arguments[0], \"image/png\");
                        return dataURL;
                        """ % (
                            js_name_todata,
                        )
                        rendered_image_data_url = self.browser.execute_script(
                            js_script, c
                        )

                        response_data = a2b_base64(
                            rendered_image_data_url.split(",")[1]
                        )

                        with open(destination_file, "wb") as f:
                            f.write(response_data)
                        fin_img.append(destination_file)
                        progress_bar.update(1)

                    if spread:
                        if sys.platform == "win32":
                            sp_c = "\\"
                        else:
                            sp_c = "/"
                        nam1 = fin_img[0].split(sp_c)[-1].split(".")[0][:-1]
                        nam2 = fin_img[1].split(sp_c)[-1].split(".")[0][:-1]
                        spread_name = nam1 + "-" + nam2
                        destination_file_spread = os.sep.join(
                            [manga_abs_path, f"{spread_name}a.png"]
                        )
                        combo = append_images(
                            fin_img, direction="horizontal", aligment="none"
                        )
                        combo.save(destination_file_spread)
                        log.debug(destination_file_spread)

                    page_num += 1
                    sleep(self.wait)
                progress_bar.close()

                del self.browser.requests  # delete old requests

                if len(self.resp_done) > 0:
                    resp_info_file = os.sep.join(
                        [response_folder, f"response_info.txt"]
                    )
                    with open(resp_info_file, "w") as file:
                        for k, v in self.resp_done.items():
                            file.write(f"{v}     {k}\n")

                # create a cbz and delete the image folder after creation
                if self.zip:
                    archive_name = shutil.make_archive(
                        folder_title, "zip", manga_folder
                    )
                    new_archive_title = folder_title + ".cbz"
                    os.rename(archive_name, new_archive_title)
                    shutil.move(new_archive_title, self.root_manga_dir)
                    shutil.rmtree(manga_folder)

                logging.info(">> manga done!")
                done_file_obj.write(f"{url}\n")
                urls_processed += 1
                if self.max is not None and urls_processed >= self.max:
                    break
                log.debug("Finished parsing page")
                sleep(self.wait)

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

    def __get_page_count(self):
        """
        Get count of manga pages from html code
        ----------------------------
        param: page_source -- string
            String that contains html code
        return: int
            Number of manga pages
        """
        log.debug("Getting gallery page count")
        page_count = None
        while not page_count:
            divs = self.browser.find_elements(By.CLASS_NAME, "row-right")
            for div in divs:
                if div.text.endswith(" pages") or div.text.endswith(" page"):
                    page_count = int(div.text.split(" ")[0])
            sleep(self.wait)
        return page_count

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

        urls = []
        with open(urls_file, "r") as f:
            for line in f:
                clean_line = line.replace("\n", "")
                if clean_line not in done and clean_line not in urls:
                    urls.append(clean_line)
        return urls

    def waiting_loading_page(self, url, is_reader_page=False):
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
                        program_exit()
                except NoSuchElementException as err2:
                    pass
                logging.info(
                    "\nError: timed out waiting for page to load. Timeout increased +10 for more delaying."
                )
                self.timeout += 10
                self.browser.refresh()
            if tried_2 > 10:
                logging.info("Connection issues, Try again")
                program_exit()
            elif tried > 2:
                logging.info("\nSome connection issues, refreshing page")
                self.browser.refresh()
                tried = 0
            else:
                tried += 1
                tried_2 += 1
            logging.debug("Sleeping")
            sleep(self.wait)
