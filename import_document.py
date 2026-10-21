"""Import a UTF-8 .txt/.md file without a multipart upload dependency."""
import argparse
import getpass
import json
from pathlib import Path

from client import Client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--collection-id", required=True, type=int)
    parser.add_argument("--username", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    if args.file.suffix.lower() not in {".md", ".txt"}:
        parser.error("Use a UTF-8 .txt or .md file")
    if args.file.stat().st_size > 400_000:
        parser.error("File exceeds the importer size limit (400,000 bytes)")
    text = args.file.read_text(encoding="utf-8")
    if len(text) > 100_000:
        parser.error("Document exceeds 100,000 characters")
    api = Client(args.base_url)
    api.login(args.username, getpass.getpass("Password: "))
    try:
        result = api.call("POST", f"/api/v1/collections/{args.collection_id}/documents", {"title": args.file.stem, "text": text})
        print(json.dumps(result, indent=2))
        print("Poll the returned job ID or search after the worker finishes.")
    finally:
        api.call("POST", "/api/v1/auth/logout")


if __name__ == "__main__":
    main()
