import os
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Flask, abort, redirect, render_template, url_for
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import storage

VERSION = "2.0"

### VALUES MUST BE LIKE THAT :
#BUCKET_NAME = 'prod-nehemiebarkia-publique'
#FOLDER_SRC = 'scripts/'
#PUB_URL = 'https://storage.googleapis.com/prod-nehemiebarkia-publique'
#TITLE = "SCRIPTS RÉDIGÉS PAR BARKIA NEHEMIE"
#TIMEZONE = 'Europe/Paris'
###
BUCKET_NAME = os.environ['BUCKET_NAME']
FOLDER_SRC = os.getenv('FOLDER_SRC', '').strip('/')
FOLDER_SRC = FOLDER_SRC + '/' if FOLDER_SRC else ''
PUB_URL = os.getenv('PUB_URL', f'https://storage.googleapis.com/{BUCKET_NAME}').rstrip('/')
TITLE = os.getenv('TITLE', BUCKET_NAME)
TIMEZONE = ZoneInfo(os.getenv('TIMEZONE', 'Europe/Paris'))

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


def get_storage_client():
    try:
        return storage.Client()
    except DefaultCredentialsError:
        # No credentials available: a public bucket can still be listed anonymously
        return storage.Client.create_anonymous_client()


storage_client = get_storage_client()


def list_folder(prefix):
    # delimiter='/' makes GCS return sub-folders as "prefixes", even when no
    # placeholder object "folder/" exists (gsutil cp -r, gcloud storage rsync, SDKs...).
    # Without it, those folders and everything inside them were invisible.
    iterator = storage_client.list_blobs(BUCKET_NAME, prefix=prefix, delimiter='/')
    blobs = list(iterator)
    return iterator.prefixes, blobs


def public_url(blob_name):
    return PUB_URL + '/' + quote(blob_name)


def file_kind(file_name):
    extension = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
    for kind, extensions in FILE_KINDS.items():
        if extension in extensions:
            return kind
    return 'file'


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
    return {'title': TITLE, 'version': VERSION}


@app.route('/', defaults={'subpath': ''})
@app.route('/<path:subpath>')
def browse(subpath):
    if subpath and not subpath.endswith('/'):
        # "/folder/script.sh" -> the public file, "/folder" -> "/folder/"
        blob_name = FOLDER_SRC + subpath
        if storage_client.bucket(BUCKET_NAME).blob(blob_name).exists():
            return redirect(public_url(blob_name))
        return redirect(url_for('browse', subpath=subpath + '/'))

    prefix = FOLDER_SRC + subpath
    prefixes, blobs = list_folder(prefix)
    if subpath and not prefixes and not blobs:
        abort(404)

    folders = [
        {
            'name': folder_prefix[len(prefix):].rstrip('/'),
            'url': url_for('browse', subpath=folder_prefix[len(FOLDER_SRC):]),
        }
        for folder_prefix in prefixes
    ]
    files = [
        {
            'name': blob.name[len(prefix):],
            'url': public_url(blob.name),
            'kind': file_kind(blob.name),
            'size': blob.size,
            'created': blob.time_created,
            'updated': blob.updated,
        }
        # Skip the placeholder object of the current folder itself
        for blob in blobs if blob.name != prefix
    ]
    folders.sort(key=lambda folder: folder['name'].casefold())
    files.sort(key=lambda file: file['name'].casefold())

    parts = subpath.rstrip('/').split('/') if subpath else []
    breadcrumbs = [
        {'name': part, 'url': url_for('browse', subpath='/'.join(parts[:index + 1]) + '/')}
        for index, part in enumerate(parts)
    ]
    parent_url = breadcrumbs[-2]['url'] if len(breadcrumbs) > 1 else url_for('browse')

    return render_template(
        'index.html',
        bucket_path=f'gs://{BUCKET_NAME}/{prefix}',
        folders=folders,
        files=files,
        total_size=sum(file['size'] for file in files),
        breadcrumbs=breadcrumbs,
        parent_url=parent_url if subpath else None,
    )


@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', code=404, message="Ce dossier n'existe pas (ou plus)."), 404


@app.errorhandler(GoogleAPIError)
def storage_error(error):
    app.logger.exception('Google Cloud Storage request failed')
    return render_template('error.html', code=502, message="Impossible de lire le bucket pour le moment."), 502


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
