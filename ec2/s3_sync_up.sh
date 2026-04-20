aws s3 sync ~/Development/feb1 s3://arbo1/feb1/ \
  --exclude "data/options/*" \
  --exclude ".git/*" \
  --exclude ".venv/*"
