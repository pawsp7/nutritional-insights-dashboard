"""Serve the function handlers on http://localhost:7071/api without the Functions host.

`func start` is the normal way to run this app locally. This harness is a fallback for
machines without Azure Functions Core Tools: it loads local.settings.json the way the host
does, then routes /api/<name> to the same decorated handlers in function_app.py.

    .venv/Scripts/python dev_server.py [--port 7071]
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

HERE = Path(__file__).resolve().parent


def load_settings():
	path = HERE / "local.settings.json"
	if not path.exists():
		path = HERE / "local.settings.example.json"
	for key, value in json.loads(path.read_text())["Values"].items():
		os.environ.setdefault(key, value)


def build_routes():
	import function_app

	routes = {}
	for fn in function_app.app.get_functions():
		routes[fn.get_trigger().route] = fn.get_user_function()
	return routes


class Handler(BaseHTTPRequestHandler):
	routes = {}

	def _send(self, status, body, mimetype="application/json"):
		self.send_response(status)
		self.send_header("Content-Type", mimetype)
		self.send_header("Access-Control-Allow-Origin", "*")
		self.end_headers()
		self.wfile.write(body)

	def do_OPTIONS(self):
		self.send_response(204)
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
		self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
		self.end_headers()

	def do_GET(self):
		import azure.functions as func

		parts = urlsplit(self.path)
		route = parts.path.removeprefix("/api/").strip("/")
		handler = self.routes.get(route)
		if handler is None:
			return self._send(404, json.dumps({"error": f"no function at {parts.path}"}).encode())
		req = func.HttpRequest("GET", self.path, headers=dict(self.headers), params=dict(parse_qsl(parts.query)), body=b"")
		resp = handler(req)
		self._send(resp.status_code, resp.get_body(), resp.mimetype)


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--port", type=int, default=7071)
	args = parser.parse_args()
	load_settings()
	Handler.routes = build_routes()
	print(f"functions: {', '.join('/api/' + r for r in Handler.routes)}")
	print(f"listening on http://localhost:{args.port}/api")
	ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
	main()
