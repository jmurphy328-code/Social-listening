#!/usr/bin/env python3
"""Collect public posts about each project in config.json from free sources.

Sources: YouTube (videos and comments), Reddit (posts and comments),
Bluesky (posts), Mastodon (hashtag posts across several servers), Lemmy
(posts and comments), Tumblr (tagged posts) and GDELT (news headlines).
Each source runs only when its key is present; a missing key is reported
as "skipped", never an error.

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
USER_AGENT = "social-listening-collector/1.0 (academic research; https://github.com/jmurphy328-code/Social-listening)"
CSV_COLUMNS = ["date", "platform", "author", "text", "engagement", "url", "type", "query"]
DEFAULT_LIMITS = {
    "youtube_videos_per_query": 25,
    "youtube_comment_videos": 15,
    "youtube_comment_pages": 2,
    "reddit_pages": 2,
    "reddit_comment_posts": 10,
    "bluesky_pages": 5,
    "mastodon_pages": 5,
    "lemmy_pages": 3,
    "tumblr_pages": 5,
}
DEFAULT_MASTODON_INSTANCES = ["mastodon.social", "mastodon.online", "mstdn.social", "mas.to", "mastodon.world"]


class Skip(Exception):
    """The source is not set up (for example its key is missing)."""


class SourceError(Exception):
    """The source was set up but the request failed."""


class Partial(Exception):
    """Some requests worked and some did not. The posts found are kept; the source tries again in full next run."""

    def __init__(self, found, note):
        super().__init__(note)
        self.found = found


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
            detail = err.read().decode("utf-8", "replace")
            detail = "" if detail.lstrip().startswith("<") else detail[:240].replace("\n", " ")  # drop HTML error pages
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise SourceError(f"{host} answered {err.code}" + (f": {detail}" if detail else "")) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            if attempt < 2:
                time.sleep(3)
                continue
            raise SourceError(f"{host} could not be reached: {err}") from None
    raise SourceError(f"{host} could not be reached")


def http_text(url, *, params=None, timeout=30):
    """GET and return the body as text (for RSS feeds)."""
    host = urllib.parse.urlsplit(url).netloc
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise SourceError(f"{host} answered {err.code}") from None
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt < 2:
                time.sleep(3)
                continue
            raise SourceError(f"{host} could not be reached: {err}") from None
    raise SourceError(f"{host} could not be reached")


def relevant(project, text):
    """True when the project has no "relevance" words, or the text has one of them.

    Used by the sources that search loosely (Lemmy, Tumblr), where a
    title like "The Odyssey" also catches Homer."""
    words = [w.lower() for w in project.get("relevance", []) if str(w).strip()]
    low = str(text or "").lower()
    return not words or any(w in low for w in words)


def plain_terms(project, key):
    """Search terms for sources without phrase search: the project's own list, else its title."""
    terms = project.get(key) or [project.get("title", "")]
    return [str(t).replace('"', "").strip() for t in terms if str(t).replace('"', "").strip()]


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

def mastodon_instances(project):
    listed = project.get("mastodon_instances") or ([project["mastodon_instance"]] if project.get("mastodon_instance") else DEFAULT_MASTODON_INSTANCES)
    return [str(i).strip().strip("/").removeprefix("https://") for i in listed if str(i).strip()]


def collect_mastodon(project, since_iso, limits, first_run):
    """Hashtag timelines from several servers. Each server only sees the posts that reach it,
    so asking several finds more; a post seen on two servers is kept once (same uri)."""
    tags = [tag.lstrip("#") for tag in project.get("hashtags", []) if tag.strip("# ")]
    if not tags:
        raise Skip("add a hashtag to the project in config.json")
    instances = mastodon_instances(project)
    token = os.environ.get("MASTODON_TOKEN", "").strip()
    found, refused, failed = {}, [], []
    for n, instance in enumerate(instances):
        auth = {"Authorization": "Bearer " + token} if token and n == 0 else {}  # the token belongs to the first server
        try:
            for tag in tags:
                max_id = None
                for _ in range(limits["mastodon_pages"]):
                    params = {"limit": 40}
                    if max_id:
                        params["max_id"] = max_id
                    statuses = http(f"https://{instance}/api/v1/timelines/tag/{urllib.parse.quote(tag)}", params=params, headers=auth)
                    if not isinstance(statuses, list) or not statuses:
                        break
                    for st in statuses:
                        rid = "masto:" + str(st.get("uri") or st.get("id"))
                        engagement = sum(number(st.get(k)) for k in ("favourites_count", "reblogs_count", "replies_count"))
                        rec_ = record(rid, "Mastodon", "post", st.get("created_at"), (st.get("account") or {}).get("acct"),
                                      strip_html(st.get("content")), engagement, st.get("url") or st.get("uri") or "", "#" + tag)
                        if rid not in found or rec_["engagement"] > found[rid]["engagement"]:
                            found[rid] = rec_  # servers count engagement differently; keep the fullest count
                    max_id = statuses[-1].get("id")
                    if to_iso(statuses[-1].get("created_at")) < since_iso:
                        break
                    if first_run:
                        time.sleep(0.3)  # a deep pass asks for many pages; stay well inside the server's rate limit
        except SourceError as err:
            (refused if re.search(r"answered (401|403|422)", str(err)) else failed).append(f"{instance} ({err})")
    if not found and (refused or failed) and len(refused) + len(failed) == len(instances):
        if refused and not failed:
            raise Skip("every server wants a login for hashtag pages; add the MASTODON_TOKEN secret")
        raise SourceError("; ".join(refused + failed))
    return list(found.values())


# ---------- Lemmy (no key) ----------

def collect_lemmy(project, since_iso, limits, first_run):
    """Posts and comments from Lemmy's search. lemmy.world federates with most of the network."""
    instance = str(project.get("lemmy_instance", "lemmy.world")).strip().strip("/").removeprefix("https://")
    found = []
    for term in plain_terms(project, "lemmy_queries"):
        for kind in ("Posts", "Comments"):
            for page in range(1, limits["lemmy_pages"] + 1):
                res = http(f"https://{instance}/api/v3/search",
                           params={"q": term, "type_": kind, "sort": "New", "listing_type": "All", "limit": 50, "page": page})
                items = res.get("posts" if kind == "Posts" else "comments") or []
                oldest = None
                for item in items:
                    creator, counts = item.get("creator") or {}, item.get("counts") or {}
                    author = creator.get("name", "")
                    if kind == "Posts":
                        p = item.get("post") or {}
                        text = p.get("name", "") + (" | " + str(p.get("body"))[:400] if p.get("body") else "")
                        rec_ = record("lemmy:" + str(p.get("ap_id") or p.get("id")), "Lemmy", "post", p.get("published"), author, text,
                                      number(counts.get("score")) + number(counts.get("comments")), p.get("ap_id") or "", term)
                    else:
                        c, parent = item.get("comment") or {}, item.get("post") or {}
                        rec_ = record("lemmy:" + str(c.get("ap_id") or c.get("id")), "Lemmy", "comment", c.get("published"), author,
                                      c.get("content"), number(counts.get("score")) + number(counts.get("child_count")),
                                      c.get("ap_id") or "", (parent.get("name") or term)[:80])
                    oldest = rec_["date"] if not oldest or (rec_["date"] and rec_["date"] < oldest) else oldest
                    context = rec_["text"] + " " + (item.get("post") or {}).get("name", "") + " " + (item.get("community") or {}).get("name", "")
                    if relevant(project, context):
                        found.append(rec_)
                if len(items) < 50 or (oldest and oldest < since_iso):
                    break
                time.sleep(0.5)
    return found


# ---------- Tumblr (free key) ----------

def collect_tumblr(project, since_iso, limits, first_run):
    key = os.environ.get("TUMBLR_API_KEY", "").strip()
    if not key:
        raise Skip("add the TUMBLR_API_KEY secret")
    tags = project.get("tumblr_tags") or [t.lstrip("#") for t in project.get("hashtags", [])]
    since_ts = dt.datetime.fromisoformat(since_iso.replace("Z", "+00:00")).timestamp() if since_iso else 0
    found = []
    for tag in tags:
        before = None
        for _ in range(limits["tumblr_pages"]):
            params = {"tag": tag, "api_key": key, "limit": 20, "filter": "text"}
            if before:
                params["before"] = before
            posts = http("https://api.tumblr.com/v2/tagged", params=params).get("response") or []
            if not isinstance(posts, list) or not posts:
                break
            for p in posts:
                parts = [p.get("title"), p.get("summary"), p.get("body"), p.get("caption"), p.get("text")]
                text = " | ".join(dict.fromkeys(strip_html(x) for x in parts if x and strip_html(x)))
                if not text and p.get("tags"):
                    text = " ".join("#" + t for t in p["tags"])
                if relevant(project, text + " " + " ".join(p.get("tags") or [])):
                    found.append(record("tumblr:" + str(p.get("id_string") or p.get("id")), "Tumblr", "post", p.get("timestamp"),
                                        p.get("blog_name"), text, p.get("note_count"), p.get("post_url") or "", "#" + tag))
            before = min(number(p.get("timestamp")) for p in posts)
            if before < since_ts:
                break
            time.sleep(0.5)
    return found


# ---------- News coverage from GDELT (no key; headlines, not posts) ----------

def half_month_windows(since_iso, now):
    start = dt.date.fromisoformat(since_iso[:10])
    end = now.date() + dt.timedelta(days=1)
    while start < end:
        nxt = start.replace(day=16) if start.day < 16 else (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        yield start, min(nxt, end)
        start = nxt


GDELT_PAUSE = 6  # GDELT asks for no more than one request every five seconds
NEWS_BUDGET = 12 * 60  # seconds; past this, or after three refused ranges in a row, the rest wait for the next run


def gdelt_stamp(day):
    return day.strftime("%Y%m%d") + "000000"


def collect_news(project, since_iso, limits, first_run, now=None):
    """English-language news articles from the GDELT index, a free research database of world news.
    It returns up to 250 articles per request, so a deep pass asks half a month at a time."""
    now = now or dt.datetime.now(dt.timezone.utc)
    queries = project.get("news_queries") or project.get("queries", [])[:1]
    if not queries:
        raise Skip("add queries to the project in config.json")
    windows = list(half_month_windows(since_iso, now))
    if not first_run:
        windows = [(dt.date.fromisoformat(since_iso[:10]), now.date() + dt.timedelta(days=1))]
    found, asked, missed, last_err, streak = [], 0, 0, "", 0
    began = time.monotonic()
    for query in queries:
        for start, end in windows:
            body = None
            if streak >= 3 or time.monotonic() - began > NEWS_BUDGET:
                missed += 1
                continue
            for wait in (0, 30, 90):  # GitHub's servers are shared, so GDELT may already be counting someone else's requests
                if asked or wait:
                    time.sleep(GDELT_PAUSE + wait)
                asked += 1
                try:
                    body = http_text("https://api.gdeltproject.org/api/v2/doc/doc", params={
                        "query": f"{query} sourcelang:english", "mode": "artlist", "format": "json", "maxrecords": 250,
                        "sort": "datedesc", "startdatetime": gdelt_stamp(start), "enddatetime": gdelt_stamp(end)})
                except SourceError as err:
                    last_err = str(err)
                    continue
                if body.strip() and not body.lstrip().startswith("{"):
                    last_err, body = "api.gdeltproject.org: " + clean(body, 160), None  # a "please slow down" page
                    continue
                break
            if body is None:
                missed, streak = missed + 1, streak + 1
                continue
            streak = 0
            try:
                articles = json.loads(body).get("articles") or [] if body.strip() else []  # empty body: nothing matched
            except json.JSONDecodeError:
                missed += 1
                continue
            for art in articles:
                seen = str(art.get("seendate", ""))
                when = f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}T{seen[9:11]}:{seen[11:13]}:{seen[13:15]}Z" if len(seen) >= 15 else ""
                if art.get("title") and art.get("url"):
                    found.append(record("news:" + art["url"], "News", "article", when, art.get("domain"), art["title"], 0, art["url"], query))
    total = len(queries) * len(windows)
    if missed == total:
        raise SourceError(last_err or "api.gdeltproject.org sent nothing usable")
    if missed:
        raise Partial(found, f"{missed} of {total} date ranges missed ({last_err}); they are asked again next run")
    return found


# ---------- Wikipedia pageviews (a daily attention series, not posts) ----------

def collect_pageviews(project, start_iso, now):
    """Daily views of the film's English Wikipedia article, as [(YYYY-MM-DD, views)]. Free, no key."""
    article = str(project.get("wikipedia", "")).strip().replace(" ", "_")
    if not article:
        raise Skip('add "wikipedia" (the article title) to the project in config.json')
    first = (start_iso or "2015-07-01")[:10].replace("-", "")
    last = (now - dt.timedelta(days=1)).strftime("%Y%m%d")
    res = http("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
               + urllib.parse.quote(article, safe="") + f"/daily/{first}00/{last}00")
    days = []
    for item in res.get("items", []):
        stamp = str(item.get("timestamp", ""))
        if len(stamp) >= 8 and stamp[:8].isdigit():
            days.append((f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", number(item.get("views"))))
    return sorted(days)


def save_pageviews(folder, days):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pageviews.csv"
    had = len(path.read_text(encoding="utf-8").splitlines()) - 1 if path.exists() else 0
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["date", "views"])
        writer.writerows(days)
    return max(0, len(days) - max(0, had))


SOURCES = {"youtube": collect_youtube, "reddit": collect_reddit, "bluesky": collect_bluesky, "mastodon": collect_mastodon,
           "lemmy": collect_lemmy, "tumblr": collect_tumblr, "news": collect_news}


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


PLATFORM_NAMES = {"youtube": "YouTube", "reddit": "Reddit", "bluesky": "Bluesky", "mastodon": "Mastodon",
                  "lemmy": "Lemmy", "tumblr": "Tumblr", "news": "News"}
DEEP_PAGES = {"mastodon_pages": 30, "bluesky_pages": 4, "reddit_pages": 2, "lemmy_pages": 4, "tumblr_pages": 20}


def source_signature(name, project):
    """What a source is asked for. When this changes, the source gets a deep pass back to "since"."""
    if name == "mastodon":
        return json.dumps([sorted(project.get("hashtags", [])), mastodon_instances(project)])
    if name == "lemmy":
        return json.dumps([plain_terms(project, "lemmy_queries"), project.get("lemmy_instance", "lemmy.world")])
    if name == "tumblr":
        return json.dumps(sorted(project.get("tumblr_tags") or project.get("hashtags", [])))
    if name == "news":
        return json.dumps(["gdelt", project.get("news_queries") or project.get("queries", [])[:1]])
    return ""  # the original sources: a deep pass only when they have nothing on file


def run_project(project, config, now):
    """Collect one project.

    A source gets a deep first pass, back to the project's "since" date with
    larger page limits, when it has no posts on file yet or when "since" has
    been moved earlier than last time. Otherwise it only looks back
    "lookback_days".
    """
    slug = project["slug"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", slug):
        raise ValueError(f"project slug {slug!r} must be lowercase letters, digits and dashes")
    folder = ROOT / "data" / slug
    posts = load_posts(folder / "posts.jsonl")
    limits = {**DEFAULT_LIMITS, **config.get("limits", {})}
    deep_limits = {**limits, **{key: limits[key] * times for key, times in DEEP_PAGES.items()}}
    start = to_iso(project.get("since", "") + "T00:00:00") if project.get("since") else ""
    lookback = to_iso((now - dt.timedelta(days=int(config.get("lookback_days", 14)))).timestamp())
    state_path = folder / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    moved_earlier = bool(posts) and bool(start) and start < state.get("since", "9999")
    report = {"slug": slug, "title": project.get("title", slug), "since": start or lookback, "sources": {}}
    signatures = dict(state.get("signatures", {}))
    for name, collect in SOURCES.items():
        if name in project.get("skip_sources", []):
            report["sources"][name] = {"status": "skipped", "note": "turned off in config.json"}
            continue
        signature = source_signature(name, project)
        changed = bool(signature) and state.get("signatures", {}).get(name) != signature
        fresh = moved_earlier or changed or not any(rec["platform"] == PLATFORM_NAMES.get(name) for rec in posts.values())
        since_iso = (start or lookback) if fresh else max(start, lookback)
        try:
            args = (project, since_iso, deep_limits if fresh else limits, fresh)
            gathered = collect(*args, now=now) if name == "news" else collect(*args)
            found = [rec for rec in gathered if keep(rec, start or "")]
            if signature:
                signatures[name] = signature
            new = sum(1 for rec in found if rec["id"] not in posts)
            posts.update({rec["id"]: rec for rec in found})
            report["sources"][name] = {"status": "ok", "found": len(found), "new": new}
        except Partial as part:
            found = [rec for rec in part.found if keep(rec, start or "")]
            new = sum(1 for rec in found if rec["id"] not in posts)
            posts.update({rec["id"]: rec for rec in found})
            report["sources"][name] = {"status": "partial", "found": len(found), "new": new, "note": str(part)}
        except Skip as why:
            report["sources"][name] = {"status": "skipped", "note": str(why)}
        except SourceError as err:
            report["sources"][name] = {"status": "error", "note": str(err)}
    try:
        days = collect_pageviews(project, start, now)
        report["sources"]["wikipedia"] = {"status": "ok", "found": len(days), "new": save_pageviews(folder, days)}
    except Skip as why:
        report["sources"]["wikipedia"] = {"status": "skipped", "note": str(why)}
    except SourceError as err:
        report["sources"]["wikipedia"] = {"status": "error", "note": str(err)}
    save_posts(folder, posts)
    state_path.write_text(json.dumps({"since": start, "signatures": signatures}) + "\n", encoding="utf-8")
    report["total"] = len(posts)
    return report


def summary_markdown(reports, ran_at):
    lines = [f"## Collection run, {ran_at}", ""]
    for rep in reports:
        lines += [f"### {rep['title']}: {rep['total']:,} posts on file", "", "| Source | Result |", "|---|---|"]
        for name, res in rep["sources"].items():
            if res["status"] == "ok" and name == "wikipedia":
                result = f"{res['found']:,} days of pageviews, {res['new']:,} new"
            elif res["status"] == "ok":
                result = f"{res['found']:,} found, {res['new']:,} new"
            elif res["status"] == "partial":
                result = f"{res['found']:,} found, {res['new']:,} new; {res['note']}"
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
