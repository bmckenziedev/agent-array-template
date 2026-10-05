"""Audit-only notifier; payloads are never printed directly."""
class Plugin:
    def __init__(self, config, ctx):
        self.ctx = ctx

    def notify(self, event):
        self.ctx.audit("supervisor.notification", detail={"type": event.get("type"), "request_id": event.get("request_id")})
        return {"delivered": True, "detail": "recorded"}
