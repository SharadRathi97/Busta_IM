from __future__ import annotations

from .audit_context import reset_audit_actor, set_audit_actor


class AuditActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_audit_actor(getattr(request, "user", None))
        try:
            response = self.get_response(request)
        finally:
            reset_audit_actor(token)
        return response
