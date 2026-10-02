# Social listening collector

This repository gathers public posts about a film or show from the free sources, once a day, and saves them as a file you can add to the Film Listening Desk.

## What it collects

| Source | What you get | What it needs |
|---|---|---|
| YouTube | Videos that match your search terms, plus their top comments | A free API key |
| Reddit | Posts that match, plus top comments on the busiest ones | A free app registration (Reddit reviews new apps) |
| Bluesky | Posts that match your terms and hashtags | A free account and an app password |
| Mastodon | Posts using your hashtags | Nothing, unless the server asks for a login |

A source with no key is skipped, not failed. Add keys one at a time and the collector picks them up on the next run.

X, Instagram, Facebook and TikTok are not here. None offers free keyword search to the public. For those, keep using scraper files and add them to the Desk directly. TikTok does run a free Research API for university researchers, by application; it can be added here once you have access.

## Where the data lands

- `data/<project>/posts.csv` is the file to add to the Desk. Download it, open the Desk, choose **Add data**, and pick the file. The Desk skips posts it already has, so adding the same file again is safe.
- `data/<project>/posts.jsonl` is the full history the collector merges into.
- `data/last-run.json` says what the latest run found from each source, and why any source was skipped.

## Change what it tracks

Edit `config.json` (the pencil icon on GitHub). Saving the file starts a new collection straight away.

```json
{
  "lookback_days": 14,
  "projects": [
    {
      "slug": "the-odyssey",
      "title": "The Odyssey",
      "queries": ["\"The Odyssey\" Nolan", "\"The Odyssey\" movie"],
      "hashtags": ["TheOdyssey"],
      "since": "2026-07-01"
    }
  ]
}
```

- `slug` names the data folder: lowercase letters, digits and dashes only.
- `queries` are the searches run on YouTube, Reddit and Bluesky. Put a title in quotes and add a word that separates it from other things with the same name.
- `hashtags` are followed on Mastodon and Bluesky. Leave off the `#`.
- `since` is the earliest date to keep, as `YYYY-MM-DD`.
- `lookback_days` is how far back each daily run looks once a project has data.
- Add a second project by adding another block inside `projects`.
- To turn a source off for a project, add `"skip_sources": ["reddit"]`.

## Add the keys

Keys go in **Settings > Secrets and variables > Actions > New repository secret**. They are hidden once saved and never appear in the files or the logs.

| Secret name | Where it comes from |
|---|---|
| `YOUTUBE_API_KEY` | Google Cloud Console: create a project, enable "YouTube Data API v3", then Credentials > Create credentials > API key |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | reddit.com/prefs/apps: create an app of type "script" |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Bluesky: Settings > Privacy and security > App passwords. Use an app password, not your main password |
| `MASTODON_TOKEN` | Only if the run summary asks for it: on your Mastodon server, Preferences > Development > New application, with the `read` scope |

## Run it

- It runs by itself every day at 09:17 UTC.
- To run it now: **Actions > Collect posts > Run workflow**.
- Each run's page shows a summary table of what every source returned. A red X means a source that has a key failed; the summary says which and why.

## Limits worth knowing

- YouTube's free quota is 10,000 units a day. A search costs 100 units, so each query in `queries` uses about 100 units per run plus a few for comments. Two or three queries per project is comfortable.
- Each run fetches a bounded amount per source (a few hundred to a few thousand posts). It is a sample of the conversation, not a census.
- The collected posts include public usernames. If you would rather the data not be public, make this repository private in **Settings > General**. The daily run still fits within the free allowance for private repositories.

## Check the code

`python3 -m unittest discover -s collector` runs the tests. They use made-up responses in each API's shape, so they need no keys and no network.
