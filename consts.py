import sys

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36 Edg/111.0.1661.62"
BASE_URL = "https://www.fakku.net"
API_URL = "https://books.fakku.net"
LOGIN_URL = f"{BASE_URL}/login/"
OPTIMIZE = True
# Path to headless driver
if sys.platform == "win32":
    EXEC_PATH = "C:/Users/beerpiss/scoop/apps/chromedriver/current/chromedriver.exe"
    BIN_PATH = r"C:\Users\beerpiss\scoop\apps\googlechrome\current\chrome.exe"
    USER_PATH = "_session"
    sp_c = "\\"
else:
    EXEC_PATH = "./chromedriver"
    BIN_PATH = "./chrome"
    USER_PATH = ".session"
    sp_c = "/"
# File with manga urls
URLS_FILE = "urls.txt"
# File with completed urls
DONE_FILE = "done.txt"
# File with prepared cookies
COOKIES_FILE = "cookies.txt"  # easy to read and edit
# Root directory for manga downloader
ROOT_MANGA_DIR = "manga"
# Root directory for original files from server response
ROOT_RESPONSE_DIR = "response"
# Timeout to page loading in seconds
TIMEOUT = 10
# Wait between page loading in seconds
WAIT = 0.1
# Should a cbz archive file be created
ZIP = False
