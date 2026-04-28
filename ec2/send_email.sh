
if [ $# -lt 1 ]; then
  echo "Usage: $0 \"enter a text message to send myself\""
  exit 1
fi

MESSAGE="$1"

aws ses send-email --region us-east-1 --from muthu.vellayan@nayalle.com \
  --destination ToAddresses=muthu.vellayan@gmail.com \
  --message 'Subject={Data=Arbo702 Trading},Body={Text={Data=$MESSAGE}}'
