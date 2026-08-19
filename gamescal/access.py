import hashlib
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.crypto import salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

ACCESS_SESSION_KEY = "gamescal_shared_access"
MAX_FAILED_ATTEMPTS_PER_MINUTE = 10


def access_enabled():
    return bool(settings.GAMESCAL_ACCESS_PASSWORD_HASH)


def _access_fingerprint():
    return salted_hmac(
        "gamescal.shared-access",
        settings.GAMESCAL_ACCESS_PASSWORD_HASH,
    ).hexdigest()


def has_shared_access(request):
    return access_enabled() and request.session.get(
        ACCESS_SESSION_KEY
    ) == _access_fingerprint()


def _safe_next_url(request):
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("home")


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded_for.split(",", 1)[0].strip() or request.META.get(
        "REMOTE_ADDR", "unknown"
    )


def _failed_attempt_key(request):
    digest = hashlib.sha256(_client_ip(request).encode()).hexdigest()
    return f"gamescal-access-failures:{digest}"


def _record_failed_attempt(request):
    key = _failed_attempt_key(request)
    if cache.add(key, 1, timeout=60):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60)
        return 1


@require_http_methods(["GET", "POST"])
def shared_access_login(request):
    next_url = _safe_next_url(request)
    if has_shared_access(request):
        return HttpResponseRedirect(next_url)

    error = ""
    status = 200
    if request.method == "POST":
        key = _failed_attempt_key(request)
        attempts = cache.get(key, 0)
        if attempts >= MAX_FAILED_ATTEMPTS_PER_MINUTE:
            error = "Too many attempts. Try again in a minute."
            status = 429
        elif check_password(
            request.POST.get("password", ""),
            settings.GAMESCAL_ACCESS_PASSWORD_HASH,
        ):
            cache.delete(key)
            request.session.cycle_key()
            request.session[ACCESS_SESSION_KEY] = _access_fingerprint()
            request.session.set_expiry(settings.GAMESCAL_ACCESS_SESSION_AGE)
            return HttpResponseRedirect(next_url)
        else:
            attempts = _record_failed_attempt(request)
            if attempts >= MAX_FAILED_ATTEMPTS_PER_MINUTE:
                error = "Too many attempts. Try again in a minute."
                status = 429
            else:
                error = "That password is not correct."

    return render(
        request,
        "shared_access/login.html",
        {"error": error, "next": next_url},
        status=status,
    )


@require_POST
def shared_access_lock(request):
    request.session.flush()
    return HttpResponseRedirect(reverse("shared_access_login"))


class SharedAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not access_enabled():
            return self.get_response(request)

        login_path = reverse("shared_access_login")
        public_path = request.path_info == login_path or request.path_info.startswith(
            settings.STATIC_URL
        )
        request.gamescal_access_granted = has_shared_access(request)

        if not public_path and not request.gamescal_access_granted:
            query = urlencode({"next": request.get_full_path()})
            response = HttpResponseRedirect(f"{login_path}?{query}")
        else:
            if request.gamescal_access_granted:
                request.session.set_expiry(settings.GAMESCAL_ACCESS_SESSION_AGE)
            response = self.get_response(request)

        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, noarchive"
        return response
