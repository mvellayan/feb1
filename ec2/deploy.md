# EC2 Trading VM

t3.medium, Amazon Linux 2023, us-east-1  
Security group: `sg-00d655d1684b2187f` | Key pair: `Muthu-06Apr25`  
Installed: Python3, uv, XFCE + XRDP, IB Gateway (stable)  
State persisted in `instance.env`.

## Scripts

| Script | Purpose |
|---|---|
| `deploy.sh` | Launch instance, allocate Elastic IP, run bootstrap |
| `hibernate.sh` | Hibernate VM (RAM saved to disk, state fully restored on resume) |
| `resume.sh` | Resume hibernated VM |
| `status.sh` | Show current instance state |
| `destroy.sh` | Terminate instance and release Elastic IP |

## Usage

```bash
./deploy.sh       # ~2 min to launch; wait ~5 min for bootstrap to finish
./status.sh       # check state
./hibernate.sh    # suspend
./resume.sh       # resume
./destroy.sh      # permanent delete (prompts for confirmation)
```

## First-Time RDP Setup

After `deploy.sh`, SSH in to set a password for RDP:

```bash
ssh -i ~/.ssh/Muthu-06Apr25.pem ec2-user@<PUBLIC_IP>
sudo passwd ec2-user
```

Then connect via RDP to `<PUBLIC_IP>:3389` (user: `ec2-user`).  
On macOS use **Microsoft Remote Desktop** from the App Store.

## Prerequisites

- Security group `sg-00d655d1684b2187f` must allow inbound **TCP 3389** (RDP) and **TCP 22** (SSH)
- AWS CLI configured for `us-east-1`
- Hibernation requires encrypted root volume — handled automatically by `deploy.sh` (30GB gp3 encrypted)

## Bootstrap Log

If something doesn't install correctly, check the log on the instance:

```bash
sudo cat /var/log/user-data.log
```

## IB Gateway

- Installed to `/home/ec2-user/ibgateway/`
- Desktop shortcut created at `/home/ec2-user/Desktop/IBGateway.desktop`
- Launch from the XFCE desktop or run: `/home/ec2-user/ibgateway/ibgateway`
- Set API socket port to `4001` in IB Gateway settings after login
