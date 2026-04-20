#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="instance.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE found. Run deploy.sh first."; exit 1; }
source "$ENV_FILE"

echo "Resuming $INSTANCE_ID ..."
aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

# Re-associate EIP (it stays allocated but association may need refresh)
aws ec2 associate-address \
  --region "$REGION" \
  --instance-id "$INSTANCE_ID" \
  --allocation-id "$ALLOC_ID" \
  --allow-reassociation > /dev/null

echo "Instance running. RDP to $PUBLIC_IP:3389"
