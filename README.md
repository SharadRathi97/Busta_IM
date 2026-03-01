# Busta IM (Django MVP)

Inventory, production, and purchasing workflow system for bag manufacturing.

## Current Scope

- Role-based authentication (`admin`, `inventory_manager`, `production_manager`, `viewer`)
- Dashboard KPIs and low-stock alerts
  - Admin users see a low-stock modal immediately after login
- Vendor/Buyer management
- Inventory module with dropdown pages:
  - Raw Materials
  - Parts
  - Finished Products
  - MRO
- Products and Parts module:
  - Separate tables for Finished Products and Parts
  - Add Part modal with mandatory colour
  - BOM can be created for both finished products and parts
  - Finished product BOM can include both raw materials and parts
- Production orders:
  - Raw material release workflow
  - Status lifecycle and stock movements
- Purchase orders:
  - Terms modal while creating PO (`payment_pdc_days`, delivery/freight/packaging/inspection/packing)
  - Low-stock purchase planner with batch PO PDF generation per vendor
  - `PO Pending Approvals` section (inventory + admin approvals)
  - Admin-only delete for pending PO approvals
  - Final PO list only after both approvals
  - Receive/cancel/reopen flow for approved POs
  - Excel + PDF export with company/vendor header layout and signatures from `assets/images/`

## Tech Stack

- Python 3.12
- Django 5.1
- SQLite (default local)
- PostgreSQL (production)
- Bootstrap 5
- openpyxl (Excel export)
- reportlab (PDF export)
- Gunicorn + Nginx (deployment)

## Project Layout

- `backend/config/` - Django settings and URLs
- `backend/accounts/` - auth, roles, users, audit logs
- `backend/dashboard/` - dashboard and low-stock modal trigger
- `backend/partners/` - vendors/buyers
- `backend/inventory/` - raw materials, MRO, inventory pages/stock actions
- `backend/production/` - products, parts, BOM, production orders
- `backend/purchasing/` - PO workflows, approvals, exports
- `backend/templates/` - server-rendered UI
- `assets/images/` - PO signature images
- `frontend/` - standalone React/Vite sandbox (optional)

## Local Setup (Backend)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env

cd backend
python manage.py migrate
python manage.py bootstrap_mvp
python manage.py seed_demo_data --reset   # optional demo data
python manage.py runserver 127.0.0.1:8000
```

Open: `http://127.0.0.1:8000/login/`

Default admin credentials:
- username: `admin`
- password: `admin123`

## Local Setup (Shortcuts)

A few helper targets are available:

```bash
make init
make migrate
make bootstrap
make run
make test
```

## Migrations and Schema Sync

Always run migrations after pulling updates:

```bash
source .venv/bin/activate
cd backend
python manage.py migrate
```

If you hit errors like `table ... has no column named ...`, your DB schema is behind code; run `python manage.py migrate` and restart the server.

## Tests

```bash
source .venv/bin/activate
cd backend
python manage.py test
```

## Environment Profiles

Settings entrypoint: `backend/config/settings.py`

- `DJANGO_ENV=development` -> `config.settings_dev`
- `DJANGO_ENV=production` -> `config.settings_prod`

Environment variables are loaded from `backend/.env` (or `DJANGO_DOTENV_PATH`).

Minimum production variables:

```env
DJANGO_ENV=production
DJANGO_SECRET_KEY=<strong-random-secret>
DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com
DATABASE_URL=postgresql://busta_user:<db-password>@127.0.0.1:5432/busta_im
```

## Deployment

Use `DEPLOYMENT_README.md` for complete VM + PostgreSQL + Nginx + Gunicorn instructions.
