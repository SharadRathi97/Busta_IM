# Busta IM Oracle Cloud Deployment Guide (Beginner Step-by-Step)

This guide assumes you are completely new to Oracle Cloud and Linux deployment.

Follow the steps in order. Do not skip steps.

## 0. What You Are Building

You will create:

1. An Oracle Cloud Ubuntu server (Compute instance)
2. A PostgreSQL database on that server
3. A Django app service (Gunicorn + systemd)
4. An Nginx web server in front of Django
5. HTTPS using a free SSL certificate (Let's Encrypt)

At the end, your app will open on:

- `https://yourdomain.com/login/`

## 1. Before You Start

You need:

1. Oracle Cloud account
2. A domain name (recommended)
3. Your app code already pushed to GitHub/GitLab
4. A local terminal (Mac/Linux Terminal or Windows WSL)

## 2. Generate SSH Key on Your Laptop

If you already have an SSH key, you can skip this section.

Run locally:

```bash
ssh-keygen -t ed25519 -C "busta-oci" -f ~/.ssh/busta_oci_key
```

Press Enter for defaults. If asked for passphrase, you can add one.

Show public key:

```bash
cat ~/.ssh/busta_oci_key.pub
```

Copy the full output. You will paste it into OCI while creating the instance.

## 3. Create Compartment in OCI

In Oracle Cloud Console:

1. Open `Identity & Security` -> `Compartments`
2. Click `Create Compartment`
3. Fill options:
4. `Name`: `busta-prod`
5. `Description`: `Busta IM production`
6. `Parent Compartment`: root tenancy
7. Click `Create Compartment`

## 4. Create VCN (Network)

In OCI Console:

1. Go to `Networking` -> `Virtual cloud networks`
2. Click `Start VCN Wizard`
3. Select `VCN with Internet Connectivity`
4. Click `Start VCN Wizard`
5. Set options:
6. `Compartment`: `busta-prod`
7. `VCN Name`: `busta-vcn`
8. `VCN CIDR Block`: keep default or use `10.0.0.0/16`
9. `Public Subnet CIDR Block`: keep default
10. `Private Subnet CIDR Block`: keep default
11. Click `Next`
12. Click `Create`

Wait until VCN status is `Available`.

## 5. Open Required Ports (Security Rules)

In OCI Console:

1. Open `Networking` -> `Virtual cloud networks`
2. Click your VCN `busta-vcn`
3. Open `Security Lists`
4. Open the security list attached to public subnet
5. Click `Add Ingress Rules`

Add these rules one by one:

1. Rule for SSH:
2. `Source Type`: `CIDR`
3. `Source CIDR`: your current public IP with `/32` (example `49.36.10.20/32`)
4. `IP Protocol`: `TCP`
5. `Destination Port Range`: `22`
6. Save

1. Rule for HTTP:
2. `Source CIDR`: `0.0.0.0/0`
3. `IP Protocol`: `TCP`
4. `Destination Port Range`: `80`
5. Save

1. Rule for HTTPS:
2. `Source CIDR`: `0.0.0.0/0`
3. `IP Protocol`: `TCP`
4. `Destination Port Range`: `443`
5. Save

Important:

1. Do not open port `5432` to public internet

## 6. Create Compute Instance

In OCI Console:

1. Go to `Compute` -> `Instances`
2. Click `Create instance`
3. Set options carefully:

1. `Name`: `busta-app-01`
2. `Compartment`: `busta-prod`
3. `Placement`: keep default availability domain
4. `Image`:
5. Choose `Canonical Ubuntu 24.04` for x86 shapes
6. If using Ampere ARM shape, choose `Canonical Ubuntu 24.04 Minimal aarch64`
7. `Shape`:
8. For beginner stable setup, choose at least 2 OCPU and 2 GB RAM equivalent
9. If free tier, use the Always Free shape available in your tenancy
10. `Networking`:
11. `Virtual cloud network`: `busta-vcn`
12. `Subnet`: public subnet from wizard
13. `Assign a public IPv4 address`: enabled
14. `Add SSH keys`:
15. Select `Paste public keys`
16. Paste content of `~/.ssh/busta_oci_key.pub`
17. Click `Create`

Wait until instance state shows `Running`.

## 7. (Optional but Recommended) Reserve Static Public IP

This avoids IP change after stop/start.

1. Go to `Networking` -> `Public IPs`
2. Click `Reserve Public IP`
3. `Compartment`: `busta-prod`
4. `Name`: `busta-app-ip`
5. Click `Reserve`
6. Attach it to the instance VNIC

## 8. Connect to Server via SSH

Get public IP from instance details page.

Run locally:

```bash
chmod 400 ~/.ssh/busta_oci_key
ssh -i ~/.ssh/busta_oci_key ubuntu@<INSTANCE_PUBLIC_IP>
```

If first connection asks `Are you sure you want to continue connecting`, type `yes`.

## 9. Install Base Packages

Run on server:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git nginx postgresql postgresql-contrib libpq-dev certbot python3-certbot-nginx ufw
```

## 10. Configure Firewall on Server (UFW)

Run:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status verbose
```

## 11. Create Deploy User and App Folder

Run:

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG sudo deploy
sudo mkdir -p /opt/busta_im
sudo chown -R deploy:deploy /opt/busta_im
sudo -iu deploy
```

From now on, stay as `deploy` user unless command needs `sudo`.

## 12. Clone Repository and Install Python Dependencies

Run:

```bash
cd /opt
git clone <YOUR_REPO_URL> busta_im
cd /opt/busta_im
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## 13. Setup PostgreSQL Database

Run:

```bash
sudo -u postgres psql
```

Inside `psql`, run:

```sql
CREATE DATABASE busta_im;
CREATE USER busta_user WITH ENCRYPTED PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE busta_im TO busta_user;
\q
```

Grant schema access:

```bash
sudo -u postgres psql -d busta_im -c "GRANT ALL ON SCHEMA public TO busta_user;"
```

## 14. Configure Django Environment File

Run:

```bash
cp /opt/busta_im/backend/.env.example /opt/busta_im/backend/.env
nano /opt/busta_im/backend/.env
```

Paste and edit values:

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

Save in nano:

1. `Ctrl+O` then Enter
2. `Ctrl+X`

Protect file permissions:

```bash
chmod 600 /opt/busta_im/backend/.env
```

## 15. Run Django Migrations and Collect Static

Run:

```bash
cd /opt/busta_im/backend
/opt/busta_im/.venv/bin/python manage.py migrate
/opt/busta_im/.venv/bin/python manage.py collectstatic --noinput
/opt/busta_im/.venv/bin/python manage.py check --deploy
/opt/busta_im/.venv/bin/python manage.py createsuperuser
```

Optional seed command:

```bash
/opt/busta_im/.venv/bin/python manage.py bootstrap_mvp
```

## 16. Configure Gunicorn Systemd Service

Run:

```bash
sudo cp /opt/busta_im/deploy/systemd/busta-im.service /etc/systemd/system/busta-im.service
sudo nano /etc/systemd/system/busta-im.service
```

Ensure these values are correct:

1. `WorkingDirectory=/opt/busta_im/backend`
2. `EnvironmentFile=/opt/busta_im/backend/.env`
3. `ExecStart=/opt/busta_im/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 120 --access-logfile - --error-logfile -`
4. `User=` and `Group=` are valid on your server (`www-data` or `deploy`)

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable busta-im
sudo systemctl start busta-im
sudo systemctl status busta-im
```

If status is not `active (running)`, check logs:

```bash
sudo journalctl -u busta-im -n 100 --no-pager
```

## 17. Configure Nginx

Run:

```bash
sudo cp /opt/busta_im/deploy/nginx/busta-im.conf /etc/nginx/sites-available/busta-im
sudo nano /etc/nginx/sites-available/busta-im
```

Edit:

1. `server_name yourdomain.com www.yourdomain.com;`
2. Keep static alias as `/opt/busta_im/backend/staticfiles/`
3. Keep proxy target as `127.0.0.1:8001`

Enable config:

```bash
sudo ln -s /etc/nginx/sites-available/busta-im /etc/nginx/sites-enabled/busta-im
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## 18. Configure DNS

Choose one method.

Method A (OCI DNS):

1. OCI Console -> `Networking` -> `DNS Management` -> `Zones`
2. Create public zone for your domain
3. Add `A` record `@` to `<INSTANCE_PUBLIC_IP>`
4. Add `A` record `www` to `<INSTANCE_PUBLIC_IP>`
5. Update nameservers at domain registrar to OCI nameservers

Method B (External DNS provider):

1. Open your registrar DNS panel
2. Add `A` record `@` -> `<INSTANCE_PUBLIC_IP>`
3. Add `A` record `www` -> `<INSTANCE_PUBLIC_IP>`

Wait for DNS propagation (few minutes to 24 hours).

## 19. Enable HTTPS Certificate

Run:

```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com --redirect -m you@example.com --agree-tos --no-eff-email
sudo certbot renew --dry-run
```

## 20. Final Validation Checklist

1. Open `https://yourdomain.com/login/`
2. Log in with admin user
3. Verify dashboard loads
4. Create test record and confirm save works
5. Verify static files load correctly

Live logs:

```bash
sudo journalctl -u busta-im -f
sudo tail -f /var/log/nginx/error.log
```

## 21. Future Code Deploy Steps

Every time you push new code:

```bash
ssh -i ~/.ssh/busta_oci_key deploy@<INSTANCE_PUBLIC_IP>
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

## 22. Backup and Restore Database

Backup:

```bash
pg_dump -h 127.0.0.1 -U busta_user -d busta_im -Fc > busta_im_$(date +%F).dump
```

Restore:

```bash
createdb -h 127.0.0.1 -U busta_user busta_im_restore
pg_restore -h 127.0.0.1 -U busta_user -d busta_im_restore --clean --if-exists busta_im_YYYY-MM-DD.dump
```

## 23. Common Errors and Fixes

1. Error: `OperationalError ... no column named ...`
2. Fix: run `python manage.py migrate`

1. Error: `502 Bad Gateway`
2. Fix: `sudo systemctl status busta-im` and inspect Gunicorn logs

1. Error: static files missing
2. Fix: run `python manage.py collectstatic --noinput` and verify Nginx alias path

1. Error: CSRF failed
2. Fix: verify `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`

## 24. OCI Official Docs

1. Launch instance: https://docs.oracle.com/iaas/Content/Compute/Tasks/launchinginstance.htm
2. Connect to Linux instance: https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/connect-to-linux-instance.htm
3. VCN quickstart: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/quickstartnetworking.htm
4. Assign public IP: https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/assign-public-ip-instance-launch.htm
5. Security list rules: https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/update-securitylist.htm
6. DNS zones: https://docs.oracle.com/en-us/iaas/Content/DNS/Concepts/gettingstarted_topic-Creating_a_Zone.htm
