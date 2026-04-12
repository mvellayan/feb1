#!/usr/bin/env bash
set -euo pipefail

# =========================
# Edit these values
# =========================
REGION="us-east-1"
INSTANCE_TYPE="t3.large"         # safer for GUI + IB Gateway/TWS than very tiny sizes
#KEY_NAME="~/.ssh/Muthu-06Apr25.pem"     # must already exist in this region
KEY_NAME="${KEY_NAME:-Muthu-06Apr25}"
INSTANCE_NAME="ibkr-gui-host"
MY_IP_CIDR="71.191.10.43"     # replace with your public IP/CIDR
ROOT_VOL_SIZE=20

# Optional: if you already have an IAM instance profile for S3 access, set it here.
# Example: IAM_INSTANCE_PROFILE="MyEc2S3Profile"
IAM_INSTANCE_PROFILE=""

# =========================
# Discover default VPC + subnet
# =========================
VPC_ID="vpc-068b0176a20af6764"
SUBNET_ID="subnet-0c7c24f30bba1c578"
SG_ID="sg-00d655d1684b2187f"

# =========================
# Launch latest AL2023 x86_64 using AWS public SSM parameter
# =========================
RUN_ARGS=(
  --region "$REGION"
  ec2 run-instances
  --image-id "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
  --instance-type "$INSTANCE_TYPE"
  --key-name "$KEY_NAME"
  --security-group-ids "$SG_ID"
  --subnet-id "$SUBNET_ID"
  --associate-public-ip-address
  --block-device-mappings "[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":${ROOT_VOL_SIZE},\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]"
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_NAME}}]"
  --iam-instance-profile '{"Arn":"arn:aws:iam::775579389744:instance-profile/Muthu-EC2-Arbo-Dev-Role"}' \
  --query 'Instances[0].InstanceId'
  --output text
)

INSTANCE_ID=$(aws "${RUN_ARGS[@]}")

echo "Launched instance: $INSTANCE_ID"

aws ec2 wait instance-running \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID"

PUBLIC_DNS=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicDnsName' \
  --output text)

PUBLIC_IP=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

echo
echo "Instance is running."
echo "INSTANCE_ID=$INSTANCE_ID"
echo "PUBLIC_IP=$PUBLIC_IP"
echo "PUBLIC_DNS=$PUBLIC_DNS"
echo
echo "SSH command:"
echo "ssh -i ~/.ssh/${KEY_NAME}.pem ec2-user@${PUBLIC_DNS}"
echo
echo "Later, DCV URL:"
echo "https://${PUBLIC_DNS}:8443"
