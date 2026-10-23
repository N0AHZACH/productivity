.PHONY: run test lint fmt typecheck docker-build demo portfolio-pdf

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	python -m pytest -q

lint:
	ruff check app tests scripts

fmt:
	ruff format app tests scripts

typecheck:
	mypy app

docker-build:
	docker build -t docpilot:latest .

demo:
	python scripts/demo.py

portfolio-pdf:
	python scripts/build_portfolio_pdf.py
