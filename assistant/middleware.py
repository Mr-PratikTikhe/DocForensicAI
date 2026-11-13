import hmac

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import salted_hmac


def current_password_fingerprint():
    return salted_hmac("assistant.site_access", settings.SITE_ACCESS_PASSWORD).hexdigest()


class SitePasswordMiddleware:
    """Require a shared beta password before serving app or API content."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.SITE_ACCESS_PASSWORD:
            return self.get_response(request)

        login_path = reverse("assistant:site_login")
        logout_path = reverse("assistant:site_logout")
        static_prefix = "/" + settings.STATIC_URL.lstrip("/")
        if (
            request.path in {login_path, logout_path}
            or request.path == "/demo/"
            or request.path.startswith("/api/demo/")
            or request.path.startswith(static_prefix)
        ):
            return self.get_response(request)
        session_fingerprint = request.session.get("site_password_fingerprint", "")
        expected_fingerprint = current_password_fingerprint()
        if session_fingerprint and hmac.compare_digest(session_fingerprint, expected_fingerprint):
            return self.get_response(request)
        request.session.pop("site_password_fingerprint", None)

        if request.path.startswith("/api/"):
            return JsonResponse({"error": "Enter the beta password to continue."}, status=401)
        return redirect(f"{login_path}?next={request.get_full_path()}")
