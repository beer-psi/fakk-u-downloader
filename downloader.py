import json
import os
import re
import secrets
import shutil
import string
import urllib.request
from collections import OrderedDict
from gzip import compress, decompress
from io import BytesIO
from pickle import UnpicklingError
from sys import platform
from time import sleep

from PIL import Image

# I could drop lxml and use sth lighter, probably
from lxml import html
from lxml.html import builder
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire.undetected_chromedriver import Chrome, ChromeOptions
from tqdm import tqdm

# from seleniumwire.undetected_chromedriver.v2 import Chrome, ChromeOptions

"""
instead of toDataURL and urlib.request you could use toBlob() and write bytes to disk

there is also CanvasRenderingContext2D.getImageData() but it's slow:
var ctx = canvas.getContext("2d");
var img_data = CanvasRenderingContext2D.originalgetImageData.call(ctx, 0, 0, canvas.width, canvas.height)

def chunks(data, rows=4):
    for i in range(0, len(data), rows):
        yield data[i: i + rows]

newimdata = []
for chunk in chunks(img_data['data']):
    newimdata.append((chunk[0], chunk[1], chunk[2], chunk[3]))
imd = Image.new("RGBA", (rendered_image_data_url['width'], rendered_image_data_url['height']))
imd.putdata(newimdata)
imd.save('sth.png')

"""

BASE_URL = "https://www.fakku.net"
LOGIN_URL = f"{BASE_URL}/login/"
# Initial display settings for browser. Used for grahic mode
MAX_DISPLAY_SETTINGS = [800, 600]
# Path to headless driver
if platform == "win32":
    EXEC_PATH = "chromedriver.exe"
else:
    EXEC_PATH = "chromedriver"
    #EXEC_PATH = "/usr/bin/chromedriver"
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

# create script tag in html body/head
js_name_todata = "".join(
    secrets.choice(string.ascii_letters + string.digits) for _ in range(10)
)
js_name_getetag = "".join(
    secrets.choice(string.ascii_letters + string.digits) for _ in range(10)
)
js_script = """
var s = document.createElement('script');
s.type = 'text/javascript';
var code = "HTMLCanvasElement.%s = HTMLCanvasElement.prototype.toDataURL;Document.%s = Document.prototype.getElementsByTagName;";
try {
      s.appendChild(document.createTextNode(code));
    } catch (e) {
      s.text = code;
}
s.onload = function() {
    this.remove();
};
(document.body || document.documentElement).appendChild(s);
(document.head || document.documentElement).appendChild(s);
""" % (
    js_name_todata,
    js_name_getetag,
)
script_elem_to_inject = builder.SCRIPT(js_script)


def program_exit():
    print("Program exit.")
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
    filename = filename.replace("\n", "")
    filename = filename.replace("\r", "")
    filename = filename.replace("\t", "")
    filename = filename.replace("/", "⁄")
    if platform == "win32":
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
            Path to the headless driver
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

    def init_browser(self, headless=False):
        """
        Initializing browser and authenticate if necessary
        Obfuscation with undetected-chromedriver
        ---------------------
        param: headless -- bool
            If True: launch browser in headless mode(for download manga)
            If False: launch usually browser with GUI(for first authenticate)
        """
        if not headless:
            self.__auth()
            headless = True
        options = ChromeOptions()
        if headless:
            options.headless = True
            options.add_argument("--headless")
        # set options to avoid cors and other bullshit
        # options.add_argument(f'user-agent={USER_AGENT}')
        # options.add_argument(f'no-sandbox')
        # options.add_argument(f'disable-setuid-sandbox')
        options.add_argument(f"disable-web-security")

        # caps = webdriver.DesiredCapabilities.CHROME.copy()
        # caps['pageLoadStrategy'] = 'eager'

        self.browser = Chrome(
            executable_path=self.driver_path,
            chrome_options=options,
            # desired_capabilities=caps,
        )
        if not headless:
            self.browser.set_window_size(*self.default_display)
        # self.browser.header_overrides = {'disable_encoding': 'True'}
        self.browser.header_overrides = {"Accept-Encoding": "gzip"}
        self.browser.response_interceptor = self.interceptor
        self.browser.scopes = [
            ".*books.fakku.net/images/.*",
            ".*fakku.net/hentai/.*/read/page/.*",
        ]
        self.__set_cookies()

    def __clean_cookies(self, cookies):
        """
        Function that removes excessive cookies
        not used anymore
        maybe extend cookies expiration time?
        """
        remove_cookies = []
        for cookie in cookies:
            if "name" in cookie:
                if cookie["name"] in {"fakku_sid", "fakku_zid"}:
                    if "expiry" in cookie:
                        cookie["expiry"] = int(cookie["expiry"])
                else:
                    remove_cookies.append(cookie)
            else:
                remove_cookies.append(cookie)
        for cookie in remove_cookies:
            cookies.remove(cookie)
        return cookies

    def __set_cookies(self):
        # self.browser.set_window_size(*self.default_display)
        self.browser.get(LOGIN_URL)
        # set fakku local storage options, like original image size or enable spreads
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-twoPageMode','1');"
        )
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-pageScalingMode','none');"
        )
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-fitIfOverWidth','false');"
        )
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-backgroundColor','#7F7B7B');"
        )
        self.browser.execute_script(
            "window.localStorage.setItem('fakku-suppressWidthFitForSpreads','false');"
        )
        with open(self.cookies_file, "rb") as f:
            cookies = json.load(f)
            for cookie in cookies:
                if "expiry" in cookie:
                    cookie["expiry"] = int(cookie["expiry"])
                    self.browser.add_cookie(cookie)

    def __init_headless_browser(self):
        """
        Recreating browser in headless mode(without GUI)
        """
        options = ChromeOptions()
        options.headless = True
        options.add_argument("--headless")
        self.browser = Chrome(executable_path=self.driver_path, chrome_options=options)

    def __auth(self):
        """
        Authentication in browser with GUI for saving cookies in first time
        """
        options = ChromeOptions()
        options.headless = False
        self.browser = Chrome(executable_path=self.driver_path, chrome_options=options)
        self.browser.set_window_size(*self.default_display)
        self.browser.get(LOGIN_URL)
        if not self.login is None:
            self.browser.find_element_by_id("username").send_keys(self.login)
        if not self.password is None:
            self.browser.find_element_by_id("password").send_keys(self.password)
        try:
            self.browser.find_element_by_class_name("js-submit").click()
        except:
            self.browser.find_element_by_class_name("js-submit2").click()

        ready = input("Tab Enter to continue after you login...")
        with open(self.cookies_file, "w") as f:
            json.dump(self.browser.get_cookies(), f, indent=True)

        self.browser.quit()

    def interceptor(self, request, response):  # A response interceptor takes two args
        if "fakku.net/hentai/" in request.url and "/read/page/" in request.url:
            # various checks to make sure we're only injecting the script on appropriate responses
            # we check that the content type is HTML, that the status code is 200, and that the encoding is gzip
            if (
                response.headers.get_content_subtype() != "html"
                or response.status_code != 200
                or response.headers["Content-Encoding"] != "gzip"
            ):
                print(response.headers.get_content_subtype())
                print(response.status_code)
                print(response.headers["Content-Encoding"])
                print(response.headers)
                print(request.url)
                return None
            try:
                parsed_html = html.fromstring(decompress(response.body))
            except Exception as err:
                print(err)
                raise err
            # injecting js
            try:
                parsed_html.head.insert(0, script_elem_to_inject)
            except Exception as err:
                print(err)
                try:
                    parsed_html.head.insert(0, script_elem_to_inject)
                except Exception as err:
                    print(err)
                    raise err
            # modify response body
            response.body = compress(html.tostring(parsed_html))
        pass

    def get_response_images(self, img_num, save_path):
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
                except Exception as err:
                    print(err)
                    raise err
            for request in all_requests:
                if request.response:
                    if request.url.startswith("https://books.fakku.net/images/manga"):
                        if request.url not in self.resp_done:
                            # print(request.url)
                            resp_file_name = request.url.split("/")[-1]
                            # print(request.response.headers)
                            resp_file_type = request.response.headers[
                                "Content-Type"
                            ].split("/")[-1]
                            resp_file_type = resp_file_type.replace("jpeg", "jpg")
                            resp_data = request.response.body
                            resp_destination_file = os.sep.join(
                                [save_path, f"{self.resp_page:02d}.{resp_file_type}"]
                            )
                            with open(resp_destination_file, "wb") as file:
                                file.write(resp_data)
                            self.resp_done[request.url] = resp_destination_file
                            self.resp_page += 1
            sleep(self.wait)

    def load_all(self):
        """
        Just main function
        open main page and first gallery page click the rest
        dumping image data from html canvas as .png
        """
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
        # print(self.browser.get_window_size())
        # print(self.browser.execute_script("return navigator.userAgent;"))
        ignore_size.add((window_size["width"], window_size["height"]))

        with open(self.done_file, "a") as done_file_obj:
            urls_processed = 0
            for url in self.urls:
                print(url)
                self.timeout = TIMEOUT
                self.wait = WAIT

                self.browser.get(url)
                self.waiting_loading_page(is_reader_page=False)
                page_count = self.__get_page_count()
                try:
                    login_check = self.browser.find_element_by_css_selector(
                        "div.my-account.header-drop-down"
                    )
                except:
                    print("Remove cookies.json and try again")
                    program_exit()

                # green button under thumbnail
                try:
                    bt = self.browser.find_element_by_css_selector(
                        "a.button.icon.green"
                    )
                    if "Start Reading" not in bt.text:
                        if "Read With FAKKU Unlimited" in bt.text:
                            print("Subscription", url)
                            urls_processed += 1
                            continue
                        else:
                            # todo need to catch some more variations of green button text
                            print(url)
                            print(bt)
                            urls_processed += 1
                            continue
                except Exception as err:
                    print(err)
                    print(url)
                    print(
                        """
                    There was a problem.
                    You do not have access to this content.
                    """
                    )
                    urls_processed += 1
                    continue

                artist = self.browser.find_element_by_css_selector("a[href*=artist]")
                artist = artist.find_element_by_xpath("./..")
                artist = fix_filename(artist.text)

                self.resp_done = OrderedDict()
                self.resp_page = 1
                resized_to_response = False
                cropped = False

                title = self.browser.find_element_by_tag_name("h1")
                title = fix_filename(title.text)

                try:
                    circle = self.browser.find_element_by_css_selector(
                        "a[href*=circles]"
                    )
                    circle = circle.find_element_by_xpath("./..")
                    circle = fix_filename(circle.text)
                except NoSuchElementException:
                    circle = None

                try:
                    extra = self.browser.find_element_by_css_selector(
                        "a[href*=magazines]"
                    )
                    extra = extra.find_element_by_xpath("./..")
                    extra = fix_filename(extra.text)
                    extra = f" ({extra})"
                except NoSuchElementException:
                    extra = " (FAKKU)"

                if circle:
                    folder_title = (
                        "[" + circle + " (" + artist + ")" + "] " + title + extra
                    )
                else:
                    folder_title = "[" + artist + "] " + title + extra
                manga_folder = os.sep.join([self.root_manga_dir, folder_title])
                if not os.path.exists(manga_folder):
                    os.mkdir(manga_folder)
                manga_abs_path = os.path.abspath(manga_folder)
                response_folder = os.sep.join([self.root_response_dir, folder_title])
                if not os.path.exists(response_folder):
                    os.mkdir(response_folder)

                print(f'Downloading "{folder_title}" manga.')

                progress_bar = tqdm(
                    total=page_count, desc="Working...", leave=False, position=0
                )

                page_num = 1

                while page_num <= page_count:
                    if page_num == 1:
                        # injection test
                        js_test = None
                        while type(js_test) is not dict:
                            self.browser.get(f"{url}/read/page/{page_num}")
                            self.waiting_loading_page(is_reader_page=True)
                            js_script_test = (
                                """
                                        var dataURL = HTMLCanvasElement.%s;
                                        return dataURL;
                                        """
                                % js_name_todata
                            )
                            js_test = self.browser.execute_script(js_script_test)
                            self.wait += 1
                            sleep(self.wait)
                    else:
                        layers = self.browser.find_elements_by_class_name("layer")
                        layer = layers[-1]
                        layer.click()

                    # get first page response image
                    self.get_response_images(page_num, response_folder)

                    # waiting for bottom layer menu, gallery control, detecting spreads
                    spread = False
                    first_spread = False
                    second_spread = False
                    try:
                        page_js_page = self.browser.find_element_by_css_selector(
                            ".page.js-page"
                        )
                        divider = self.browser.find_element_by_css_selector(
                            ".divider.js-divider"
                        )
                        count_js_count = self.browser.find_element_by_css_selector(
                            ".count.js-count"
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
                        # print(drawer.get_property("children"))
                    except Exception as err:
                        print("Catch this error")
                        print(err)
                        raise err

                    # print(pages)
                    for page_num in pages:
                        # get next page response image
                        self.get_response_images(page_num, response_folder)

                    # wait untill loader hides itsefl
                    loader = self.browser.find_element_by_class_name("loader")
                    WebDriverWait(self.browser, self.timeout).until(
                        EC.invisibility_of_element_located((By.CLASS_NAME, "loader"))
                    )

                    img_urls = []
                    canvas_found = []
                    fin_img = []

                    try:
                        img_urls = self.browser.find_elements_by_class_name("page")
                        for img_url in img_urls:
                            img_url = img_url.get_attribute("src")
                            if img_url:
                                if img_url in self.resp_done:
                                    canvas_found.append(img_url)
                                elif "/thumbs/" in img_url:
                                    pass
                                else:
                                    print(img_url)
                                    print("Issue when image not in response")
                    except Exception as err:
                        print(err)
                    done = 0

                    # copy image from server response
                    for img_url in canvas_found:
                        done = False
                        shutil.copy(self.resp_done[img_url], manga_abs_path)
                        fin_path = os.path.join(
                            manga_abs_path, self.resp_done[img_url].split("/")[-1]
                        )
                        # print(fin_path)
                        fin_img.append(fin_path)
                        done += 1
                        progress_bar.update(1)

                    if done < len(pages):
                        # get all images from canvas
                        while len(canvas_found) != len(pages):
                            c = 0
                            for canvas in self.browser.find_elements_by_tag_name(
                                "canvas"
                            ):
                                widthc = canvas.size["width"]
                                heightc = canvas.size["height"]
                                # print(widthc, heightc)
                                # print(ignore_size)
                                if (widthc, heightc) not in ignore_size:
                                    canvas_found.append(c)
                                    if len(canvas_found) == len(pages):
                                        break
                                c += 1
                            sleep(self.wait)

                        for c, page_num in zip(canvas_found, pages):
                            destination_file = os.sep.join(
                                [manga_abs_path, f"{page_num:02d}.png"]
                            )
                            if spread:
                                if pages.index(page_num) == 0:
                                    destination_file = os.sep.join(
                                        [manga_abs_path, f"{page_num:02d}b.png"]
                                    )
                                elif pages.index(page_num) == 1:
                                    destination_file = os.sep.join(
                                        [manga_abs_path, f"{page_num:02d}c.png"]
                                    )

                            js_script = f"""
                            var canvas = Document.%s.call(document, 'canvas')[{c}];
                            var dataURL = HTMLCanvasElement.%s.call(canvas, \"image/png\");
                            return dataURL;
                            """ % (
                                js_name_getetag,
                                js_name_todata,
                            )
                            # print(js_script)
                            try:
                                rendered_image_data_url = self.browser.execute_script(
                                    js_script
                                )
                            except JavascriptException as err:
                                print(c)
                                print(c)
                                print(c)
                                print(err)
                                raise err

                            response = urllib.request.urlopen(rendered_image_data_url)
                            response_data = response.file.read()
                            im = Image.open(BytesIO(response_data))
                            if im.size in {(300, 150), (1, 1)}:
                                print(c, page_num)
                                print(page_num)
                                print(url)
                                print("Probably check it manually")
                                program_exit()

                            with open(destination_file, "wb") as f:
                                f.write(response_data)
                            fin_img.append(destination_file)
                            done += 1
                            progress_bar.update(1)

                    if spread:
                        nam1 = fin_img[0].split("/")[-1].split(".")[0][:-1]
                        nam2 = fin_img[1].split("/")[-1].split(".")[0][:-1]
                        spread_name = nam1 + "-" + nam2
                        destination_file_spread = os.sep.join(
                            [manga_abs_path, f"{spread_name}a.png"]
                        )
                        combo = append_images(
                            fin_img, direction="horizontal", aligment="none"
                        )
                        combo.save(destination_file_spread)
                        # print(destination_file_spread)

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

                # by default create a cbz and delete the image folder after creation
                if self.zip:
                    archive_name = shutil.make_archive(
                        folder_title, "zip", manga_folder
                    )
                    new_archive_title = folder_title + ".cbz"
                    os.rename(archive_name, new_archive_title)
                    shutil.move(new_archive_title, self.root_manga_dir)
                    shutil.rmtree(manga_folder)

                print(">> manga done!")
                done_file_obj.write(f"{url}\n")
                urls_processed += 1
                if self.max is not None and urls_processed >= self.max:
                    break

    def load_urls_from_collection(self, collection_url):
        """
        Function which records the manga URLs inside a collection
        """
        self.browser.get(collection_url)
        self.waiting_loading_page(is_reader_page=False)
        page_count = self.__get_page_count_in_collection()
        with open(self.urls_file, "a") as f:
            for page_num in tqdm(range(1, page_count + 1)):
                if (
                    page_num != 1
                ):  # Fencepost problem, the first page of a collection is already loaded
                    self.browser.get(f"{collection_url}/page/{page_num}")
                    self.waiting_loading_page(is_reader_page=False)

                try:
                    all_pages_book = self.browser.find_elements_by_css_selector(
                        "a.book-title"
                    )
                    for a in all_pages_book:
                        href = a["href"]
                        f.write(f"{BASE_URL}{href}\n")
                except NoSuchElementException:
                    pass
                try:
                    all_pages_content = self.browser.find_elements_by_css_selector(
                        "a.content-title"
                    )
                    for a in all_pages_content:
                        href = a["href"]
                        f.write(f"{BASE_URL}{href}\n")
                except NoSuchElementException:
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
        page_count = None
        divs = self.browser.find_elements_by_class_name("row-right")
        for div in divs:
            if div.text.endswith(" pages") or div.text.endswith(" page"):
                page_count = int(div.text.split(" ")[0])
                break
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
        page_count = None
        try:
            pagination = self.browser.find_element_by_class_name("pagination-meta")
            pagination_text = pagination.text
            page_count = int(
                re.search(r"Page\s+\d+\s+of\s+(\d+)", pagination_text).group(1)
            )
        except NoSuchElementException:
            pass
        except Exception as err:
            print(err)
            raise err

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
        done = []
        with open(done_file, "r") as donef:
            for line in donef:
                done.append(line.replace("\n", ""))

        urls = []
        with open(urls_file, "r") as f:
            for line in f:
                clean_line = line.replace("\n", "")
                if clean_line not in done:
                    urls.append(clean_line)
        return urls

    def waiting_loading_page(self, is_reader_page=False):
        """
        Awaiting while page will load
        ---------------------------
        param: is_non_reader_page -- bool
            False -- awaiting of main manga page
            True -- awaiting of others manga pages
        """
        if not is_reader_page:
            elem_xpath = "//link[@type='image/x-icon']"
        else:
            elem_xpath = "//div[@data-name='PageView']"
        elm_found = False
        # FAKKU is temporarily down for maintenance.
        while not elm_found:
            try:
                element = EC.presence_of_element_located((By.XPATH, elem_xpath))
                elm_found = WebDriverWait(self.browser, self.timeout).until(element)
            except TimeoutException as err:
                try:
                    title = self.browser.find_element_by_tag_name("h1")
                    title = title.text
                    if "FAKKU is temporarily down for maintenance." in title:
                        print("FAKKU is temporarily down for maintenance.")
                        program_exit()
                except Exception as err2:
                    print(err2)
                print(
                    "\nError: timed out waiting for page to load. Timeout increased +10 for more delaying."
                )
                self.timeout += 10
                self.browser.refresh()
            except Exception as err:
                print(err)
                raise err
            sleep(self.wait)
