# Social listening

This repository gathers public posts about a film or show from the free sources, once a day, and shows them on a public website: the Film Listening Desk.

**The site:** https://jmurphy328-code.github.io/Social-listening/

The site reads the collected posts straight from this repository, so it updates after every collection run. Anyone with the link can see it. Files a visitor adds on the site stay in their own browser until they close the page; nothing is uploaded.

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

- `data/<project>/posts.csv` is what the site shows. You can also download it and open it in Excel.
- `data/<project>/posts.jsonl` is the full history the collector merges into.
- `data/last-run.json` says what the latest run found from each source, and why any source was skipped.

## Add student files

Posts your students collect themselves go in `data/<project>/uploads/`. Every CSV saved there is loaded by the site for everyone, next to the automatic collection.

1. Start from `templates/student-posts-template.csv`.
2. On GitHub, open `data/the-odyssey/uploads/` and choose **Add file > Upload files**.
3. A minute or two later the site shows the new posts.

Students need to be added to the repository as collaborators (**Settings > Collaborators**) to upload, or they can send their files to the owner to upload.

### The columns

| Column | What goes in it | Needed? |
|---|---|---|
| `date` | When the post was published, as `YYYY-MM-DD` (a time can follow) | Yes, for every time chart |
| `platform` | TikTok, Instagram, X, YouTube, Reddit and so on | Yes, unless `url` is filled in |
| `author` | The account that posted | Recommended |
| `text` | The full text of the post or comment, in one cell | Yes |
| `engagement` | One number: likes, comments and shares added up | Recommended |
| `url` | The link to the post | Recommended |
| `driver` | The research area: Cast, Director, How it was made, Marketing, Social media usage | Recommended |
| `collected_by` | The student's name | Optional, not shown on the site |

- One post per row. No blank rows, totals or notes inside the data.
- `driver` is the student's own tag for the post and is kept alongside the keyword tags. Use the area names above so everyone's posts land in the same groups. For a post that fits two areas, separate them with a semicolon: `Marketing; Social media usage`.
- If a file has separate likes, comments and shares columns instead of `engagement`, leave them as they are; the site adds them up.
- A `sentiment` column (positive, neutral or negative) is used if present. Otherwise the site estimates it.
- The same post in two files is counted once, as long as its date, author and text match.

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
- `release` is optional: the release or premiere date, as `YYYY-MM-DD`. The site marks it on the volume chart.
- `drivers` are the word lists the site uses to tag each post by research area (cast, director, how it was made, marketing). A post is tagged when it contains one of the words or phrases, and can carry more than one tag. End a word with `*` to match any ending (`trailer*` matches trailer and trailers). Add, rename or remove drivers freely, up to 12. Hashtags are matched as one word, so add `mattdamon` as well as `matt damon`.
- `events` are the campaign's key moments, each as `{"date": "2026-07-06", "label": "World premiere, London"}`. The site numbers them on the volume chart and shows how the conversation changed in the week after each one. The release date is added for you.
- `voices` sorts accounts into groups. `"owned"` lists the studio's and film's official account names; an account whose name contains one of them counts as Studio and official. Add `"press"` to replace the built-in list of press and media names. Everyone else counts as Audience.
- `emotions` is optional. It replaces the built-in emotion word lists (Anticipation, Awe, Joy, Moved, Disappointment, Anger) and works like `drivers`.
- `lookback_days` is how far back each daily run looks once a project has data. A source's first run for a project goes all the way back to `since`, and so does the next run after you move `since` earlier.
- Add another film by adding another block inside `projects`. With two or more, the site shows a head-to-head panel: share of voice, both films' volume lined up by days from release, and their drivers, emotions and voices side by side.
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

## The site's file

`index.html` is the whole site in one file. It needs GitHub Pages turned on: **Settings > Pages > Deploy from a branch > main, / (root)**.

## Check the code

`python3 -m unittest discover -s collector` runs the tests. They use made-up responses in each API's shape, so they need no keys and no network.
