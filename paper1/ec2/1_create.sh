#!/usr/bin/env bash

set -euo pipefail

REGION="${REGION:-us-east-1}"
KEY_NAME="${KEY_NAME:-Muthu-06Apr25}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/${KEY_NAME}.pem}"
INSTANCE_INFO_FILE="${INSTANCE_INFO_FILE:-ec2_instance.txt}"

RUN_OUTPUT="$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id 'ami-01b14b7ad41e17ba4' \
  --instance-type 't2.medium' \
  --key-name "$KEY_NAME" \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"Encrypted":false,"DeleteOnTermination":true,"Iops":3000,"SnapshotId":"snap-0f0d385a3abb4becf","VolumeSize":20,"VolumeType":"gp3","Throughput":125}}]' \
  --network-interfaces '[{"AssociatePublicIpAddress":true,"DeviceIndex":0,"Groups":["sg-00d655d1684b2187f"]}]' \
  --credit-specification '{"CpuCredits":"standard"}' \
  --tag-specifications '[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"Paper"}]}]' \
  --iam-instance-profile '{"Arn":"arn:aws:iam::775579389744:instance-profile/Muthu-EC2-Arbo-Dev-Role"}' \
  --metadata-options '{"HttpEndpoint":"enabled","HttpPutResponseHopLimit":2,"HttpTokens":"required","InstanceMetadataTags":"enabled"}' \
  --private-dns-name-options '{"HostnameType":"ip-name","EnableResourceNameDnsARecord":true,"EnableResourceNameDnsAAAARecord":false}' \
  --count '1')"

INSTANCE_ID="$(printf '%s\n' "$RUN_OUTPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Instances"][0]["InstanceId"])')"

printf 'Instance %s launched — waiting for running state...\n' "$INSTANCE_ID"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
printf 'Instance is running.\n'

INSTANCE_JSON="$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0]' \
  --output json)"

PUBLIC_DNS="$(printf '%s\n' "$INSTANCE_JSON"  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("PublicDnsName",""))')"
PUBLIC_IP="$(printf '%s\n'  "$INSTANCE_JSON"  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("PublicIpAddress",""))')"
PRIVATE_IP="$(printf '%s\n' "$INSTANCE_JSON"  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("PrivateIpAddress",""))')"
AVAILABILITY_ZONE="$(printf '%s\n' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Placement"]["AvailabilityZone"])')"

cat > "$INSTANCE_INFO_FILE" <<EOF
REGION=$REGION
INSTANCE_ID=$INSTANCE_ID
PUBLIC_DNS=$PUBLIC_DNS
PUBLIC_IP=$PUBLIC_IP
PRIVATE_IP=$PRIVATE_IP
AVAILABILITY_ZONE=$AVAILABILITY_ZONE
SSH_USER=ec2-user
KEY_NAME=$KEY_NAME
KEY_PATH=$KEY_PATH
IBG_SERVICE=ibgateway.service
EOF

printf '\nInstance info saved to %s\n' "$INSTANCE_INFO_FILE"
printf '\nSSH:\n  ssh -i "%s" ec2-user@%s\n' "$KEY_PATH" "$PUBLIC_DNS"
printf '\nInstall:\n  IB_USERNAME=<user> IB_PASSWORD=<pass> bash ./2_install.sh\n'
printf '\nStart gateway after install:\n  ssh -i "%s" ec2-user@%s '\''sudo systemctl start ibgateway && sudo systemctl status ibgateway --no-pager'\''\n' "$KEY_PATH" "$PUBLIC_DNS"
