#!/usr/bin/env bash
set -euo pipefail

REGION="us-east-1"
SG="sg-00d655d1684b2187f"
KEY_NAME="Muthu-06Apr25"
INSTANCE_TYPE="t3.medium"
ENV_FILE="instance.env"

# Amazon Linux 2 latest AMI in us-east-1
AMI_ID=$(aws ec2 describe-images \
  --region "$REGION" \
  --owners amazon \
  --filters "Name=name,Values=amzn2-ami-hvm-2.0.*-x86_64-gp2" \
            "Name=state,Values=available" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" \
  --output text)

echo "Using AMI: $AMI_ID"

USER_DATA=$(cat <<'USERDATA'
#!/bin/bash
set -e
exec > /var/log/user-data.log 2>&1

# System update
yum update -y

# Python3 + uv
yum install -y python3 python3-pip curl
curl -LsSf https://astral.sh/uv/install.sh | sh

# EPEL + MATE desktop + XRDP
amazon-linux-extras install -y epel
amazon-linux-extras install -y mate-desktop1.x
yum install -y xrdp
systemctl enable xrdp
systemctl start xrdp

# Point xrdp's window manager script to mate-session
cat > /usr/libexec/xrdp/startwm-bash.sh <<'EOF'
#!/bin/sh
exec mate-session
EOF
chmod +x /usr/libexec/xrdp/startwm-bash.sh

# Java (required by IB Gateway)
yum install -y java-11-amazon-corretto-headless

# IB Gateway stable
IB_INSTALLER="/tmp/ibgateway-stable-linux-x64.sh"
curl -L -o "$IB_INSTALLER" \
  "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
chmod +x "$IB_INSTALLER"
sudo -u ec2-user bash "$IB_INSTALLER" -q -dir /home/ec2-user/ibgateway

# Desktop shortcut
DESKTOP_DIR="/home/ec2-user/Desktop"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/IBGateway.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=IB Gateway
Comment=Interactive Brokers Gateway
Exec=/home/ec2-user/ibgateway/ibgateway
Icon=/home/ec2-user/ibgateway/ibgateway.png
Terminal=false
Categories=Finance;
EOF
chmod +x "$DESKTOP_DIR/IBGateway.desktop"
chown -R ec2-user:ec2-user "$DESKTOP_DIR"

echo "Bootstrap complete"
USERDATA
)

# IAM role + instance profile for S3 access
if ! aws iam get-role --role-name trading-vm-role &>/dev/null; then
  aws iam create-role \
    --role-name trading-vm-role \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam attach-role-policy \
    --role-name trading-vm-role \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess
fi
if ! aws iam get-instance-profile --instance-profile-name trading-vm-profile &>/dev/null; then
  aws iam create-instance-profile --instance-profile-name trading-vm-profile
  aws iam add-role-to-instance-profile \
    --instance-profile-name trading-vm-profile \
    --role-name trading-vm-role
fi

# Launch with hibernation (encrypted root required)
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG" \
  --hibernation-options "Configured=true" \
  --block-device-mappings '[{
    "DeviceName": "/dev/xvda",
    "Ebs": {
      "VolumeSize": 30,
      "VolumeType": "gp3",
      "Encrypted": true,
      "DeleteOnTermination": true
    }
  }]' \
  --user-data "$USER_DATA" \
  --iam-instance-profile Name=trading-vm-profile \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=trading-vm}]' \
  --query "Instances[0].InstanceId" \
  --output text)

echo "Instance launched: $INSTANCE_ID"
echo "Waiting for instance to be running..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

# Allocate and associate Elastic IP
ALLOC_ID=$(aws ec2 allocate-address \
  --region "$REGION" \
  --domain vpc \
  --query "AllocationId" \
  --output text)

aws ec2 associate-address \
  --region "$REGION" \
  --instance-id "$INSTANCE_ID" \
  --allocation-id "$ALLOC_ID" > /dev/null

PUBLIC_IP=$(aws ec2 describe-addresses \
  --region "$REGION" \
  --allocation-ids "$ALLOC_ID" \
  --query "Addresses[0].PublicIp" \
  --output text)

cat > "$ENV_FILE" <<EOF
INSTANCE_ID=$INSTANCE_ID
ALLOC_ID=$ALLOC_ID
PUBLIC_IP=$PUBLIC_IP
REGION=$REGION
EOF

echo ""
echo "Done. Instance: $INSTANCE_ID | IP: $PUBLIC_IP"
echo "RDP to $PUBLIC_IP:3389 (user: ec2-user)"
echo "Bootstrap running in background — wait ~5 min, then set password via SSH:"
echo "  ssh -i ~/.ssh/${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
echo "  sudo passwd ec2-user"
echo "State saved to $ENV_FILE"
