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
#CACHE_TTL = 30
###
BUCKET_NAME = os.environ['BUCKET_NAME']
FOLDER_SRC = os.getenv('FOLDER_SRC', '').strip('/')
FOLDER_SRC = FOLDER_SRC + '/' if FOLDER_SRC else ''
PUB_URL = os.getenv('PUB_URL', f'https://storage.googleapis.com/{BUCKET_NAME}').rstrip('/')
PUB_ORIGIN = '{0.scheme}://{0.netloc}'.format(urlsplit(PUB_URL))
TITLE = os.getenv('TITLE', BUCKET_NAME)
TIMEZONE = ZoneInfo(os.getenv('TIMEZONE', 'Europe/Paris'))
CACHE_TTL = float(os.getenv('CACHE_TTL', '30'))

# Only the fields the page displays: lighter responses, faster listing
LIST_FIELDS = 'items(name,size,timeCreated,updated),nextPageToken'
LIST_TIMEOUT = 10
# The default policy keeps retrying for 120 s: a GCS hiccup used to freeze pages for 2 minutes
LIST_RETRY = DEFAULT_RETRY.with_timeout(15)
# After a failed reload, keep serving the previous copy this long before trying again
RETRY_DELAY = 10
# Unknown path: reload if the copy is older than this, the object may have just been uploaded
MISS_MAX_AGE = 5

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


class Snapshot:
    """Copy of the FOLDER_SRC tree: {folder path: folder} and {file path: file}."""

    def __init__(self, folders, files):
        self.folders = folders
        self.files = files
        self.loaded_at = time.monotonic()

    @property
    def age(self):
        return time.monotonic() - self.loaded_at


def new_folder(name, path):
    return {'name': name, 'path': path, 'folders': [], 'files': [], 'size': 0, 'count': 0, 'updated': None}


def build_snapshot(blobs):
    root = new_folder('', '')
    folders = {'': root}
    files = {}

    for blob in blobs:
        path = blob.name[len(FOLDER_SRC):]
        # Folders come from the object paths, so they exist with or without a
        # placeholder object "folder/" (gsutil cp -r, gcloud storage rsync, SDKs...)
        ancestors = [root]
        for name in path.split('/')[:-1]:
            folder_path = ancestors[-1]['path'] + name + '/'
            if folder_path not in folders:
                folders[folder_path] = new_folder(name, folder_path)
                ancestors[-1]['folders'].append(folders[folder_path])
            ancestors.append(folders[folder_path])

        if not path or path.endswith('/'):
            continue  # placeholder object of a folder, not a file

        file = {
            'name': path.rsplit('/', 1)[-1],
            'path': path,
            'url': public_url(blob.name),
            'kind': file_kind(path),
            'size': blob.size or 0,
            'created': blob.time_created,
            'updated': blob.updated,
        }
        ancestors[-1]['files'].append(file)
        files[path] = file
        for folder in ancestors:
            folder['size'] += file['size']
            folder['count'] += 1
            if folder['updated'] is None or file['updated'] > folder['updated']:
                folder['updated'] = file['updated']

    for folder in folders.values():
        folder['folders'].sort(key=lambda child: child['name'].casefold())
        folder['files'].sort(key=lambda child: child['name'].casefold())
    return Snapshot(folders, files)


class BucketIndex:
    """In-memory copy of the bucket, so browsing never waits for GCS.

    The whole FOLDER_SRC tree is listed in one go (one request per 1000 objects)
    and reloaded when older than CACHE_TTL. While a reload runs, or when GCS is
    unreachable, the previous copy keeps being served.
    """

    def __init__(self, ttl, client=None):
        self.ttl = ttl
        # Public bucket: no credentials needed. Looking for them takes ~12 s outside Google Cloud.
        self.client = client or storage.Client.create_anonymous_client()
        self.anonymous = True
        self.snapshot = None
        self.retry_at = 0
        self.lock = threading.Lock()

    def get(self, max_age=None):
        snapshot = self.snapshot
        if snapshot is None or snapshot.age >= (self.ttl if max_age is None else max_age):
            self.reload(snapshot)
            snapshot = self.snapshot
        if snapshot is None:
            raise BucketUnavailable
        return snapshot

    def reload(self, seen):
        # Without any copy, wait for the running reload; otherwise keep serving the current copy
        if not self.lock.acquire(blocking=seen is None):
            return
        try:
            if self.snapshot is not seen or time.monotonic() < self.retry_at:
                return  # reloaded by another request meanwhile, or failed a moment ago
            self.snapshot = build_snapshot(self.list_objects())
        except Exception:
            app.logger.exception('Listing gs://%s/%s failed', BUCKET_NAME, FOLDER_SRC)
            self.retry_at = time.monotonic() + RETRY_DELAY
        finally:
            self.lock.release()

    def list_objects(self):
        try:
            return list(self.client.list_blobs(BUCKET_NAME, prefix=FOLDER_SRC, fields=LIST_FIELDS,
                                               timeout=LIST_TIMEOUT, retry=LIST_RETRY))
        except (Unauthorized, Forbidden):
            if not self.anonymous:
                raise
            # The listing is not public: use the default credentials (service account...)
            app.logger.warning('Anonymous listing refused, using the default Google credentials')
            self.client, self.anonymous = storage.Client(), False
            return self.list_objects()


bucket_index = BucketIndex(CACHE_TTL)


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


def find(snapshot, subpath):
    if not subpath or subpath.endswith('/'):
        return snapshot.folders.get(subpath)
    return snapshot.files.get(subpath) or snapshot.folders.get(subpath + '/')


@app.route('/', defaults={'subpath': ''})
@app.route('/<path:subpath>')
def browse(subpath):
    entry = find(bucket_index.get(), subpath)
    if entry is None:
        entry = find(bucket_index.get(max_age=MISS_MAX_AGE), subpath)
    if entry is None:
        abort(404)
    if 'folders' not in entry:
        # "/folder/script.sh" -> the public file
        return redirect(entry['url'])
    if entry['path'] != subpath:
        # "/folder" -> "/folder/"
        return redirect(url_for('browse', subpath=entry['path']))

    parts = subpath.rstrip('/').split('/') if subpath else []
    breadcrumbs = [
        {'name': part, 'url': url_for('browse', subpath='/'.join(parts[:index + 1]) + '/')}
        for index, part in enumerate(parts)
    ]
    parent_url = breadcrumbs[-2]['url'] if len(breadcrumbs) > 1 else url_for('browse')

    return render_template(
        'index.html',
        bucket_path=f'gs://{BUCKET_NAME}/{FOLDER_SRC}{subpath}',
        folders=entry['folders'],
        files=entry['files'],
        total_size=entry['size'],
        breadcrumbs=breadcrumbs,
        parent_url=parent_url if subpath else None,
    )


@app.route('/healthz')
def healthz():
    # Loads the copy the first time but never reloads it: monitoring costs no GCS requests
    snapshot = bucket_index.snapshot or bucket_index.get()
    return {'status': 'ok', 'objects': len(snapshot.files), 'age': round(snapshot.age)}


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
