import Connector from '../engine/Connector.mjs';
import Manga from '../engine/Manga.mjs';
import uheprng from '../engine/uheprng.mjs';

export default class Fakku extends Connector {
    constructor() {
        super();

        super.id = 'fakku';
        super.label = 'Fakku';

        this.tags = ['hentai', 'english'];
        this.url = 'https://www.fakku.net';
        this.api = 'https://reader.fakku.net';

        this.requestOptions.headers.set('x-referer', `${this.url}/`);

        this.links = {
            login: 'https://www.fakku.net/login',
        };
    }

    canHandleURI(uri) {
        return /(www\.)?fakku\.net\/hentai\/([a-z-]+)(?:\/read)?/.test(uri);
    }

    async _getMangaFromURI(uri) {
        const request = new Request(new URL(uri), this.requestOptions);
        const dom = await this.fetchDOM(request, 'div.col-span-full h1');
        const id = uri.pathname.match(/\/hentai\/([a-z-]+)(?:\/read)?/)[1];

        return new Manga(this, `/hentai/${id}`, dom[0].textContent.trim());
    }

    async _getMangas() {
        const dom = await this.fetchDOM(this.url);

        const lastPage = Number(dom.querySelector("a[href^='/page/']:last-child").getAttribute('href').match(/\/page\/([0-9]+)/)[1]);
        const mangaList = this._getMangasFromDOM(dom);

        for (let page = 2; page <= lastPage; page++) {
            const mangas = await this._getMangasFromPage(page);
            mangaList.push(...mangas);
        }

        return mangaList;
    }

    _getMangasFromDOM(dom) {
        return [...dom.querySelectorAll("div[id^='content-']")]
            .map(element => ({
                id: element.querySelector('a').getAttribute('href'),
                title: element.querySelector('a').getAttribute('title'),
            }));
    }

    async _getMangasFromPage(page) {
        const path = page > 1 ? `/page/${page}` : '';
        const dom = await this.fetchDOM(`${this.url}${path}`);

        return this._getMangasFromDOM(dom);
    }

    async _getChapters(manga) {
        return [manga];
    }

    async _getPages(chapter) {
        // Needed to fetch cookies
        const dom = await this.fetchDOM(new URL(`${chapter.id}/read`, this.url), 'div h3');
        if (dom.length > 0 && dom[0].textContent === 'You do not have access to this content.') {
            throw new Error("You do not have access to this content. Maybe you haven't bought it?");
        }

        const url = new URL(`${chapter.id}/read`, this.api);
        const resp = await fetch(url, {
            mode: 'cors',
            redirect: 'follow',
            credentials: 'same-origin',
            cache: 'no-cache',
            referrer: `${this.url}/`,
            headers: {
                'accept': 'application/json',
                'x-referer': `${this.url}/`,
            }
        });
        const data = await resp.json();

        if (data.key_data) {
            let electron = require('electron');
            const zidCookies = await electron.remote.session.defaultSession.cookies.get({ url: this.url, name : 'fakku_zid' });
            const zid = zidCookies[0].value;

            const key = calculateDecryptionKey(data.key_hash, zid);

            const dataKeys = JSON.parse(
                decodeXorCipher(
                    key,
                    atob(data.key_data),
                ).join(''),
            );

            return Object.entries(data.pages).map(([key, value]) => this.createConnectorURI({
                url: value.image,
                keys: dataKeys[key],
            }));
        } else {
            return Object.values(data.pages).map(value => this.createConnectorURI({
                url: value.image,
            }));
        }
    }

    async _handleConnectorURI(uri) {
        const resp = await fetch(uri.url, {
            mode: 'cors',
            redirect: 'follow',
            credentials: 'same-origin',
            cache: 'no-cache',
            referrer: `${this.url}/`,
            headers: {
                'accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'x-referer': `${this.url}/`,
            }
        });
        if (!uri.keys) {
            return await this._blobToBuffer(await resp.blob());
        }

        const image = await createImageBitmap(await resp.blob());

        const keys = uri.keys;
        const keysReordered = shuffleArray(keys, keys.pop());

        const xorKey = keysReordered[2];
        const width = keysReordered[0] ^ xorKey;
        const height = keysReordered[1] ^ xorKey;

        const isHorizontalPage = width / height > 1;
        const smallerEdge = isHorizontalPage ? height : width;
        const offset = 128 * Math.ceil(smallerEdge / 128) - smallerEdge;
        const widthPieces = Math.ceil(width / 128);
        const heightPieces = Math.ceil(height / 128);

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        randomize(range(widthPieces * heightPieces), xorKey).forEach((value, index) => {
            const sxPiece = value % widthPieces;
            const syPiece = (value - sxPiece) / widthPieces;
            const dxPiece = index % widthPieces;
            const dyPiece = (index - dxPiece) / widthPieces;
            const lastPiece = isHorizontalPage
                ? dyPiece === heightPieces - 1
                : dxPiece === widthPieces - 1;

            let dx = dxPiece * 128;
            let dy = dyPiece * 128;

            if (lastPiece) {
                dx -= isHorizontalPage ? 0 : offset;
                dy -= isHorizontalPage ? offset : 0;
            }

            ctx.drawImage(image, sxPiece * 128, syPiece * 128, 128, 128, dx, dy, 128, 128);
        });

        return await this._blobToBuffer(await this._canvasToBlob(canvas));
    }

    _canvasToBlob(canvas) {
        return new Promise(resolve => {
            canvas.toBlob(data => {
                resolve(data);
            }, Engine.Settings.recompressionFormat.value, parseFloat(Engine.Settings.recompressionQuality.value) / 100);
        });
    }
}

function range(count) {
    return [...Array(count).keys()];
}

function randomize(arr, seed) {
    const instance = uheprng.create(seed);
    const arrCopy = arr.slice(0);
    for (let length = arr.length; length;) {
        let newLocation = Math.floor(instance.random() * length--);
        let temp = arrCopy[newLocation];
        arrCopy[newLocation] = arrCopy[length];
        arrCopy[length] = temp;
    }
    return arrCopy;
}

function shuffleArray(arr, seed) {
    const length = arr.length;
    const randomized = randomize(range(length), seed);
    const result = [];
    for (let i = 0; i < length; ++i) {
        result[randomized[i]] = arr[i];
    }
    return result;
}

function decodeXorCipher(key, content) {
    const keyLength = key.length;
    const contentLength = content.length;
    const result = [];
    for (let i = 0; i < contentLength; ++i) {
        result.push(String.fromCharCode(content.charCodeAt(i) ^ key.charCodeAt(i % keyLength)));
    }
    return result;
}

function calculateDecryptionKey(keyHash, fakkuZid) {
    const zid =
      '13fafbe11a72969c2464696efd553940f6a45c1c4801b19c3445e033f38b0e7e';

    const extra =
      fakkuZid === zid
          ? 'b3d90ea3cc794be5e74013880c4519aae1b8fbe3108f2bbe60c5dc3f6e807ff1'
          : '0a10f3bd42587ad70fc96886d8e5e7b3614ce69529b238a1c690cb9b51d4868f';

    return fakkuZid + keyHash + extra;
}
