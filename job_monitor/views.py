"""Server-rendered project-level pages."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Render the source-neutral application landing page."""
    return render(request, "home.html")
