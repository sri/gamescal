# Contributing

Use Python 3.14.6 and install the locked environment with `uv sync`.

Before submitting a change, run:

```console
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
```
