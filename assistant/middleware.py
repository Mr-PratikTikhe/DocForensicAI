from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


class SitePasswordMiddleware:
    """Require a shared beta password before serving app or API content."""

    SESSION_KEY = "site_password_verified"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.SITE_ACCESS_PASSWORD:
            return self.get_response(request)

        login_path = reverse("assistant:site_login")
        logout_path = reverse("assistant:site_logout")
        static_prefix = "/" + settings.STATIC_URL.lstrip("/")
        if request.path in {login_path, logout_path} or request.path.startswith(static_prefix):
            return self.get_response(request)
        if request.session.get(self.SESSION_KEY):
            return self.get_response(request)

        if request.path.startswith("/api/"):
            return JsonResponse({"error": "Enter the beta password to continue."}, status=401)
        return redirect(f"{login_path}?next={request.get_full_path()}")
