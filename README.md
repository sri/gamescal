# Gamescal

Gamescal is a Django 6.1 project running on Python 3.14.6 and SQLite. It was
scaffolded from the open-source [Lithium](https://github.com/wsvincent/lithium)
starter.

## Included

- iCalendar (ICS) feed import with an event preview and confirmation step
- Recurring event expansion, calendar refresh, and active/inactive calendars
- Automatic game, practice, tournament, and other event classification
- Per-calendar keyword rules with immediate event reclassification
- Games, practices, and all-events views with a weekend-only filter
- Cached Geoapify driving-time, distance, and between-game buffer estimates
- Development-only Geoapify request/response logs with redacted API keys and usage statistics
- A unified table of upcoming events
- SQLite as the only configured database
- Email-based authentication with django-allauth
- A custom user model
- Bootstrap 5 and crispy forms
- WhiteNoise static-file serving
- Django Debug Toolbar
- uv dependency management

Calendar imports include events from the previous 30 days through the next year.
All event times are interpreted and displayed in Arizona time (`America/Phoenix`),
which remains on Mountain Standard Time year-round. XML calendar feeds are not yet
supported because they require a provider-specific schema/parser.

## Local setup

```console
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

The application will be available at <http://127.0.0.1:8000/>. Add your
Geoapify project key to `.env` as `GEOAPIFY_API_KEY=...` to enable travel
estimates. The application calculates at most five new route pairs per page
load and caches successful estimates for 30 days. In development, the
**Populate Demo** button creates an idempotent seven-game travel scenario using
locations already present in the database; the endpoint is unavailable when
`DEBUG` is false. The development-only **API Logs** page records Geoapify
request parameters, response payloads, status codes, response sizes, and timing;
API keys are redacted before storage.

## Tests and checks

```console
uv run python manage.py check
uv run python manage.py test
```

## Docker

```console
docker compose up --build
```

The SQLite database is stored in `db.sqlite3` at the project root.

## Production configuration

Configure production through environment variables rather than committed files:

```console
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=replace-with-a-random-secret
DJANGO_ALLOWED_HOSTS=g.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://g.example.com
GEOAPIFY_API_KEY=optional-provider-key
```

The included Gunicorn dependency can serve the application behind an HTTPS reverse
proxy such as Caddy.
