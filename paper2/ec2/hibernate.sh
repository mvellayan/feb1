#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="instance.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE found. Run deploy.sh first."; exit 1; }
source "$ENV_FILE"

echo "Hibernating $INSTANCE_ID ..."
aws ec2 stop-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --hibernate

aws ec2 wait instance-stopped --region "$REGION" --instance-ids "$INSTANCE_ID"
echo "Instance hibernated."
