"""Connector queue delivery."""
class Plugin:
    def __init__(self, config, ctx):
        self.ctx = ctx

    def notify(self, event):
        self.ctx.post_json("console", event, 10)
        return {"delivered": True, "detail": "queued"}
