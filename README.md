# Busta IM (Django MVP)

Inventory and production management application for bag manufacturing.

## Stack

- Python 3.12
- Django 5.1
- SQLite (development)
- PostgreSQL (production)
- Bootstrap 5 (responsive UI)
- openpyxl (Excel export)
- reportlab (PDF export)
- Gunicorn (WSGI server)
- Nginx (reverse proxy + static files)

## MVP Features Implemented

- Login/logout with Django auth.
- Role-based access with custom user roles:
  - `admin`
  - `inventory_manager`
  - `production_manager`
  - `viewer`
- Admin user management:
  - create users
  - deactivate users
  - delete users
- Dashboard:
  - raw material count
  - finished product count
  - vendor/buyer count
  - production in progress
  - low stock alerts
  - recent inventory transactions
- Vendor/Buyer management with GST and address validation.
  - edit and delete actions from list view
- Raw material management:
  - mandatory supplier selection from existing vendors
  - optional multiple supplier mapping per material
  - material type tagging (e.g., Fabric, Mesh, Thread)
  - edit and delete actions from list view
  - units (`kg`, `m`, `pieces`, `litre`)
  - opening stock and reorder level
  - manual stock adjustment with ledger audit
- Finished products and BOM mapping.
- Production order workflow:
  - create order for finished product + quantity
  - automatic raw material deduction using BOM
  - insufficient-stock blocking
  - consumption + ledger entries
- Purchase orders:
  - multiple line items
  - vendor-first creation flow (select vendor, then choose eligible materials)
  - partial receive workflow per line item
  - status lifecycle with rules (`open` -> `partially_received` -> `received`, plus `cancelled`/`reopen`)
  - stock increment + inventory ledger entries on each receipt
  - list filters/search by status, vendor, date range, PO/material text
  - Excel and PDF export per PO
- Responsive pages for desktop/mobile.

## Project Layout

- `backend/config/` - Django project config
- `backend/accounts/` - auth + roles + user management
- `backend/dashboard/` - dashboard page
- `backend/partners/` - vendors/buyers
- `backend/inventory/` - raw materials + inventory ledger
- `backend/production/` - finished products, BOM, production orders
- `backend/purchasing/` - purchase orders + exports
- `backend/templates/` - HTML templates
- `backend/static/` - CSS

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
cd backend
python manage.py makemigrations
python manage.py migrate
python manage.py bootstrap_mvp
python manage.py seed_demo_data --reset  # optional: refresh demo vendors/materials/PO statuses
python manage.py runserver 127.0.0.1:8000
```

Open: `http://127.0.0.1:8000/login/`

Default admin credentials:
- username: `admin`
- password: `admin123`

## Tests

```bash
source .venv/bin/activate
cd backend
python manage.py test
```

## Settings Profiles

- Settings entrypoint: `backend/config/settings.py`
- Controlled by `DJANGO_ENV` in `backend/.env`
- Values:
  - `development` -> `config.settings_dev`
  - `production` -> `config.settings_prod`

Environment variables are loaded from `backend/.env` (or `DJANGO_DOTENV_PATH`).

Minimum production environment values:

```bash
DJANGO_ENV=production
DJANGO_SECRET_KEY=<strong-random-secret>
DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com
DATABASE_URL=postgresql://busta_user:<db-password>@127.0.0.1:5432/busta_im
```

## Production Deployment (Ubuntu VM)

1. Clone code to `/opt/busta_im` and create venv:
```bash
cd /opt
git clone <your-repo-url> busta_im
cd busta_im
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

2. Prepare environment:
```bash
cp backend/.env.example backend/.env
# edit backend/.env with production values
```

3. Setup PostgreSQL (example):
```bash
sudo -u postgres psql
CREATE DATABASE busta_im;
CREATE USER busta_user WITH PASSWORD '<strong-db-password>';
GRANT ALL PRIVILEGES ON DATABASE busta_im TO busta_user;
\q
```

4. Run migrations and collect static:
```bash
cd /opt/busta_im/backend
/opt/busta_im/.venv/bin/python manage.py migrate
/opt/busta_im/.venv/bin/python manage.py collectstatic --noinput
```

5. Configure systemd:
```bash
sudo cp /opt/busta_im/deploy/systemd/busta-im.service /etc/systemd/system/busta-im.service
sudo systemctl daemon-reload
sudo systemctl enable busta-im
sudo systemctl start busta-im
sudo systemctl status busta-im
```

6. Configure Nginx:
```bash
sudo cp /opt/busta_im/deploy/nginx/busta-im.conf /etc/nginx/sites-available/busta-im
sudo ln -s /etc/nginx/sites-available/busta-im /etc/nginx/sites-enabled/busta-im
sudo nginx -t
sudo systemctl reload nginx
```

7. Enable HTTPS (Let's Encrypt):
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

Deployment templates:
- `deploy/systemd/busta-im.service`
- `deploy/nginx/busta-im.conf`
- `backend/.env.example`

## Notes

- Legacy non-framework implementation backup is kept at `backend_legacy_stdlib/`.
- For production, use PostgreSQL and keep `DEBUG` disabled.
