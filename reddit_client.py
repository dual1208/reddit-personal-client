#!/usr/bin/env python3
"""Small, single-user Reddit client. Live calls require a recorded API approval."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


APP = "reddit-personal-client"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
AUTH_URL = "https://www.reddit.com/api/v1/authorize"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_URL = "https://oauth.reddit.com"
SCOPES = "identity read privatemessages submit"


class ClientError(Exception):
    pass


def read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else default.copy()
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def require_approval() -> None:
    approval = read_json(CONFIG_DIR / "approval.json")
    if approval.get("status") != "approved" or not approval.get("reference"):
        raise ClientError(
            "Live Reddit API access is disabled. Wait for explicit Reddit approval, "
            "then save {\"status\": \"approved\", \"reference\": \"<approval ID or URL>\"} "
            f"in {CONFIG_DIR / 'approval.json'}."
        )


def config() -> dict:
    require_approval()
    value = read_json(CONFIG_DIR / "config.json")
    for key in ("client_id", "redirect_uri", "user_agent"):
        if not value.get(key):
            raise ClientError(f"Missing {key} in {CONFIG_DIR / 'config.json'}")
    return value


def http_json(method: str, url: str, *, headers: dict, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ClientError(f"Reddit HTTP {error.code} at {urllib.parse.urlsplit(url).path}") from error
    except urllib.error.URLError as error:
        raise ClientError(f"Reddit connection failed: {error.reason}") from error


def token_request(fields: dict, settings: dict) -> dict:
    credentials = f"{settings['client_id']}:{settings.get('client_secret', '')}".encode()
    headers = {
        "Authorization": "Basic " + base64.b64encode(credentials).decode("ascii"),
        "User-Agent": settings["user_agent"],
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return http_json("POST", TOKEN_URL, headers=headers, data=urllib.parse.urlencode(fields).encode())


def authorize_start() -> None:
    settings = config()
    state = secrets.token_urlsafe(32)
    save_json(CONFIG_DIR / "oauth_pending.json", {"state": state})
    query = urllib.parse.urlencode({
        "client_id": settings["client_id"],
        "response_type": "code",
        "state": state,
        "redirect_uri": settings["redirect_uri"],
        "duration": "permanent",
        "scope": SCOPES,
    })
    print(f"Open this URL, approve the listed scopes, then run auth-finish with the full redirect URL:\n{AUTH_URL}?{query}")


def authorize_finish(redirect_url: str) -> None:
    settings = config()
    pending = read_json(CONFIG_DIR / "oauth_pending.json")
    if not pending.get("state"):
        raise ClientError("Run auth-start first.")
    parsed = urllib.parse.urlsplit(redirect_url)
    expected = urllib.parse.urlsplit(settings["redirect_uri"])
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
        raise ClientError("Redirect URL does not match the configured redirect URI.")
    params = urllib.parse.parse_qs(parsed.query)
    if params.get("state", [None])[0] != pending["state"]:
        raise ClientError("OAuth state mismatch.")
    if "error" in params:
        raise ClientError(f"OAuth was declined: {params['error'][0]}")
    code = params.get("code", [None])[0]
    if not code:
        raise ClientError("No authorization code in redirect URL.")
    result = token_request({"grant_type": "authorization_code", "code": code,
                            "redirect_uri": settings["redirect_uri"]}, settings)
    if not result.get("refresh_token"):
        raise ClientError("No refresh token returned. Check app type and permanent duration.")
    settings["refresh_token"] = result["refresh_token"]
    save_json(CONFIG_DIR / "config.json", settings)
    (CONFIG_DIR / "oauth_pending.json").unlink(missing_ok=True)
    print("Refresh token saved locally with mode 600.")


class Reddit:
    def __init__(self):
        self.settings = config()
        if not self.settings.get("refresh_token"):
            raise ClientError("Run auth-start and auth-finish first.")
        result = token_request({"grant_type": "refresh_token",
                                "refresh_token": self.settings["refresh_token"]}, self.settings)
        self.token = result.get("access_token")
        if not self.token:
            raise ClientError("Reddit did not return an access token.")

    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        if not path.startswith("/") or path.startswith("//"):
            raise ClientError("Invalid API path.")
        headers = {"Authorization": "bearer " + self.token,
                   "User-Agent": self.settings["user_agent"]}
        if method == "GET":
            query = urllib.parse.urlencode({**(params or {}), "raw_json": "1"})
            return http_json(method, API_URL + path + "?" + query, headers=headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return http_json(method, API_URL + path, headers=headers,
                         data=urllib.parse.urlencode(params or {}).encode())


def children(listing: dict) -> list[dict]:
    return [item["data"] for item in listing.get("data", {}).get("children", [])
            if isinstance(item.get("data"), dict)]


def summary(item: dict) -> str:
    name = item.get("name", "?")
    author = item.get("author", "?")
    title = item.get("title") or item.get("body", "").replace("\n", " ")[:120]
    return f"{name}  u/{author}  {title}"


def probe(client: Reddit) -> None:
    me = client.request("GET", "/api/v1/me")
    posts = client.request("GET", "/r/IELTS/new", {"limit": 10})
    inbox = client.request("GET", "/message/inbox", {"limit": 25, "mark": "false"})
    print(f"Authenticated as u/{me['name']}; r/IELTS: {len(children(posts))} posts; "
          f"inbox: {len(children(inbox))} items. No items were marked read.")


def list_posts(client: Reddit, limit: int) -> None:
    for item in children(client.request("GET", "/r/IELTS/new", {"limit": limit})):
        print(summary(item))
        print(" ", "https://www.reddit.com" + item.get("permalink", ""))


def flatten_comments(items: list, depth: int = 0):
    for item in items:
        if item.get("kind") != "t1":
            continue  # Unexpanded morechildren markers are not silently treated as comments.
        data = item.get("data", {})
        yield depth, data
        replies = data.get("replies")
        if isinstance(replies, dict):
            yield from flatten_comments(replies.get("data", {}).get("children", []), depth + 1)


def thread(client: Reddit, post_id: str) -> None:
    if not post_id.isalnum():
        raise ClientError("Post ID must be alphanumeric.")
    result = client.request("GET", f"/comments/{post_id}", {"limit": 200})
    post = children(result[0])[0]
    print(summary(post))
    print(post.get("selftext", ""))
    for depth, comment in flatten_comments(result[1].get("data", {}).get("children", [])):
        print("  " * depth + summary(comment))


def draft(parent_id: str, text: str) -> None:
    if not (parent_id.startswith("t1_") or parent_id.startswith("t3_")) or not parent_id[3:].isalnum():
        raise ClientError("Parent must be a Reddit t1_ comment ID or t3_ post ID.")
    if not text.strip():
        raise ClientError("Draft cannot be blank.")
    draft_id = secrets.token_hex(8)
    state = read_json(DATA_DIR / "state.json")
    state.setdefault("drafts", {})[draft_id] = {"parent_id": parent_id, "text": text,
                                                   "status": "draft"}
    save_json(DATA_DIR / "state.json", state)
    print(f"Draft {draft_id} saved for {parent_id}. Review with `draft-show {draft_id}`.")


def show_draft(draft_id: str) -> dict:
    entry = read_json(DATA_DIR / "state.json").get("drafts", {}).get(draft_id)
    if not entry:
        raise ClientError("Draft not found.")
    print(f"Draft {draft_id} — {entry['status']} — parent {entry['parent_id']}\n\n{entry['text']}")
    return entry


def send(client: Reddit, draft_id: str) -> None:
    state = read_json(DATA_DIR / "state.json")
    entry = state.get("drafts", {}).get(draft_id)
    if not entry or entry["status"] != "draft":
        raise ClientError("Draft is missing, sent, or has an unresolved prior send attempt.")
    me = client.request("GET", "/api/v1/me")
    print(f"Sending account: u/{me['name']}\nTarget: {entry['parent_id']}\n\n{entry['text']}\n")
    answer = input(f"Type SEND {draft_id} to submit this one comment: ")
    if answer != f"SEND {draft_id}":
        print("Cancelled. Draft remains unsent.")
        return
    entry["status"] = "pending"
    entry["attempted_at"] = datetime.now(timezone.utc).isoformat()
    save_json(DATA_DIR / "state.json", state)
    result = client.request("POST", "/api/comment", {"api_type": "json",
                                                       "thing_id": entry["parent_id"],
                                                       "text": entry["text"]})
    errors = result.get("json", {}).get("errors", [])
    if errors:
        raise ClientError(f"Reddit reported a comment error: {errors}. Draft left pending; reconcile before retrying.")
    things = result.get("json", {}).get("data", {}).get("things", [])
    if not things:
        raise ClientError("No comment ID returned. Draft left pending; reconcile before retrying.")
    entry["status"] = "sent"
    entry["comment_id"] = things[0]["data"]["name"]
    save_json(DATA_DIR / "state.json", state)
    print(f"Sent once: {entry['comment_id']}")


def inbox(client: Reddit, *, notify: bool, limit: int = 100) -> None:
    state = read_json(DATA_DIR / "state.json")
    processed = set(state.get("processed_inbox_ids", []))
    fetched = []
    after = None
    while True:
        params = {"limit": min(limit, 100), "mark": "false"}
        if after:
            params["after"] = after
        page = client.request("GET", "/message/inbox", params)
        fetched.extend(children(page))
        after = page.get("data", {}).get("after")
        if not after or len(fetched) >= limit:
            break
    unseen = [item for item in fetched[:limit] if item.get("name") not in processed]
    for item in (unseen if notify else fetched[:limit]):
        print(summary(item))
        if item.get("context"):
            print(" ", "https://www.reddit.com" + item["context"])
    if notify:
        processed.update(item["name"] for item in unseen if item.get("name"))
        state["processed_inbox_ids"] = sorted(processed)
        save_json(DATA_DIR / "state.json", state)
        print(f"{len(unseen)} new inbox item(s); no items marked read on Reddit.")
        if unseen and sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'display notification "{len(unseen)} new Reddit inbox item(s)" with title "Reddit personal client"'],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def watch(interval: int) -> None:
    if interval < 300:
        raise ClientError("The polling interval must be at least 300 seconds.")
    print(f"Checking Reddit inbox every {interval} seconds. Press Ctrl-C to stop.")
    while True:
        try:
            inbox(Reddit(), notify=True)
        except ClientError as error:
            print(f"Inbox check failed: {error}", file=sys.stderr)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth-start")
    finish = sub.add_parser("auth-finish")
    finish.add_argument("redirect_url")
    sub.add_parser("probe")
    listing = sub.add_parser("new")
    listing.add_argument("--limit", type=int, default=10)
    item = sub.add_parser("thread")
    item.add_argument("post_id")
    create = sub.add_parser("draft")
    create.add_argument("parent_id")
    create.add_argument("--text", help="Reply text; omit to read standard input")
    show = sub.add_parser("draft-show")
    show.add_argument("draft_id")
    sending = sub.add_parser("send")
    sending.add_argument("draft_id")
    sub.add_parser("inbox")
    sub.add_parser("watch-once")
    watching = sub.add_parser("watch")
    watching.add_argument("--interval", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        if args.command == "draft":
            draft(args.parent_id, args.text if args.text is not None else sys.stdin.read())
        elif args.command == "draft-show":
            show_draft(args.draft_id)
        elif args.command == "auth-start":
            authorize_start()
        elif args.command == "auth-finish":
            authorize_finish(args.redirect_url)
        elif args.command == "watch":
            require_approval()
            watch(args.interval)
        else:
            client = Reddit()
            if args.command == "probe":
                probe(client)
            elif args.command == "new":
                list_posts(client, args.limit)
            elif args.command == "thread":
                thread(client, args.post_id)
            elif args.command == "send":
                send(client, args.draft_id)
            elif args.command == "inbox":
                inbox(client, notify=False)
            elif args.command == "watch-once":
                inbox(client, notify=True)
        return 0
    except (ClientError, ValueError, KeyError, IndexError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
