#!/usr/bin/env python3
"""Replace the example read tool with a bounded internal API adapter."""
import argparse
import os
from aa_mcp import Application, Auth, Directory, Kubernetes, make_server

TOOLS = [{"name": "example_read", "description": "Read an example internal record",
          "inputSchema": {"type": "object", "properties": {
              "record_id": {"type": "string", "minLength": 1, "maxLength": 128}},
              "required": ["record_id"], "additionalProperties": False},
          "annotations": {"readOnlyHint": True}}]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8080")))
    args = parser.parse_args(argv)
    auth = Auth(Kubernetes(os.environ["APISERVER_URL"]), Directory(),
                os.environ["PROJECT_NAME"], os.environ["USER_NS_PREFIX"],
                os.environ["LABEL_PREFIX"], os.environ["MCP_NAME"])
    app = Application(os.environ["MCP_NAME"], auth, TOOLS,
                      lambda name, args, actor: {"id": args["record_id"], "text": "Example API adapter"})
    with make_server(app, ("0.0.0.0", args.port), os.environ.get("MCP_PATH", "/mcp")) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
