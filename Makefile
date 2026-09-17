PYTHON ?= .venv/bin/python
PIP    ?= .venv/bin/pip
HOST   ?= 127.0.0.1
PORT   ?= 8080
PIDDIR := .run
PIDFILE := $(PIDDIR)/app.pid
LOGFILE := $(PIDDIR)/app.log
URL    := http://$(HOST):$(PORT)

.PHONY: help setup install env run dev start stop restart status logs open check test ci release release-minor release-major build-win clean perf-quick scrape-bench

help:
	@echo "Realitify"
	@echo
	@echo "  make setup          venv + závislosti + .env"
	@echo "  make run            spustit v popředí"
	@echo "  make dev            web (reload) + scrape_worker; SCRAPE_ROLE=all make dev = jeden proces"
	@echo "  make start          spustit na pozadí"
	@echo "  make stop           zastavit (port $(PORT))"
	@echo "  make restart        stop + start"
	@echo "  make status         běží / neběží"
	@echo "  make logs           sledovat log ze start"
	@echo "  make open           otevřít $(URL)"
	@echo "  make check          zkompilovat Python"
	@echo "  make test           unittest (včetně test_regex_safety)"
	@echo "  make ci             check + test + perf-quick (pre-merge)"
	@echo "  make release        Windows release (patch)"
	@echo "  make release-minor  Windows release (minor)"
	@echo "  make release-major  Windows release (major)"
	@echo "  make build-win      jen lokální Windows zip"
	@echo "  make clean          smazat cache a .run"
	@echo "  make scrape-bench   5min scrape throughput bench → scripts/perf/scrape_baseline.json"

setup: $(PYTHON) env
	$(PIP) install -r requirements.txt

install: setup

$(PYTHON):
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip

env:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Vytvořeno .env z .env.example"; else echo ".env už existuje"; fi

run: $(PYTHON)
	$(PYTHON) -m app

dev: $(PYTHON)
	$(PYTHON) -m app.run_dev

start: $(PYTHON)
	@mkdir -p $(PIDDIR)
	@if [ -n "$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null)" ]; then \
		echo "Už běží na $(URL)"; \
		exit 0; \
	fi
	@nohup $(PYTHON) -m app > $(LOGFILE) 2>&1 & echo $$! > $(PIDFILE)
	@sleep 0.4
	@echo "Spuštěno na $(URL) (pid $$(cat $(PIDFILE)))"

stop:
	@pids="$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null)"; \
	if [ -z "$$pids" ]; then \
		echo "Nic neposlouchá na portu $(PORT)"; \
	else \
		echo "Zastavuji $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 0.6; \
		still="$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null)"; \
		if [ -n "$$still" ]; then kill -9 $$still 2>/dev/null || true; fi; \
		echo "Zastaveno"; \
	fi
	@if [ -f $(PIDDIR)/scrape_worker.pid ]; then \
		wp=$$(cat $(PIDDIR)/scrape_worker.pid); \
		echo "Zastavuji scrape_worker $$wp"; \
		kill $$wp 2>/dev/null || true; \
		sleep 0.3; \
		kill -9 $$wp 2>/dev/null || true; \
		rm -f $(PIDDIR)/scrape_worker.pid; \
	fi
	@rm -f $(PIDFILE)

restart: stop start

status:
	@pids="$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null)"; \
	if [ -n "$$pids" ]; then echo "Běží na $(URL) (pid $$pids)"; else echo "Neběží"; fi

logs:
	@mkdir -p $(PIDDIR)
	@touch $(LOGFILE)
	tail -f $(LOGFILE)

open:
	@open "$(URL)" 2>/dev/null || xdg-open "$(URL)" 2>/dev/null || echo "$(URL)"

check: $(PYTHON)
	$(PYTHON) -m py_compile app/main.py app/monitor.py app/store.py app/identity.py app/sreality.py app/bezrealitky.py app/idnes.py app/idnes_url.py app/catalog_sync.py app/filter_bridge.py app/billing.py app/account.py app/push.py app/html_listing.py app/places.py app/scrape_engine.py

test: $(PYTHON)
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -q

ci: check test perf-quick
	@echo "ci ok"

release: $(PYTHON)
	$(PYTHON) packaging/release.py patch

release-minor: $(PYTHON)
	$(PYTHON) packaging/release.py minor

release-major: $(PYTHON)
	$(PYTHON) packaging/release.py major

build-win: $(PYTHON)
	$(PYTHON) packaging/build_windows.py

perf-quick: $(PYTHON)
	$(PYTHON) -m scripts.perf.perf_quick

scrape-bench: $(PYTHON)
	SCRAPE_METRICS_DETAIL=1 $(PYTHON) -m scripts.perf.scrape_bench --minutes 5 --out scripts/perf/scrape_baseline.json

clean:
	rm -rf $(PIDDIR) __pycache__ app/__pycache__ .pytest_cache
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
