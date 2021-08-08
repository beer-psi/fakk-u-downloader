import argparse
import logging
import sys
from pathlib import Path

from downloader import (
    COOKIES_FILE,
    DONE_FILE,
    JewcobDownloader,
    MAX,
    ROOT_MANGA_DIR,
    TIMEOUT,
    URLS_FILE,
    WAIT,
    program_exit,
    version,
)


def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "-z",
        "--collection_url",
        type=str,
        default=None,
        help=f"Give a collection URL that will be parsed and loaded into urls.txt \
            The normal operations of downloading manga images will not happen while this \
            parameter is set. \
            By default -- None, process the urls.txt instead",
    )
    argparser.add_argument(
        "-f",
        "--file_urls",
        type=str,
        default=URLS_FILE,
        help=f".txt file that contains list of urls for download \
            By default -- {URLS_FILE}",
    )
    argparser.add_argument(
        "-d",
        "--done_file",
        type=str,
        default=DONE_FILE,
        help=f".txt file that contains list of urls that have been downloaded. \
            This is used to resume in the event that the process stops midway. \
            By default -- {DONE_FILE}",
    )
    argparser.add_argument(
        "-c",
        "--cookies_file",
        type=str,
        default=COOKIES_FILE,
        help=f"Binary file that contains saved cookies for authentication. \
            By default -- {COOKIES_FILE}",
    )
    argparser.add_argument(
        "-o",
        "--output_dir",
        type=str,
        default=ROOT_MANGA_DIR,
        help=f"The directory that will be used as the root of the output \
            By default -- {ROOT_MANGA_DIR}",
    )
    argparser.add_argument(
        "-l",
        "--login",
        type=str,
        default=None,
        help="Login or email for authentication",
    )
    argparser.add_argument(
        "-p", "--password", type=str, default=None, help="Password for authentication"
    )
    argparser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=TIMEOUT,
        help=f"Timeout in seconds for loading first manga page. \
            Increase this argument if quality of pages is bad. By default -- {TIMEOUT} sec",
    )
    argparser.add_argument(
        "-w",
        "--wait",
        type=float,
        default=WAIT,
        help=f"Wait time in seconds for pauses beetween downloading pages \
            Increase this argument if you become blocked. By default -- {WAIT} sec",
    )
    argparser.add_argument(
        "-m",
        "--max",
        type=int,
        default=MAX,
        help=f"Max number of volumes to download at once \
            Set this argument if you become blocked. By default -- No limit",
    )
    argparser.add_argument(
        "-n",
        "--nozip",
        dest="zip",
        action="store_true",
        help=f"By default this program creates a folder containing the images as an output. \
                Setting this creates a CBZ file instead.",
    )
    argparser.add_argument(
        "-G",
        "--GUI",
        dest="gui",
        action="store_false",
        help=f"Run with browser in graphic mode. By default this program runs in headless mode.",
    )
    argparser.add_argument(
        "-D",
        "--DEBUG",
        dest="debug",
        action="store_true",
        help=f"Run in debug mode, saves logs in debug.log file. Default false.",
    )
    args = argparser.parse_args()
    log_handlers = []
    if args.debug:
        log_level = logging.DEBUG
        log_formatter_steam = logging.Formatter(
            "%(levelname)s : %(name)s : %(module)s : %(funcName)s : %(lineno)d : %(message)s"
        )
        log_formatter_file = "%(asctime)s %(levelname)s %(name)s %(filename)s %(module)s %(funcName)s %(lineno)d %(message)s"
        log_file = "debug.log"
        log_handlers.append(logging.FileHandler(log_file, mode="w"))
    else:
        log_level = logging.INFO
        log_formatter_steam = logging.Formatter("%(message)s")
        log_formatter_file = ""
        log_file = None

    # added logging for both console and file
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_formatter_steam)
    stream_handler.setLevel(log_level)
    log_handlers.append(stream_handler)
    logging.basicConfig(
        level=log_level, format=log_formatter_file, handlers=log_handlers
    )

    log = logging.getLogger(__name__)
    if args.debug:
        log.debug(f"Version: %s", version)
        log.debug("Python %s - %s", sys.version, sys.platform)
        log.debug(sys.argv)
        log.debug(args)

    # ignore handlers other than root
    logging.getLogger("hpack").setLevel(logging.ERROR)
    logging.getLogger("PIL.PngImagePlugin").setLevel(logging.ERROR)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logging.getLogger("selenium.webdriver.remote.remote_connection").setLevel(
        logging.ERROR
    )
    logging.getLogger("seleniumwire").setLevel(logging.ERROR)
    logging.getLogger("undetected_chromedriver").setLevel(logging.ERROR)

    file_urls = Path(args.file_urls)
    if args.collection_url:
        Path(args.file_urls).touch()
    elif not file_urls.is_file() or file_urls.stat().st_size == 0:
        logging.info(
            f"File {args.file_urls} does not exist or empty.\n"
            + "Create it and write the list of manga urls first.\n"
            + "Or run this again with the -z parameter with a collection_url to download urls first."
        )
        program_exit()

    # Create empty done.text if it not exists
    if not Path(args.done_file).is_file():
        Path(args.done_file).touch()

    loader = JewcobDownloader(
        urls_file=args.file_urls,
        done_file=args.done_file,
        cookies_file=args.cookies_file,
        root_manga_dir=args.output_dir,
        login=args.login,
        password=args.password,
        timeout=args.timeout,
        wait=args.wait,
        _max=args.max,
        _zip=args.zip,
    )

    if not Path(args.cookies_file).is_file():
        logging.info(
            f"Cookies file({args.cookies_file}) are not detected. Please, "
            + "login in next step for generate cookie for next runs."
        )
        loader.init_browser(auth=True, headless=args.gui)
    else:
        logging.info(f"Using cookies file: {args.cookies_file}")
        loader.init_browser(headless=args.gui)

    if args.collection_url:
        loader.load_urls_from_collection(args.collection_url)
    else:
        loader.load_all()


if __name__ == "__main__":
    main()
