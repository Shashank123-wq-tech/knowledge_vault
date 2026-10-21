"""Create a temporary demo account and searchable sample collection."""
import argparse
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from client import Client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    api = Client(args.base_url)
    username, password = "demo_" + secrets.token_hex(4), secrets.token_urlsafe(24)
    api.call("POST", "/api/v1/auth/register", {"username": username, "password": password})
    api.login(username, password)
    cid = api.call("POST", "/api/v1/collections", {"name": "Backend study notes"})["id"]
    try:
        jobs = []
        for file in sorted(Path(__file__).with_name("samples").glob("*.md")):
            result = api.call("POST", f"/api/v1/collections/{cid}/documents", {"title": file.stem, "text": file.read_text(encoding="utf-8")})
            jobs.append(result["job"]["id"])
        deadline = time.monotonic() + 30
        while True:
            states = [api.call("GET", f"/api/v1/jobs/{job_id}")["status"] for job_id in jobs]
            if all(state == "succeeded" for state in states):
                break
            if "failed" in states:
                raise RuntimeError("Indexing failed; inspect the worker terminal")
            if time.monotonic() > deadline:
                raise RuntimeError("Indexing timed out. Start python worker.py in another terminal and rerun the demo.")
            time.sleep(0.25)
        query = urlencode({"q": "FastAPI validation", "mode": "all"})
        hits = api.call("GET", "/api/v1/search?" + query)
        assert hits["count"] > 0, "Expected a FastAPI search result"
        print(json.dumps(hits, indent=2))
        print("User statistics:", api.call("GET", "/api/v1/stats"))
        print("Demo successful. Removing the demo collection and revoking its session.")
    finally:
        api.call("DELETE", f"/api/v1/collections/{cid}")
        api.call("POST", "/api/v1/auth/logout")
    print("An empty, randomly named demo account remains in the local database.")


if __name__ == "__main__":
    main()
