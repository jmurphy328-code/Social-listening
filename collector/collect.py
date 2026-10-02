#!/usr/bin/env python3
"""Collect public posts about each project in config.json from free sources.

Sources: YouTube (videos and comments), Reddit (posts and comments),
Bluesky (posts) and Mastodon (hashtag posts). Each source runs only when
its key is present; a missing key is reported as "skipped", never an error.

Results are merged into data/<slug>/posts.jsonl (one record per line, the
full history) and data/<slug>/posts.csv (the file to add to the Listening
Desk). Uses only the Python standard library.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "social-listening-collector/1.0 (academic research)"
CSV_COLUMNS = ["date", "platform", "author", "text", "engagement", "url", "type", "query"]
DEFAULT_LIMITS = {
    "youtube_videos_per_query": 25,
    "youtube_comment_videos": 15,
    "youtube_comment_pages": 2,
    "reddit_pages": 2,
    "reddit_comment_posts": 10,
    "bluesky_pages": 5,
    "mastodon_pages": 5,
}


class Skip(Exception):
    """The source is not set up (for example its key is missing)."""


class SourceError(Exception):
    """The source was set up but the request failed."""


# ---------- helpers ----------

def http(url, *, params=None, headers=None, data=None, json_body=None, timeout=30):
    """GET or POST and return parsed JSON. Error text never includes the query string, so keys stay out of logs."""
    host = urllib.parse.urlsplit(url).netloc
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=hdrs)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:240].replace("\n", " ")
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise SourceError(f"{host} answered {err.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            if attempt < 2:
                time.sleep(3)
                continue
            raise SourceError(f"{host} could not be reached: {err}") from None
    raise SourceError(f"{host} could not be reached")


def clean(text, limit=1000):
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()[:limit]


def strip_html(text):
    text = re.sub(r"<br\s*/?>|</p>", " ", str(text or ""), flags=re.I)
    return clean(re.sub(r"<[^>]+>", "", text))


def to_iso(value):
    """Return a UTC timestamp like 2026-09-14T13:22:00Z from seconds or an ISO string, or '' if unreadable."""
    try:
        if isinstance(value, (int, float)):
            moment = dt.datetime.fromtimestamp(value, dt.timezone.utc)
        else:
            moment = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=dt.timezone.utc)
        return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return ""


def number(value):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def record(rid, platform, kind, date, author, text, engagement, url, query):
    return {"id": rid, "platform": platform, "type": kind, "date": to_iso(date), "author": clean(author, 80),
            "text": clean(text), "engagement": number(engagement), "url": url, "query": query}


def keep(rec, since_iso):
    return bool(rec["text"]) and bool(rec["date"]) and rec["date"] >= since_iso


# ---------- YouTube ----------

def collect_youtube(project, since_iso, limits, first_run):
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise Skip("add the YOUTUBE_API_KEY secret")
    api = "https://www.googleapis.com/youtube/v3/"
    found, video_query = [], {}
    for query in project.get("queries", []):
        res = http(api + "search", params={
            "part": "snippet", "q": query, "type": "video", "maxResults": min(50, limits["youtube_videos_per_query"]),
            "order": "relevance" if first_run else "date", "publishedAfter": since_iso, "key": key})
        for item in res.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            if vid:
                video_query.setdefault(vid, query)
    ids = list(video_query)
    stats = {}
    for start in range(0, len(ids), 50):
        res = http(api + "videos", params={"part": "snippet,statistics", "id": ",".join(ids[start:start + 50]), "key": key})
        for item in res.get("items", []):
            stats[item["id"]] = item
    for vid, item in stats.items():
        sn, st = item.get("snippet", {}), item.get("statistics", {})
        text = sn.get("title", "") + (" | " + sn.get("description", "")[:280] if sn.get("description") else "")
        found.append(record("yt:v:" + vid, "YouTube", "video", sn.get("publishedAt"), sn.get("channelTitle"), text,
                            number(st.get("likeCount")) + number(st.get("commentCount")),
                            "https://www.youtube.com/watch?v=" + vid, video_query[vid]))
    busiest = sorted(stats, key=lambda v: number(stats[v].get("statistics", {}).get("commentCount")), reverse=True)
    for vid in busiest[:limits["youtube_comment_videos"]]:
        if not number(stats[vid].get("statistics", {}).get("commentCount")):
            continue
        token = None
        for _ in range(limits["youtube_comment_pages"]):
            params = {"part": "snippet", "videoId": vid, "maxResults": 100, "textFormat": "plainText",
                      "order": "relevance" if first_run else "time", "key": key}
            if token:
                params["pageToken"] = token
            try:
                res = http(api + "commentThreads", params=params)
            except SourceError as err:
                if "commentsDisabled" in str(err) or "answered 404" in str(err):
                    break  # comments are turned off or the video is gone
                raise
            for item in res.get("items", []):
                top = (item.get("snippet") or {}).get("topLevelComment") or {}
                sn = top.get("snippet") or {}
                found.append(record("yt:c:" + str(top.get("id")), "YouTube", "comment", sn.get("publishedAt"),
                                    sn.get("authorDisplayName"), sn.get("textOriginal") or sn.get("textDisplay"),
                                    number(sn.get("likeCount")) + number(item["snippet"].get("totalReplyCount")),
                                    f"https://www.youtube.com/watch?v={vid}&lc={top.get('id')}", video_query[vid]))
            token = res.get("nextPageToken")
            if not token:
                break
    return found


# ---------- Reddit ----------

def collect_reddit(project, since_iso, limits, first_run):
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise Skip("add the REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET secrets")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    token = http("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                 headers={"Authorization": "Basic " + basic}).get("access_token")
    if not token:
        raise SourceError("Reddit did not return an access token; check the client id and secret")
    auth = {"Authorization": "Bearer " + token}
    found, posts = [], {}
    for query in project.get("queries", []):
        after = None
        for _ in range(limits["reddit_pages"]):
            params = {"q": query, "limit": 100, "type": "link", "raw_json": 1,
                      "sort": "relevance" if first_run else "new", "t": "year" if first_run else "month"}
            if after:
                params["after"] = after
            listing = http("https://oauth.reddit.com/search", params=params, headers=auth).get("data", {})
            for child in listing.get("children", []):
                d = child.get("data", {})
                if d.get("id") and d["id"] not in posts:
                    posts[d["id"]] = d
                    text = d.get("title", "") + (" | " + d.get("selftext", "")[:400] if d.get("selftext") else "")
                    found.append(record("reddit:t3_" + d["id"], "Reddit", "post", d.get("created_utc"), d.get("author"), text,
                                        number(d.get("score")) + number(d.get("num_comments")),
                                        "https://www.reddit.com" + d.get("permalink", ""), query))
            after = listing.get("after")
            if not after:
                break
    busiest = sorted(posts.values(), key=lambda d: number(d.get("num_comments")), reverse=True)
    for d in busiest[:limits["reddit_comment_posts"]]:
        if not number(d.get("num_comments")):
            continue
        res = http(f"https://oauth.reddit.com/comments/{d['id']}", headers=auth,
                   params={"limit": 100, "depth": 1, "sort": "top", "raw_json": 1})
        children = res[1].get("data", {}).get("children", []) if isinstance(res, list) and len(res) > 1 else []
        for child in children:
            c = child.get("data", {})
            if child.get("kind") != "t1" or not c.get("id"):
                continue
            found.append(record("reddit:t1_" + c["id"], "Reddit", "comment", c.get("created_utc"), c.get("author"), c.get("body"),
                                number(c.get("score")), "https://www.reddit.com" + c.get("permalink", ""), d.get("title", "")[:80]))
    return found


# ---------- Bluesky ----------

def collect_bluesky(project, since_iso, limits, first_run):
    handle = os.environ.get("BLUESKY_HANDLE", "").strip().lstrip("@")
    password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    base, auth = "https://public.api.bsky.app", {}
    if handle and password:
        session = http("https://bsky.social/xrpc/com.atproto.server.createSession",
                       json_body={"identifier": handle, "password": password})
        auth = {"Authorization": "Bearer " + session["accessJwt"]}
        base = "https://bsky.social"
        for service in (session.get("didDoc") or {}).get("service", []):
            if service.get("id") == "#atproto_pds" and service.get("serviceEndpoint"):
                base = service["serviceEndpoint"].rstrip("/")
    found = []
    terms = list(project.get("queries", [])) + ["#" + tag.lstrip("#") for tag in project.get("hashtags", [])]
    for query in terms:
        cursor = None
        for _ in range(limits["bluesky_pages"]):
            params = {"q": query, "limit": 100, "sort": "latest", "since": since_iso}
            if cursor:
                params["cursor"] = cursor
            try:
                res = http(base + "/xrpc/app.bsky.feed.searchPosts", params=params, headers=auth)
            except SourceError as err:
                if not auth:
                    raise Skip(f"add the BLUESKY_HANDLE and BLUESKY_APP_PASSWORD secrets (search without a login was refused: {err})") from None
                raise
            for post in res.get("posts", []):
                author = (post.get("author") or {}).get("handle", "")
                rec_ = post.get("record") or {}
                rkey = str(post.get("uri", "")).rsplit("/", 1)[-1]
                engagement = sum(number(post.get(k)) for k in ("likeCount", "repostCount", "replyCount", "quoteCount"))
                found.append(record("bsky:" + str(post.get("uri")), "Bluesky", "post", rec_.get("createdAt") or post.get("indexedAt"),
                                    author, rec_.get("text"), engagement, f"https://bsky.app/profile/{author}/post/{rkey}", query))
            cursor = res.get("cursor")
            if not cursor or not res.get("posts"):
                break
    return found


# ---------- Mastodon ----------

def collect_mastodon(project, since_iso, limits, first_run):
    tags = [tag.lstrip("#") for tag in project.get("hashtags", []) if tag.strip("# ")]
    if not tags:
        raise Skip("add a hashtag to the project in config.json")
    instance = project.get("mastodon_instance", "mastodon.social")
    token = os.environ.get("MASTODON_TOKEN", "").strip()
    auth = {"Authorization": "Bearer " + token} if token else {}
    found = []
    for tag in tags:
        max_id = None
        for _ in range(limits["mastodon_pages"]):
            params = {"limit": 40}
            if max_id:
                params["max_id"] = max_id
            try:
                statuses = http(f"https://{instance}/api/v1/timelines/tag/{urllib.parse.quote(tag)}", params=params, headers=auth)
            except SourceError as err:
                if not token and re.search(r"answered (401|403|422)", str(err)):
                    raise Skip(f"{instance} wants a login for hashtag pages; add the MASTODON_TOKEN secret") from None
                raise
            if not isinstance(statuses, list) or not statuses:
                break
            for st in statuses:
                engagement = sum(number(st.get(k)) for k in ("favourites_count", "reblogs_count", "replies_count"))
                found.append(record("masto:" + str(st.get("uri") or st.get("id")), "Mastodon", "post", st.get("created_at"),
                                    (st.get("account") or {}).get("acct"), strip_html(st.get("content")), engagement,
                                    st.get("url") or st.get("uri") or "", "#" + tag))
            max_id = statuses[-1].get("id")
            if to_iso(statuses[-1].get("created_at")) < since_iso:
                break
    return found


SOURCES = {"youtube": collect_youtube, "reddit": collect_reddit, "bluesky": collect_bluesky, "mastodon": collect_mastodon}


# ---------- storage ----------

def load_posts(path):
    posts = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                posts[rec["id"]] = rec
    return posts


def save_posts(folder, posts):
    folder.mkdir(parents=True, exist_ok=True)
    ordered = sorted(posts.values(), key=lambda r: (r["date"], r["id"]), reverse=True)
    with (folder / "posts.jsonl").open("w", encoding="utf-8") as out:
        for rec in ordered:
            out.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    with (folder / "posts.csv").open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)


def run_project(project, config, now):
    slug = project["slug"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", slug):
        raise ValueError(f"project slug {slug!r} must be lowercase letters, digits and dashes")
    folder = ROOT / "data" / slug
    posts = load_posts(folder / "posts.jsonl")
    first_run = not posts
    limits = {**DEFAULT_LIMITS, **config.get("limits", {})}
    start = to_iso(project.get("since", "") + "T00:00:00") if project.get("since") else ""
    lookback = to_iso((now - dt.timedelta(days=int(config.get("lookback_days", 14)))).timestamp())
    since_iso = (start or lookback) if first_run else max(start, lookback)
    report = {"slug": slug, "title": project.get("title", slug), "since": since_iso, "sources": {}}
    for name, collect in SOURCES.items():
        if name in project.get("skip_sources", []):
            report["sources"][name] = {"status": "skipped", "note": "turned off in config.json"}
            continue
        try:
            found = [rec for rec in collect(project, since_iso, limits, first_run) if keep(rec, start or "")]
            new = sum(1 for rec in found if rec["id"] not in posts)
            posts.update({rec["id"]: rec for rec in found})
            report["sources"][name] = {"status": "ok", "found": len(found), "new": new}
        except Skip as why:
            report["sources"][name] = {"status": "skipped", "note": str(why)}
        except SourceError as err:
            report["sources"][name] = {"status": "error", "note": str(err)}
    save_posts(folder, posts)
    report["total"] = len(posts)
    return report


def summary_markdown(reports, ran_at):
    lines = [f"## Collection run, {ran_at}", ""]
    for rep in reports:
        lines += [f"### {rep['title']}: {rep['total']:,} posts on file", "", "| Source | Result |", "|---|---|"]
        for name, res in rep["sources"].items():
            if res["status"] == "ok":
                result = f"{res['found']:,} found, {res['new']:,} new"
            else:
                result = f"{res['status']}: {res['note']}"
            lines.append(f"| {name} | {result} |")
        lines.append("")
    return "\n".join(lines)


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    ran_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    reports = [run_project(project, config, now) for project in config.get("projects", [])]
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "last-run.json").write_text(json.dumps({"ran_at": ran_at, "projects": reports}, indent=2) + "\n", encoding="utf-8")
    text = summary_markdown(reports, ran_at)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(text + "\n")
    failed = [f"{rep['slug']}/{name}" for rep in reports for name, res in rep["sources"].items() if res["status"] == "error"]
    if failed:
        print("Sources that failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
