#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="instance.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE found. Run deploy.sh first."; exit 1; }
source "$ENV_FILE"

aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].{State:State.Name,IP:PublicIpAddress,Type:InstanceType,AZ:Placement.AvailabilityZone}" \
  --output table

echo "Elastic IP: $PUBLIC_IP (AllocationId: $ALLOC_ID)"
