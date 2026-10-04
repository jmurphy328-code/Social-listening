"""Tests for the collector. No network: each source is fed responses in the shape its API returns."""
import csv
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import collect

LIMITS = dict(collect.DEFAULT_LIMITS)
PROJECT = {"slug": "test-film", "title": "Test Film", "queries": ["test film"], "hashtags": ["TestFilm"], "since": "2026-09-01"}
SINCE = "2026-09-01T00:00:00Z"


def router(routes):
    calls = []

    def fake(url, **kwargs):
        calls.append((url, kwargs))
        for part, answer in routes:
            if part in url:
                return answer(url, kwargs) if callable(answer) else answer
        raise AssertionError("unexpected request: " + url)
    fake.calls = calls
    return fake


class Helpers(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(collect.to_iso(1789392120), "2026-09-14T13:22:00Z")
        self.assertEqual(collect.to_iso("2026-09-14T09:22:00-04:00"), "2026-09-14T13:22:00Z")
        self.assertEqual(collect.to_iso("2026-09-14T13:22:00.000Z"), "2026-09-14T13:22:00Z")
        self.assertEqual(collect.to_iso("not a date"), "")

    def test_text(self):
        self.assertEqual(collect.strip_html("<p>Loved it<br>so much &amp; more</p><p>#TestFilm</p>"), "Loved it so much & more #TestFilm")
        self.assertEqual(collect.number("12"), 12)
        self.assertEqual(collect.number(None), 0)


class Sources(unittest.TestCase):
    def test_youtube(self):
        fake = router([
            ("/search", {"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]}),
            ("/videos", {"items": [
                {"id": "v1", "snippet": {"title": "Test Film review", "description": "Worth it?", "channelTitle": "Critic", "publishedAt": "2026-09-10T10:00:00Z"}, "statistics": {"likeCount": "10", "commentCount": "2"}},
                {"id": "v2", "snippet": {"title": "Trailer", "channelTitle": "Studio", "publishedAt": "2026-09-02T10:00:00Z"}, "statistics": {"likeCount": "5"}}]}),
            ("/commentThreads", lambda url, kw: {"items": [{"snippet": {"totalReplyCount": 3, "topLevelComment": {"id": "c1", "snippet": {"textOriginal": "Loved it", "authorDisplayName": "@fan", "likeCount": 7, "publishedAt": "2026-09-11T08:00:00Z"}}}}]}),
        ])
        with mock.patch.object(collect, "http", fake), mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "k"}):
            found = collect.collect_youtube(PROJECT, SINCE, LIMITS, True)
        by_id = {r["id"]: r for r in found}
        self.assertEqual(set(by_id), {"yt:v:v1", "yt:v:v2", "yt:c:c1"})
        self.assertEqual(by_id["yt:v:v1"]["engagement"], 12)
        self.assertEqual(by_id["yt:c:c1"]["engagement"], 10)
        self.assertEqual(by_id["yt:c:c1"]["url"], "https://www.youtube.com/watch?v=v1&lc=c1")
        self.assertEqual(sum("/commentThreads" in url for url, _ in fake.calls), 1)  # v2 has no comments, so it is not asked

    def test_youtube_needs_key(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}):
            with self.assertRaises(collect.Skip):
                collect.collect_youtube(PROJECT, SINCE, LIMITS, True)

    def test_reddit(self):
        fake = router([
            ("access_token", {"access_token": "t"}),
            ("/search", {"data": {"after": None, "children": [{"kind": "t3", "data": {"id": "p1", "title": "Test Film discussion", "selftext": "Thoughts?", "author": "mod", "created_utc": 1789392120, "score": 100, "num_comments": 2, "permalink": "/r/movies/comments/p1/x/"}}]}}),
            ("/comments/p1", [{"data": {"children": []}}, {"data": {"children": [
                {"kind": "t1", "data": {"id": "c1", "body": "Stunning", "author": "a", "created_utc": 1789395720, "score": 9, "permalink": "/r/movies/comments/p1/x/c1/"}},
                {"kind": "more", "data": {"id": "m"}}]}}]),
        ])
        with mock.patch.object(collect, "http", fake), mock.patch.dict(os.environ, {"REDDIT_CLIENT_ID": "i", "REDDIT_CLIENT_SECRET": "s"}):
            found = collect.collect_reddit(PROJECT, SINCE, LIMITS, False)
        self.assertEqual([r["id"] for r in found], ["reddit:t3_p1", "reddit:t1_c1"])
        self.assertEqual(found[0]["engagement"], 102)
        self.assertEqual(found[0]["text"], "Test Film discussion | Thoughts?")
        self.assertEqual(found[1]["url"], "https://www.reddit.com/r/movies/comments/p1/x/c1/")

    def test_bluesky_with_login_pages_through_results(self):
        pages = iter([
            {"cursor": "next", "posts": [{"uri": "at://did:plc:a/app.bsky.feed.post/r1", "author": {"handle": "ana.bsky.social"}, "record": {"text": "A masterpiece", "createdAt": "2026-09-14T13:22:00.000Z"}, "likeCount": 4, "repostCount": 1, "replyCount": 0, "quoteCount": 0}]},
            {"posts": []}, {"posts": []},
        ])
        fake = router([("createSession", {"accessJwt": "jwt", "didDoc": {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example"}]}}),
                       ("searchPosts", lambda url, kw: next(pages))])
        with mock.patch.object(collect, "http", fake), mock.patch.dict(os.environ, {"BLUESKY_HANDLE": "@me.bsky.social", "BLUESKY_APP_PASSWORD": "p"}):
            found = collect.collect_bluesky(PROJECT, SINCE, LIMITS, False)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["url"], "https://bsky.app/profile/ana.bsky.social/post/r1")
        self.assertEqual(found[0]["engagement"], 5)
        self.assertTrue(all(url.startswith("https://pds.example") for url, _ in fake.calls if "searchPosts" in url))

    def test_bluesky_without_login_is_skipped_when_refused(self):
        def refuse(url, **kw):
            raise collect.SourceError("public.api.bsky.app answered 403: forbidden")
        with mock.patch.object(collect, "http", refuse), mock.patch.dict(os.environ, {"BLUESKY_HANDLE": "", "BLUESKY_APP_PASSWORD": ""}):
            with self.assertRaises(collect.Skip):
                collect.collect_bluesky(PROJECT, SINCE, LIMITS, False)

    def test_mastodon(self):
        fake = router([("/timelines/tag/TestFilm", lambda url, kw: [] if "max_id" in str(kw.get("params")) else [
            {"id": "9", "uri": "https://m.example/users/a/statuses/9", "url": "https://m.example/@a/9", "created_at": "2026-09-14T13:22:00.000Z", "account": {"acct": "a@m.example"}, "content": "<p>Great <a href='#'>#TestFilm</a></p>", "favourites_count": 2, "reblogs_count": 1, "replies_count": 0}])])
        with mock.patch.object(collect, "http", fake):
            found = collect.collect_mastodon(PROJECT, SINCE, LIMITS, False)
        self.assertEqual(found[0]["text"], "Great #TestFilm")
        self.assertEqual(found[0]["engagement"], 3)
        self.assertEqual(found[0]["url"], "https://m.example/@a/9")


class Runs(unittest.TestCase):
    def test_merge_report_and_csv(self):
        old = collect.record("x:1", "Bluesky", "post", "2026-08-01T00:00:00Z", "a", "too early", 1, "", "q")
        first = [collect.record("x:2", "Bluesky", "post", "2026-09-05T00:00:00Z", "b", "Good, \"quoted\"\nline", 2, "https://e/2", "q"), old]
        again = [collect.record("x:2", "Bluesky", "post", "2026-09-05T00:00:00Z", "b", "Good, \"quoted\"\nline", 9, "https://e/2", "q"),
                 collect.record("x:3", "Bluesky", "post", "2026-09-06T00:00:00Z", "c", "New one", 0, "https://e/3", "q")]

        def skip(*args):
            raise collect.Skip("add the key")

        def broken(*args):
            raise collect.SourceError("host answered 500")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(collect, "ROOT", Path(tmp)):
            now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
            with mock.patch.dict(collect.SOURCES, {"youtube": skip, "reddit": broken, "bluesky": lambda *a: first, "mastodon": skip}):
                rep = collect.run_project(PROJECT, {}, now)
            self.assertEqual(rep["sources"]["bluesky"], {"status": "ok", "found": 1, "new": 1})  # the August post is before "since"
            self.assertEqual(rep["sources"]["youtube"]["status"], "skipped")
            self.assertEqual(rep["sources"]["reddit"]["status"], "error")
            seen = {}
            with mock.patch.dict(collect.SOURCES, {"youtube": skip, "reddit": skip, "mastodon": skip,
                                                   "bluesky": lambda project, since, limits, first_run: seen.update(since=since, first=first_run) or again}):
                rep = collect.run_project(PROJECT, {"lookback_days": 7}, now)
            self.assertEqual(seen, {"since": "2026-09-13T00:00:00Z", "first": False})
            self.assertEqual(rep["sources"]["bluesky"], {"status": "ok", "found": 2, "new": 1})
            self.assertEqual(rep["total"], 2)
            with (Path(tmp) / "data" / "test-film" / "posts.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["text"] for r in rows], ["New one", 'Good, "quoted" line'])
            self.assertEqual(rows[1]["engagement"], "9")  # re-seen posts take the newer engagement count
            self.assertEqual(list(rows[0]), collect.CSV_COLUMNS)
            self.assertIn("| reddit | skipped: add the key |", collect.summary_markdown([rep], "now"))

    def test_deep_pass_for_a_new_source_or_an_earlier_start(self):
        seen = []

        def spy(platform):
            def collect_(project, since, limits, first_run):
                seen.append((platform, since, first_run, limits["mastodon_pages"]))
                return [collect.record(platform + ":1", platform, "post", "2026-09-05T00:00:00Z", "a", "text", 0, "", "q")]
            return collect_

        def skip(*args):
            raise collect.Skip("no key")
        now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(collect, "ROOT", Path(tmp)):
            only_masto = {"youtube": skip, "reddit": skip, "bluesky": skip, "mastodon": spy("Mastodon")}
            with mock.patch.dict(collect.SOURCES, only_masto):
                collect.run_project(PROJECT, {}, now)
                collect.run_project(PROJECT, {}, now)
            self.assertEqual(seen, [("Mastodon", "2026-09-01T00:00:00Z", True, 150), ("Mastodon", "2026-09-06T00:00:00Z", False, 5)])
            del seen[:]
            with mock.patch.dict(collect.SOURCES, {**only_masto, "youtube": spy("YouTube")}):
                collect.run_project(PROJECT, {}, now)  # a source whose key was just added starts from the beginning
            self.assertEqual(seen, [("YouTube", "2026-09-01T00:00:00Z", True, 150), ("Mastodon", "2026-09-06T00:00:00Z", False, 5)])
            del seen[:]
            with mock.patch.dict(collect.SOURCES, only_masto):
                collect.run_project({**PROJECT, "since": "2026-07-01"}, {}, now)  # an earlier start date triggers one deep pass
                collect.run_project({**PROJECT, "since": "2026-07-01"}, {}, now)
            self.assertEqual([(x[1], x[2]) for x in seen], [("2026-07-01T00:00:00Z", True), ("2026-09-06T00:00:00Z", False)])

    def test_bad_slug(self):
        with self.assertRaises(ValueError):
            collect.run_project({"slug": "../x"}, {}, dt.datetime.now(dt.timezone.utc))

    def test_starter_config_is_valid(self):
        config = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
        self.assertTrue(config["projects"][0]["queries"])


if __name__ == "__main__":
    unittest.main()
