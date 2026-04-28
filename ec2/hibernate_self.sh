#!/bin/bash
#
set -euo pipefail
#
#
#
INSTANCE_ID=$(curl -fsS http://169.254.169.254/latest/meta-data/instance-id)
#
REGION=$(curl -fsS http://169.254.169.254/latest/dynamic/instance-identity/document | \
   grep region | awk -F\" '{print $4}')
#
sh ~/Development/feb1/ec2/send_email.sh suspending arbo2 ec2
#
aws ec2 stop-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --hibernate
