USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
BASE_URL = "https://www.fakku.net"
API_URL = "https://reader.fakku.net"
LOGIN_URL = f"{BASE_URL}/login/"
OPTIMIZE = True

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

LANG_MAP = {
    "English": "en",
    "Spanish": "es",
    "Japanese": "ja",
    "Chinese": "zh",
}
