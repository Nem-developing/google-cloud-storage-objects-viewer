from flask import Flask, render_template
from google.cloud import storage
from flask import request
import locale
import os

BUCKET_NAME = os.getenv('BUCKET_NAME')
FOLDER_SRC = os.getenv('FOLDER_SRC')
PUB_URL = os.getenv('PUB_URL')
TITLE = os.getenv('TITLE')
version = "1.3"

### VALUES MUST BE LIKE THAT :
#BUCKET_NAME = 'prod-nehemiebarkia-publique'
#FOLDER_SRC = 'scripts/'
#PUB_URL = 'https://storage.googleapis.com/prod-nehemiebarkia-publique'
#TITLE = "SCRIPTS RÉDIGÉS PAR BARKIA NEHEMIE"
###


app = Flask(__name__)


def list_blobs_with_prefix(bucket_name, prefix):
    storage_client = storage.Client()
    blobs = storage_client.list_blobs(bucket_name, prefix=prefix)
    return blobs

def is_a_folder(path):
    if path.endswith("/"):
        return True
    else:
        return False


def get_split(path):
    SPLITED = path.rsplit("/")
    return SPLITED

def path_is_a_file(path):
    if path.endswith('/'):
        return False
    else:
        return True

def define_url(file_name,subpath,is_a_file):
    URL = ''
    if is_a_file:
        URL = PUB_URL + '/' + FOLDER_SRC +  subpath + file_name
    else:
        URL = './' + file_name
    return URL
    
def get_blob_name(file_path, is_a_file):
    name=''
    if is_a_file:
        name = file_path.rsplit('/', 1)[-1]
    else:
        name = file_path.rsplit('/')[-2] + '/'
    return name

def bytes_to_human_readable(size_bytes):
    suffixes = ['o', 'Ko', 'Mo', 'Go', 'To', 'Po']
    suffix_index = 0
    while size_bytes >= 1024 and suffix_index < len(suffixes) - 1:
        size_bytes /= 1024
        suffix_index += 1
    return f"{size_bytes:.2f} {suffixes[suffix_index]}"


@app.route('/', defaults={'subpath': ''})
@app.route('/<path:subpath>')
def catch_all(subpath):
    prefix = FOLDER_SRC + subpath
    blobs = list_blobs_with_prefix(BUCKET_NAME, prefix)
    files = []

    for blob in blobs:
        ###################################################
        file_path = blob.name[len(FOLDER_SRC):]
        path_parents = get_split(file_path)
        deep = file_path.count('/')
        file_size = bytes_to_human_readable(blob.size)
        is_a_file = path_is_a_file(file_path)
        ###################################################

        blob_name = get_blob_name(file_path, is_a_file)
        url = define_url(blob_name,subpath,is_a_file)
        #
        creation_time = blob.time_created.strftime("%A %d %B %Y à %H:%M")
        last_modified = blob.updated.strftime("%A %d %B %Y à %H:%M")
        ###################################################

        file_info = {
            'path': file_path,
            'name': blob_name,
            'size': file_size,
            'path_parents': path_parents,
            'is_a_file': is_a_file,
            'deep': deep,
            'url': url,
            'creation_time': creation_time,
            'last_modified': last_modified,
        }
        deep_in_sub = subpath.count('/')
        if (is_a_file == True):
            if (deep == deep_in_sub):
                files.append(file_info)
        elif (is_a_file == False):
            if (deep == (deep_in_sub + 1)):
                files.append(file_info)

    return render_template('index.html', files=files, subpath=subpath, title=TITLE, version=version)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
