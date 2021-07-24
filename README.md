forked from https://github.com/Hikot0shi/fakku-downloader/

warning tested only on https://www.fakku.net/tags/free  
63 works downloaded without an issue (subscription, games, anime skipped)

# jewcob-downloader

Jewcob-downloader - this is python script that allows downloading manga directly from fakku.net.

## The problem

> *Fakku.net manga reader has a good protection from download.* 

- server sends scrambled image - jpg* quality 80-90 for color, png for greyscale
- reader unscramble image and put it on html canvas - returns rgba values for each pixel
- reader overwrites javascript methods to block downloading images from canvas

## Solution

- screenshots of canvas/layer element
  - pros:
    - quick and easy find_elements_by().screenshot()
  - cons:
    - defaults to screenshots with the size of browser window
    - browser.window needs to be resized to canvas size
	- hard to tell when camvas resize ends (window.addEventListener("resize") or canvas.onresize)
    - ends up with hardcoded wait time after resize but before screenshot
- injecting javascript before evaluation of reader.min.js https://intoli.com/blog/javascript-injection/  
  - pros:
    - easy when using cdp, selenium execute_cdp_cmd() Page.addScriptToEvaluateOnNewDocument, puppeteer and pyppeteer evaluateOnNewDocument()
    - evaluates injected javascript code before external scripts
  - cons:
    - cors https://fetch.spec.whatwg.org/#http-cors-protocol https://www.w3.org/wiki/CORS_Enabled 
    - when implemented with webextension can't run headless chromium  
    - whem implemented with proxy and html response body modification it requires selenium-wire 
    - whem implemented with cdp prone to race conditions, often fails in selenium

## Quality

There is no difference in qualiy between element.screenshot and canvas.todataurl. Both returns RGBA32 PNG.  
There is no way to get lossless unscrambled jpg from fakku scrambled source jpg image quality 80-90.  
You can only get unscrambled bloated png with 4x or 5x the size of jpg, with the same shitty jpg quality.  
Basically fakku is serving shitty color jpgs and most rippers are treating them as high quality lossless pngs. 

## Implementation
- open /read/page/1
- wait for response with scrambled image
- wait fo .loader to hide
- wait fo notification message to hide
- take screenshot of canvas/layer element / get todataurl
- click() on the layer to load next image
- repeat for all images

## Changes from the fakku-downloader
- readable json cookies instead of pickle
- css selectors instead of bs
- canvas toDataURL instead of screenshots
- added js injections with selenium-wire
- added response image downloader with selenium-wire
- added support for spreads
- removed not working obfuscation, used undetected-chromedriver instead
- directory/archive and image naming schemes match rbot rips https://sukebei.nyaa.si/user/rbot2000

## How to launch  
### Windows (quick)
1) Download jewcob-downloader.exe from [Releases](https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/releases) page
2) Download [ChromeDriver](https://chromedriver.chromium.org/downloads) the same version as you Chrome Browser and move it in the same folder as jewcob-downloader.exe. (Rename it to **chromedriver.exe**)  
4) Create **urls.txt** file in that folder and write urls of manga one by line
5) Double-click on jewcob-downloader.exe or open folder in command line and run the command <code>jewcob-downloader.exe</code>

### From source  
1) Download or clone this repository
2) Download and install [Python](https://www.python.org/downloads/release)  version >= 3.9
3) Download [ChromeDriver](https://chromedriver.chromium.org/downloads) the same version as you Chrome Browser and move it in root folder.
(Rename it to **chromedriver.exe**)
4) Create **urls.txt** file in root folder and write into that urls of manga one by line
5) Install all requirements for script via run **install.bat** (for Windows) or run <code>pip install -r requirements.txt</code>
6) Open root folder in command line and run the command <code>python main.py</code>

## Some features
* Use option -w for set wait time between loading the pages. If program somewhere crush its can help.
* Use option -t for set timeout for loading pages.
* Use option -l and -p for write the login and password from fakku.net
* More option technical you can find via --help

## TODOS

- probably add some tests, with free urls, both scrambled and unscrambled, spreads

## Working example

1. After downloading the repository, chromedriver and creating urls.txt file, root folder will be like this:
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/1.PNG" width="800">
</p>
2. Urls in urls.txt views like this:
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/2.PNG" width="800">
</p>
3. Write the command: python main.py
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/3.PNG" width="800">
</p>
4. If you launch program in first time, you need to login in opening browser and press enter in console. After that program save the cookies and will be use it in next runs in headless browser mode and skeep this step.
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/4.PNG" width="800">
</p>
5. Downloading process
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/5.PNG" width="800">
</p>
6. The program will create its own folder for each manga in urls.txt
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/6.PNG" width="800">
</p>
7. And inside in each folder you can see the manga pages in the most affordable quality as in a browser.
<p align="center">
	<img src="https://gitgud.io/combtmp-w5f08/jewcob-downloader/-/raw/master/readme_png/7.PNG" width="800">
</p>

## Extra: Download URLs from a Collection

If you have a collection that has the manga that you would like to download,
you can generate a **urls.txt** file that has all of its links.

Setup as above, and then call like this:

```bash
python main.py -z https://www.fakku.net/users/MY-USER-12345/collections/MY-COLLECTION
```

This will make a **urls.txt** file with the links, then run the program as normal
with this file as input.

# Results

pyppeteer rewrite  
unstable, on every page.goto there is 50% chance of it throwing pyppeteer.errors.PageError: Page crashed!, test took 33 sec

selenium using cdp  
unstable 50% chance of addScriptToEvaluateOnNewDocument failing, slightly slower than pyppeteer, test took 37 sec

selenium with selenium-wire proxy  
stable, worked through 68 free links without an issue, slightly slower than rdp, test took 43 sec

selenium using CanvasRenderingContext2D.originalgetImageData() method  
slow, very slow, ~20 seconds to get pixels from browser and to put them back together into single rgba32 image, ~20s per image

canvas.toblob() can't save files outside of browser download location

# Extra2: Enable controversial content

https://archived.moe/h/thread/6271290/#6289883  
https://archiveofsins.com/h/thread/6271290/#6289883  

> 1. Go to https://www.fakku.net/account/preferences
> 2. Open the browser console (F12)
> 3. Paste this into the console and press enter:  
> $("form.js-start-disabled-button").first().append($('<input type="hidden" name="content_controversial" value="1" />')).find("button[type=submit]").attr("disabled", false).click();
