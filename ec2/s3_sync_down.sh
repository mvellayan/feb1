echo "`date`: syncing down from s3"
aws s3 sync s3://arbo1/feb1/ ~/Development/feb1 
