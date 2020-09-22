watchtest:
	pytest-watch

debugtest:
	pytest -s

test:
	pytest --cov=. --cov-report=term-missing

runserver:
	python manage.py runserver
