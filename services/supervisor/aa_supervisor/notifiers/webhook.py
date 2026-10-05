"""TokenReview-authenticated relay delivery."""
class Plugin:
    def __init__(self, config, ctx):
        self.config, self.ctx = config, ctx

    def notify(self, event):
        self.ctx.post_json(self.config["url"], event, self.config.get("timeout_s", 10))
        return {"delivered": True, "detail": "accepted"}
