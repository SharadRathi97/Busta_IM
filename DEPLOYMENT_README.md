# Busta IM Deployment Guide (Internet + PostgreSQL)

This guide explains exactly where to host the system, where to place files, which tools to use, and how to deploy `Busta_IM` for multi-user internet access.

## 1. Recommended Hosting Stack

The app supports multiple providers. This file includes:

1. DigitalOcean path (Sections 1-9)
2. Oracle Cloud path (Section 10, requested path)

Common stack:

1. Cloud VM: Ubuntu Linux
2. Database: PostgreSQL
3. Web server: Nginx
4. App server: Gunicorn + systemd
5. SSL: Certbot (Let's Encrypt)
6. DNS: Provider DNS or external DNS

## 2. Accounts and Tools You Need

Create these first:

1. Cloud account (DigitalOcean or Oracle Cloud)
2. Domain name (Namecheap, GoDaddy, Google Domains, etc.)
3. Git hosting account (GitHub/GitLab/Bitbucket)
4. SSH key on your local machine

Install locally:

1. `git`
2. `python3`
3. `ssh`

## 3. Exact File Locations

### Local machine

1. Project repo: your existing `Busta_IM` folder
2. Local environment file: `backend/.env` (do not commit)

### Server

1. Project root: `/opt/busta_im`
2. Project env file: `/opt/busta_im/backend/.env`
3. Gunicorn service file: `/etc/systemd/system/busta-im.service`
4. Nginx site file: `/etc/nginx/sites-available/busta-im`
5. Nginx enabled symlink: `/etc/nginx/sites-enabled/busta-im`
6. Static files: `/opt/busta_im/backend/staticfiles`

## 4. Step-by-Step Deployment

## Step 1: Push latest code to git

Run on your local machine:

```bash
cd /Users/shrathi/Library/CloudStorage/OneDrive-Deloitte(O365D)/Desktop/Repos/Busta_IM
git add .
git commit -m "Prepare deployment"
git push
```

## Step 2: Create managed PostgreSQL (DigitalOcean)

DigitalOcean UI:

1. `Create` -> `Databases`
2. Choose `PostgreSQL`
3. Choose region (use same region as app server)
4. Create cluster
5. Save these values:
   1. Host
   2. Port
   3. Database name
   4. Username
   5. Password
6. In database `Trusted Sources`, allow your app Droplet (after Droplet is created)

## Step 3: Create app server Droplet

DigitalOcean UI:

1. `Create` -> `Droplets`
2. OS: Ubuntu LTS
3. Size: at least 2 GB RAM
4. Region: same as database region
5. Auth: SSH key
6. Create Droplet and copy public IP

## Step 4: Configure firewall

Use DigitalOcean Cloud Firewall:

1. Allow inbound `22` from your IP only
2. Allow inbound `80` from all
3. Allow inbound `443` from all
4. Deny all other inbound traffic

## Step 5: Connect domain to server

1. Add domain in DigitalOcean Networking -> DNS
2. At registrar, set nameservers to DigitalOcean nameservers
3. Create records:
   1. `A` for `@` -> Droplet IP
   2. `A` for `www` -> Droplet IP

## Step 6: SSH to server and install packages

As root first:

```bash
ssh root@YOUR_DROPLET_IP
apt update && apt upgrade -y
apt install -y python3-venv python3-pip nginx git
adduser deploy
usermod -aG sudo deploy
mkdir -p /opt/busta_im
chown -R deploy:deploy /opt/busta_im
```

Reconnect as deploy user:

```bash
ssh deploy@YOUR_DROPLET_IP
```

## Step 7: Clone repo and install Python dependencies

```bash
cd /opt
git clone <YOUR_REPO_URL> busta_im
cd /opt/busta_im
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Step 8: Configure production environment file

```bash
cp /opt/busta_im/backend/.env.example /opt/busta_im/backend/.env
nano /opt/busta_im/backend/.env
```

Set values like this:

```env
DJANGO_ENV=production
DJANGO_SECRET_KEY=<LONG_RANDOM_SECRET>
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
DJANGO_TIME_ZONE=Asia/Kolkata
DATABASE_URL=postgresql://DBUSER:DBPASSWORD@DBHOST:DBPORT/DBNAME?sslmode=require
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true
DJANGO_SECURE_HSTS_PRELOAD=true
```

Protect the env file:

```bash
chmod 600 /opt/busta_im/backend/.env
```

## Step 9: Run migrations and collect static

```bash
cd /opt/busta_im/backend
/opt/busta_im/.venv/bin/python manage.py migrate
/opt/busta_im/.venv/bin/python manage.py collectstatic --noinput
/opt/busta_im/.venv/bin/python manage.py check --deploy
```

If first-time setup:

```bash
/opt/busta_im/.venv/bin/python manage.py createsuperuser
```

## Step 10: Configure Gunicorn systemd service

Copy provided service template:

```bash
sudo cp /opt/busta_im/deploy/systemd/busta-im.service /etc/systemd/system/busta-im.service
sudo nano /etc/systemd/system/busta-im.service
```

Verify these fields:

1. `User=deploy` and `Group=deploy` (or configured runtime user)
2. `WorkingDirectory=/opt/busta_im/backend`
3. `EnvironmentFile=/opt/busta_im/backend/.env`
4. `ExecStart=/opt/busta_im/.venv/bin/gunicorn ...`

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable busta-im
sudo systemctl start busta-im
sudo systemctl status busta-im
```

## Step 11: Configure Nginx

Copy provided Nginx template:

```bash
sudo cp /opt/busta_im/deploy/nginx/busta-im.conf /etc/nginx/sites-available/busta-im
sudo nano /etc/nginx/sites-available/busta-im
```

Update:

1. `server_name yourdomain.com www.yourdomain.com;`
2. Keep static path: `/opt/busta_im/backend/staticfiles/`
3. Keep app upstream: `127.0.0.1:8001`

Enable site:

```bash
sudo ln -s /etc/nginx/sites-available/busta-im /etc/nginx/sites-enabled/busta-im
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## Step 12: Enable HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com --redirect -m you@example.com --agree-tos --no-eff-email
sudo certbot renew --dry-run
```

## Step 13: Verify from multiple devices

1. Open `https://yourdomain.com/login/` from different machines/networks
2. Test login with multiple users
3. Verify CRUD actions and production flow
4. Check logs:

```bash
sudo journalctl -u busta-im -f
sudo tail -f /var/log/nginx/error.log
```

## 5. PostgreSQL Data Migration (If You Already Have SQLite Data)

Use this once to move data from SQLite to PostgreSQL.

## Step A: Export from SQLite (local machine)

```bash
cd /Users/shrathi/Library/CloudStorage/OneDrive-Deloitte(O365D)/Desktop/Repos/Busta_IM/backend
../.venv/bin/python manage.py dumpdata \
  --exclude contenttypes \
  --exclude auth.permission \
  --natural-foreign --natural-primary \
  > data_migration.json
```

## Step B: Copy JSON to server

```bash
scp data_migration.json deploy@YOUR_DROPLET_IP:/opt/busta_im/backend/
```

## Step C: Load into PostgreSQL (server)

```bash
cd /opt/busta_im/backend
/opt/busta_im/.venv/bin/python manage.py migrate
/opt/busta_im/.venv/bin/python manage.py loaddata data_migration.json
```

## Step D: Validate

1. Login and verify users/vendors/materials/orders
2. Compare record counts between old SQLite and new PostgreSQL
3. Keep a backup of old `db.sqlite3`

## 6. Regular Update/Deploy Procedure

Whenever you push new code:

```bash
ssh deploy@YOUR_DROPLET_IP
cd /opt/busta_im
git pull
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart busta-im
sudo systemctl reload nginx
```

## 7. Useful Commands

Check Gunicorn service:

```bash
sudo systemctl status busta-im
sudo journalctl -u busta-im -f
```

Check Nginx:

```bash
sudo nginx -t
sudo systemctl status nginx
sudo tail -f /var/log/nginx/error.log
```

Check PostgreSQL connectivity from app host:

```bash
cd /opt/busta_im/backend
/opt/busta_im/.venv/bin/python manage.py dbshell
```

## 8. Security Checklist Before Go-Live

1. `DJANGO_ENV=production`
2. Strong `DJANGO_SECRET_KEY`
3. `DEBUG` must be false (already enforced by production settings)
4. Correct `DJANGO_ALLOWED_HOSTS`
5. Correct `DJANGO_CSRF_TRUSTED_ORIGINS` with `https://...`
6. PostgreSQL credentials are private
7. Firewall only allows `22`, `80`, `443`
8. SSL certificate active and auto-renewing
9. `/opt/busta_im/backend/.env` permission is `600`

## 9. Files Already Added In This Repo For Deployment

1. `backend/.env.example`
2. `deploy/systemd/busta-im.service`
3. `deploy/nginx/busta-im.conf`
4. `backend/config/settings.py`
5. `backend/config/settings_base.py`
6. `backend/config/settings_dev.py`
7. `backend/config/settings_prod.py`
8. `backend/config/env.py`

## 10. Oracle Cloud Deployment (Step-by-Step + PostgreSQL)

This is the Oracle Cloud path to deploy the same app for internet users, with PostgreSQL enabled.

Architecture in this section:

1. OCI Compute instance (Ubuntu): Django + Gunicorn + Nginx
2. PostgreSQL on the same instance (lowest cost)
3. OCI DNS or external registrar DNS

## Step 1: Create OCI account and choose home region

1. Sign up at Oracle Cloud Infrastructure.
2. Confirm your tenancy and pick your home region.
3. In OCI Console, create a compartment for this app (for example: `busta-prod`).

## Step 2: Create network (VCN with internet connectivity wizard)

In OCI Console:

1. Open navigation menu -> `Networking` -> `Virtual cloud networks`.
2. Click `Start VCN Wizard` (or `Create VCN` with internet connectivity).
3. Choose `VCN with Internet Connectivity`.
4. Set:
   1. VCN name (example: `busta-vcn`)
   2. Compartment: `busta-prod`
   3. Accept default CIDRs unless you need custom ranges
5. Create.

This creates:

1. VCN
2. Internet Gateway
3. Public subnet
4. Route table
5. Default security list

## Step 3: Launch Ubuntu compute instance

In OCI Console:

1. `Compute` -> `Instances` -> `Create instance`
2. Name: `busta-app-01`
3. Compartment: `busta-prod`
4. Image: Ubuntu LTS
5. Shape:
   1. Always Free option: `VM.Standard.A1.Flex` (Arm)
   2. Alternative: `VM.Standard.E2.1.Micro`
6. Networking:
   1. Select the VCN/subnet created in Step 2
   2. Keep `Assign public IPv4 address` enabled
7. Add your SSH public key
8. Create instance

Note: for Ubuntu images, the default SSH user is `ubuntu`.

## Step 4: Configure OCI network inbound rules

In the subnet security list (or NSG), add ingress rules:

1. TCP `22` from your IP only (recommended) or temporary `0.0.0.0/0` during setup
2. TCP `80` from `0.0.0.0/0`
3. TCP `443` from `0.0.0.0/0`
4. Do not open `5432` publicly

## Step 5: SSH into the instance

From local machine:

```bash
chmod 400 /path/to/oci_private_key
ssh -i /path/to/oci_private_key ubuntu@<INSTANCE_PUBLIC_IP>
```

## Step 6: Install OS packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git nginx postgresql postgresql-contrib libpq-dev certbot python3-certbot-nginx ufw
```

## Step 7: Configure server firewall (UFW)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

## Step 8: Create PostgreSQL database and user

```bash
sudo -u postgres psql
```

Run in `psql`:

```sql
CREATE DATABASE busta_im;
CREATE USER busta_user WITH ENCRYPTED PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE busta_im TO busta_user;
\q
```

Grant schema rights:

```bash
sudo -u postgres psql -d busta_im -c "GRANT ALL ON SCHEMA public TO busta_user;"
```

## Step 9: Create deployment user and app directory

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG sudo deploy
sudo mkdir -p /opt/busta_im
sudo chown -R deploy:deploy /opt/busta_im
```

Switch to deploy user:

```bash
sudo -iu deploy
```

## Step 10: Clone project and install Python dependencies

```bash
cd /opt
git clone <YOUR_REPO_URL> busta_im
cd /opt/busta_im
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Step 11: Configure production `.env` with PostgreSQL URL

```bash
cp /opt/busta_im/backend/.env.example /opt/busta_im/backend/.env
nano /opt/busta_im/backend/.env
```

Use:

```env
DJANGO_ENV=production
DJANGO_SECRET_KEY=<LONG_RANDOM_SECRET>
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,<INSTANCE_PUBLIC_IP>
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
DJANGO_TIME_ZONE=Asia/Kolkata
DATABASE_URL=postgresql://busta_user:REPLACE_WITH_STRONG_PASSWORD@127.0.0.1:5432/busta_im
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true
DJANGO_SECURE_HSTS_PRELOAD=true
```

Secure env file:

```bash
chmod 600 /opt/busta_im/backend/.env
```

## Step 12: Run Django migrations and collect static

```bash
cd /opt/busta_im/backend
/opt/busta_im/.venv/bin/python manage.py migrate
/opt/busta_im/.venv/bin/python manage.py collectstatic --noinput
/opt/busta_im/.venv/bin/python manage.py check --deploy
/opt/busta_im/.venv/bin/python manage.py createsuperuser
```

## Step 13: Configure systemd service for Gunicorn

```bash
sudo cp /opt/busta_im/deploy/systemd/busta-im.service /etc/systemd/system/busta-im.service
sudo nano /etc/systemd/system/busta-im.service
```

In service file, ensure:

1. `User=deploy`
2. `Group=deploy`
3. `WorkingDirectory=/opt/busta_im/backend`
4. `EnvironmentFile=/opt/busta_im/backend/.env`
5. `ExecStart=/opt/busta_im/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 120 --access-logfile - --error-logfile -`

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable busta-im
sudo systemctl start busta-im
sudo systemctl status busta-im
```

## Step 14: Configure Nginx reverse proxy

```bash
sudo cp /opt/busta_im/deploy/nginx/busta-im.conf /etc/nginx/sites-available/busta-im
sudo nano /etc/nginx/sites-available/busta-im
```

Change:

1. `server_name yourdomain.com www.yourdomain.com;`
2. Keep static alias as `/opt/busta_im/backend/staticfiles/`
3. Keep `proxy_pass http://127.0.0.1:8001;`

Enable site:

```bash
sudo ln -s /etc/nginx/sites-available/busta-im /etc/nginx/sites-enabled/busta-im
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## Step 15: Configure DNS (OCI DNS or registrar DNS)

Option A: OCI DNS

1. OCI Console -> `Networking` -> `DNS Management` -> `Zones`
2. Create public zone for your domain
3. Add A records:
   1. `@` -> `<INSTANCE_PUBLIC_IP>`
   2. `www` -> `<INSTANCE_PUBLIC_IP>`
4. Update your registrar with OCI NS records

Option B: External DNS (Cloudflare/registrar)

1. Add A records `@` and `www` pointing to `<INSTANCE_PUBLIC_IP>`

## Step 16: Enable HTTPS

```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com --redirect -m you@example.com --agree-tos --no-eff-email
sudo certbot renew --dry-run
```

## Step 17: Validate from multiple machines

1. Open `https://yourdomain.com/login/`
2. Log in with multiple users from different devices
3. Verify major workflows
4. Tail logs:

```bash
sudo journalctl -u busta-im -f
sudo tail -f /var/log/nginx/error.log
```

## Step 18: Ongoing update flow on OCI server

```bash
ssh -i /path/to/oci_private_key deploy@<INSTANCE_PUBLIC_IP>
cd /opt/busta_im
git pull
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart busta-im
sudo systemctl reload nginx
```

## 11. Oracle References (Official Docs)

1. Launch instance: https://docs.oracle.com/iaas/Content/Compute/Tasks/launchinginstance.htm
2. Connect to Linux instance: https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/connect-to-linux-instance.htm
3. VCN wizard: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/quickstartnetworking.htm
4. Public IP assignment: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/assign-public-ip-instance-launch.htm
5. Security list rules: https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/update-securitylist.htm
6. Always Free resources: https://docs.oracle.com/iaas/Content/FreeTier/resourceref.htm
7. DNS public zone: https://docs.oracle.com/en-us/iaas/Content/DNS/Concepts/gettingstarted_topic-Creating_a_Zone.htm
8. DNS delegation: https://docs.oracle.com/en-us/iaas/Content/DNS/Concepts/gettingstarted_topic-Delegating_Your_Zone.htm
