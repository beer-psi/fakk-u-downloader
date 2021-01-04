import argparse
from downloader import (FDownloader,
                        program_exit,
                        TIMEOUT,
                        WAIT,
                        URLS_FILE,
                        DONE_FILE,
                        COOKIES_FILE,
                        ROOT_MANGA_DIR,
                    )


def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "-f",
        "--file_urls",
        type=str,
        default=URLS_FILE,
        help=f".txt file that contains list of urls for download if \
            By default -- {URLS_FILE}")
    argparser.add_argument(
        "-d",
        "--done_file",
        type=str,
        default=DONE_FILE,
        help=f".txt file that contains list of urls that have been downloaded. \
            This is used to resume in the event that the process stops midway. \
            By default -- {DONE_FILE}")
    argparser.add_argument(
        "-c",
        "--cookies_file",
        type=str,
        default=COOKIES_FILE,
        help=f"Binary file that contains saved cookies for authentication. \
            By default -- {COOKIES_FILE}")
    argparser.add_argument(
        "-o",
        "--output_dir",
        type=str,
        default=ROOT_MANGA_DIR,
        help=f"The directory that will be used as the root of the output \
            By default -- {ROOT_MANGA_DIR}")
    argparser.add_argument(
        "-l",
        "--login",
        type=str,
        default=None,
        help="Login or email for authentication")
    argparser.add_argument(
        "-p",
        "--password",
        type=str,
        default=None,
        help="Password for authentication")
    argparser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=TIMEOUT,
        help=f"Timeout in seconds for how long to wait to take screenshot \
            Increase this argument if quality of pages is bad. By default -- {TIMEOUT} sec")
    argparser.add_argument(
        "-w",
        "--wait",
        type=float,
        default=WAIT,
        help=f"Wait time in seconds for pauses beetween downloading pages \
            Increase this argument if you become blocked. By default -- {WAIT} sec")
    args = argparser.parse_args()

    try:
        with open(args.file_urls, 'r') as f:
            pass
    except FileNotFoundError:

        print(f'File {args.file_urls} are not exist in folder. \n  \
            Create him and write into list of manga, or for set urls \n  \
            and downloading via console use key [--input_type]')
        program_exit()
    loader = FDownloader(
        urls_file=args.file_urls,
        done_file=args.done_file,
        cookies_file=args.cookies_file,
        root_manga_dir=args.output_dir,
        login=args.login,
        password=args.password,
        timeout=args.timeout,
        wait=args.wait,
    )

    try:
        with open(args.cookies_file, 'rb') as f:
            pass
    except FileNotFoundError:
        print('\nCookies file are not detected. Please, authenticate login ' + \
            'in next step and generate cookie for next runs.')
        loader.init_browser(headless=False)
    else:
        print(f'\nUsing cookies file: {args.cookies_file}')
        loader.init_browser(headless=True)
    loader.load_all()

if __name__ == '__main__':
    main()
