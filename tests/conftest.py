import os

# app.py reads its configuration at import time
os.environ.update(
    BUCKET_NAME='bucket',
    FOLDER_SRC='scripts/',
    PUB_URL='https://pub.example',
    TITLE='Titre',
)
