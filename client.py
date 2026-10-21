"""Tiny standard-library HTTP client shared by the demo and importer."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.token = ""

    def call(self, method, path, body=None):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(self.base_url + path, method=method, headers=headers,
                          data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=15) as response:
                data = response.read()
                return json.loads(data) if data else None
        except HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from None

    def login(self, username, password):
        result = self.call("POST", "/api/v1/auth/login", {"username": username, "password": password})
        self.token = result["access_token"]
