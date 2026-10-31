# famitsu-rss

Unofficial RSS feeds for [ファミ通.com](https://www.famitsu.com/), which no longer publishes its own.
A GitHub Actions job runs `famitsu_rss.py` about once an hour and publishes the feeds on GitHub Pages.
The feeds include each article's full text.

## Feeds

Open the Pages site (`https://<user>.github.io/<repo>/`) for a list of feed URLs to copy.
Each feed is at `https://<user>.github.io/<repo>/<category>.xml`:

| Category | File |
| --- | --- |
| 新着 (everything) | `new-article.xml` |
| ニュース | `news.xml` |
| Switch | `switch.xml` |
| PS5 / PS4 | `ps5.xml`, `ps4.xml` |
| PCゲーム | `pc-game.xml` |
| 動画 | `videos.xml` |
| 特集・企画記事 | `special-article.xml` |
| インタビュー | `interview.xml` |
| 取材・リポート | `event-report.xml` |
| レビュー | `review.xml` |
| インディーゲーム | `indie-game.xml` |

## Setup

1. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
2. Actions → *Update Famitsu feeds* → **Run workflow** (or push to `main`).

After that, the job runs hourly on its own. It adds an empty commit once a month,
because GitHub turns off scheduled workflows in repositories with no activity for 60 days.

## If the feeds stop updating

Famitsu can change its site at any time. GitHub emails you when a scheduled run fails;
the run log says which request broke. The data comes from the same endpoints as
[RSSHub's famitsu route](https://github.com/DIYgod/RSSHub/tree/master/lib/routes/famitsu),
so a fix there usually shows what changed.
