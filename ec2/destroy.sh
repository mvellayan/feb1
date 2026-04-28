#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="instance.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE found. Run deploy.sh first."; exit 1; }
source "$ENV_FILE"

read -rp "This will PERMANENTLY delete instance $INSTANCE_ID and release IP $PUBLIC_IP. Type 'yes' to confirm: " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "Aborted."; exit 0; }

echo "Terminating $INSTANCE_ID ..."
aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$INSTANCE_ID"

echo "Releasing Elastic IP $ALLOC_ID ..."
aws ec2 release-address --region "$REGION" --allocation-id "$ALLOC_ID"

rm -f "$ENV_FILE"

echo "Cleaning up IAM instance profile ..."
aws iam remove-role-from-instance-profile \
  --instance-profile-name trading-vm-profile \
  --role-name trading-vm-role 2>/dev/null || true
aws iam delete-instance-profile \
  --instance-profile-name trading-vm-profile 2>/dev/null || true
aws iam detach-role-policy \
  --role-name trading-vm-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess 2>/dev/null || true
aws iam delete-role-policy \
  --role-name trading-vm-role \
  --policy-name trading-vm-ec2-self 2>/dev/null || true
aws iam delete-role-policy \
  --role-name trading-vm-role \
  --policy-name trading-vm-sns 2>/dev/null || true
aws iam delete-role-policy \
  --role-name trading-vm-role \
  --policy-name trading-vm-ses 2>/dev/null || true
aws iam delete-role --role-name trading-vm-role 2>/dev/null || true

echo "Done. Instance terminated and IP released."
