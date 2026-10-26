set -e
cd "$(dirname "$0")"
docker build -t gcs-list .
# The previous container keeps running if the build fails
docker rm -f gcs-list 2>/dev/null || true
docker run -d --name gcs-list --restart unless-stopped --env-file variables.env -p 8080:8080 gcs-list
 



### VALUES MUST BE LIKE THAT :
#BUCKET_NAME = 'prod-nehemiebarkia-publique'
#FOLDER_SRC = 'scripts/'
#PUB_URL = 'https://storage.googleapis.com/prod-nehemiebarkia-publique'
#TITLE = "SCRIPTS RÉDIGÉS PAR BARKIA NEHEMIE"
#CACHE_TTL = 30
###
