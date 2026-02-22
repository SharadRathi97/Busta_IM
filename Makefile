.PHONY: init migrate bootstrap run test

init:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r backend/requirements.txt

migrate:
	. .venv/bin/activate && cd backend && python manage.py makemigrations && python manage.py migrate

bootstrap:
	. .venv/bin/activate && cd backend && python manage.py bootstrap_mvp

run:
	. .venv/bin/activate && cd backend && python manage.py runserver 127.0.0.1:8000

test:
	. .venv/bin/activate && cd backend && python manage.py test
