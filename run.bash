cd /home/nehemiebarkia/docker/google-storage-web/
docker build -t gcs-list .
docker run --env-file variables.env -p 8080:8080 gcs-list 
 



### VALUES MUST BE LIKE THAT :
#BUCKET_NAME = 'prod-nehemiebarkia-publique'
#FOLDER_SRC = 'scripts/'
#PUB_URL = 'https://storage.googleapis.com/prod-nehemiebarkia-publique'
#TITLE = "SCRIPTS RÉDIGÉS PAR BARKIA NEHEMIE"
###