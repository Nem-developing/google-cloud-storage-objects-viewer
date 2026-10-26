import gzip
import os
import threading
import time
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, abort, redirect, render_template, request, url_for
from google.api_core.exceptions import Forbidden, Unauthorized
from google.cloud import storage
from google.cloud.storage.retry import DEFAULT_RETRY

with open(Path(__file__).with_name('pyproject.toml'), 'rb') as pyproject:
    VERSION = tomllib.load(pyproject)['project']['version']

### VALUES MUST BE LIKE THAT :
#BUCKET_NAME = 'prod-nehemiebarkia-publique'
#FOLDER_SRC = 'scripts/'
#PUB_URL = 'https://storage.googleapis.com/prod-nehemiebarkia-publique'
#TITLE = "SCRIPTS RÉDIGÉS PAR BARKIA NEHEMIE"
#TIMEZONE = 'Europe/Paris'
#CACHE_TTL = 60
###
BUCKET_NAME = os.environ['BUCKET_NAME']
FOLDER_SRC = os.getenv('FOLDER_SRC', '').strip('/')
FOLDER_SRC = FOLDER_SRC + '/' if FOLDER_SRC else ''
PUB_URL = os.getenv('PUB_URL', f'https://storage.googleapis.com/{BUCKET_NAME}').rstrip('/')
PUB_ORIGIN = '{0.scheme}://{0.netloc}'.format(urlsplit(PUB_URL))
TITLE = os.getenv('TITLE', BUCKET_NAME)
TIMEZONE = ZoneInfo(os.getenv('TIMEZONE', 'Europe/Paris'))
CACHE_TTL = float(os.getenv('CACHE_TTL', '60'))

# Only the fields the page displays: lighter responses, faster listing
LIST_FIELDS = 'items(name,size,timeCreated,updated),prefixes,nextPageToken'
LIST_TIMEOUT = 10
# The default policy keeps retrying for 120 s: a GCS hiccup used to freeze pages for 2 minutes
LIST_RETRY = DEFAULT_RETRY.with_timeout(15)
# After a failed listing, serve the previous ones this long before asking GCS again
RETRY_DELAY = 10
# Unknown path: list again if the cached listing is older than this, it may have just been uploaded
MISS_MAX_AGE = 5
# Folder listings kept in memory (any URL creates one, even a wrong one)
MAX_CACHED_FOLDERS = 1000

MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
          'août', 'septembre', 'octobre', 'novembre', 'décembre']

FILE_KINDS = {
    'code': {'sh', 'bash', 'zsh', 'ps1', 'psm1', 'bat', 'cmd', 'py', 'js', 'ts', 'rb', 'pl',
             'php', 'go', 'rs', 'c', 'cpp', 'h', 'java', 'sql', 'html', 'css', 'json',
             'yml', 'yaml', 'toml', 'ini', 'conf', 'cfg', 'xml', 'tf'},
    'archive': {'zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', '7z', 'rar', 'deb', 'rpm', 'iso'},
    'image': {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'bmp'},
    'doc': {'pdf', 'txt', 'md', 'doc', 'docx', 'odt', 'rtf', 'csv', 'xls', 'xlsx', 'ods'},
}


app = Flask(__name__)


def public_url(blob_name):
    return PUB_URL + '/' + quote(blob_name)


def file_kind(file_name):
    extension = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
    for kind, extensions in FILE_KINDS.items():
        if extension in extensions:
            return kind
    return 'file'


class BucketUnavailable(Exception):
    pass


class Listing:
    """Content of one folder: its sub-folders and files."""

    def __init__(self, folders, files, exists):
        self.folders = folders
        self.files = files
        self.exists = exists
        self.loaded_at = time.monotonic()

    @property
    def age(self):
        return time.monotonic() - self.loaded_at


class FolderCache:
    """Listing of each visited folder, kept CACHE_TTL seconds.

    Only the displayed folder is listed (delimiter '/'), never the whole bucket.
    While a listing is refreshed, or when GCS is unreachable, the previous one
    keeps being served.
    """

    def __init__(self, ttl, client=None):
        self.ttl = ttl
        # Public bucket: no credentials needed. Looking for them takes ~12 s outside Google Cloud.
        self.client = client or storage.Client.create_anonymous_client()
        self.anonymous = True
        self.listings = {}
        self.locks = {}
        self.mutex = threading.Lock()
        self.retry_at = 0

    def get(self, path, max_age=None):
        listing = self.listings.get(path)
        if listing is None or listing.age >= (self.ttl if max_age is None else max_age):
            self.reload(path, listing)
            listing = self.listings.get(path)
        if listing is None:
            raise BucketUnavailable
        return listing

    def reload(self, path, seen):
        with self.mutex:
            lock = self.locks.setdefault(path, threading.Lock())
        # Never listed: wait for the running listing; otherwise keep serving the current one
        if not lock.acquire(blocking=seen is None):
            return
        try:
            if self.listings.get(path) is not seen or time.monotonic() < self.retry_at:
                return  # listed by another request meanwhile, or GCS failed a moment ago
            listing = self.list_folder(path)
            with self.mutex:
                if len(self.listings) >= MAX_CACHED_FOLDERS:
                    self.forget_oldest()
                self.listings[path] = listing
        except Exception:
            app.logger.exception('Listing gs://%s/%s%s failed', BUCKET_NAME, FOLDER_SRC, path)
            self.retry_at = time.monotonic() + RETRY_DELAY
        finally:
            lock.release()

    def forget_oldest(self):
        for path in sorted(self.listings, key=lambda path: self.listings[path].loaded_at)[:MAX_CACHED_FOLDERS // 2]:
            del self.listings[path]
            self.locks.pop(path, None)

    def list_folder(self, path):
        prefix = FOLDER_SRC + path
        try:
            # delimiter='/' returns the sub-folders as "prefixes", even when no
            # placeholder object "folder/" exists (gsutil cp -r, gcloud storage rsync, SDKs...)
            iterator = self.client.list_blobs(BUCKET_NAME, prefix=prefix, delimiter='/', fields=LIST_FIELDS,
                                              timeout=LIST_TIMEOUT, retry=LIST_RETRY)
            blobs = list(iterator)
        except (Unauthorized, Forbidden):
            if not self.anonymous:
                raise
            # The listing is not public: use the default credentials (service account...)
            app.logger.warning('Anonymous listing refused, using the default Google credentials')
            self.client, self.anonymous = storage.Client(), False
            return self.list_folder(path)

        folders = [
            {'name': folder_prefix[len(prefix):].rstrip('/'), 'path': folder_prefix[len(FOLDER_SRC):]}
            for folder_prefix in iterator.prefixes
        ]
        files = [
            {
                'name': blob.name[len(prefix):],
                'url': public_url(blob.name),
                'kind': file_kind(blob.name),
                'size': blob.size or 0,
                'created': blob.time_created,
                'updated': blob.updated,
            }
            # Skip the placeholder object of the folder itself
            for blob in blobs if blob.name != prefix
        ]
        folders.sort(key=lambda folder: folder['name'].casefold())
        files.sort(key=lambda file: file['name'].casefold())
        return Listing(folders, files, exists=bool(blobs or folders))


folder_cache = FolderCache(CACHE_TTL)


@app.template_filter('human_size')
def bytes_to_human_readable(size_bytes):
    suffixes = ['o', 'Ko', 'Mo', 'Go', 'To', 'Po']
    suffix_index = 0
    while size_bytes >= 1024 and suffix_index < len(suffixes) - 1:
        size_bytes /= 1024
        suffix_index += 1
    if suffix_index == 0:
        return f"{size_bytes} {suffixes[0]}"
    return f"{size_bytes:.1f}".replace('.', ',') + f" {suffixes[suffix_index]}"


@app.template_filter('french_date')
def french_date(date, with_time=True):
    date = date.astimezone(TIMEZONE)
    day = f"{date.day} {MONTHS[date.month - 1]} {date.year}"
    return f"{day}, {date:%H:%M}" if with_time else day


@app.context_processor
def inject_globals():
    return {'title': TITLE, 'version': VERSION, 'pub_origin': PUB_ORIGIN}


def find(listing, name):
    for file in listing.files:
        if file['name'] == name:
            return file['url']
    for folder in listing.folders:
        if folder['name'] == name:
            return url_for('browse', subpath=folder['path'])
    return None


@app.route('/', defaults={'subpath': ''})
@app.route('/<path:subpath>')
def browse(subpath):
    if subpath and not subpath.endswith('/'):
        # "/folder/script.sh" -> the public file, "/folder" -> "/folder/", from the parent's listing
        parent, _, name = subpath.rpartition('/')
        parent = parent + '/' if parent else ''
        target = find(folder_cache.get(parent), name)
        if target is None:
            target = find(folder_cache.get(parent, max_age=MISS_MAX_AGE), name)
        if target is None:
            abort(404)
        return redirect(target)

    listing = folder_cache.get(subpath)
    if subpath and not listing.exists:
        listing = folder_cache.get(subpath, max_age=MISS_MAX_AGE)
        if not listing.exists:
            abort(404)

    parts = subpath.rstrip('/').split('/') if subpath else []
    breadcrumbs = [
        {'name': part, 'url': url_for('browse', subpath='/'.join(parts[:index + 1]) + '/')}
        for index, part in enumerate(parts)
    ]
    parent_url = breadcrumbs[-2]['url'] if len(breadcrumbs) > 1 else url_for('browse')

    return render_template(
        'index.html',
        bucket_path=f'gs://{BUCKET_NAME}/{FOLDER_SRC}{subpath}',
        folders=listing.folders,
        files=listing.files,
        total_size=sum(file['size'] for file in listing.files),
        breadcrumbs=breadcrumbs,
        parent_url=parent_url if subpath else None,
    )


@app.route('/healthz')
def healthz():
    # Lists the root the first time but never refreshes it: monitoring costs no GCS requests
    listing = folder_cache.listings.get('') or folder_cache.get('')
    return {'status': 'ok', 'cached_folders': len(folder_cache.listings), 'age': round(listing.age)}


@app.after_request
def compress(response):
    # Pages are ~25 Ko of HTML (inline styles and icons): gzip divides that by ~5
    if (response.status_code == 200 and response.mimetype == 'text/html'
            and 'gzip' in request.accept_encodings and not response.direct_passthrough):
        response.set_data(gzip.compress(response.get_data(), compresslevel=6))
        response.headers['Content-Encoding'] = 'gzip'
        response.vary.add('Accept-Encoding')
    return response


@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', code=404, message="Ce dossier n'existe pas (ou plus)."), 404


@app.errorhandler(BucketUnavailable)
def bucket_unavailable(error):
    return render_template('error.html', code=502, message="Impossible de lire le bucket pour le moment."), 502


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
