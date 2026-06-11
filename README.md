# Multifamily & Development Briefings

An auto-updating, TCR-branded news dashboard. A scheduled job pulls real-estate
feeds, has **Claude (Haiku 4.5)** write key points + a short summary for each
story, and publishes everything to a single web page your whole team opens in a
browser. No installs for the team, one shared API key, runs itself.

```
index.html      → the dashboard (reads data.json)
data.json       → the generated briefing (replaced automatically each run)
fetch_news.py   → pulls feeds + rates, writes summaries with Claude
requirements.txt
.github/workflows/brief.yml → runs fetch_news.py on a schedule, commits data.json
```

---

## What you need to sign up for

1. **GitHub account** — free. <https://github.com> (hosts the page + runs the job).
2. **Anthropic API key with credit** — <https://platform.claude.com>.
   Create a key, add a little credit (~$20 lasts a long time), and **copy the key
   immediately** — you can't view it again later.

That's it. No server, no other services.

---

## Setup (about 10 minutes, one time)

1. **Create a new repository** on GitHub. A **public** repo is simplest (free
   Pages + free Actions). See the privacy note at the bottom.

2. **Upload these files** into the repo, keeping the folder structure — the
   workflow must stay at `.github/workflows/brief.yml`. (On github.com: "Add file
   → Upload files", then drag everything in. The `.github` folder uploads fine.)

3. **Add your API key as a secret.** Repo **Settings → Secrets and variables →
   Actions → New repository secret**:
   - Name: `ANTHROPIC_API_KEY`
   - Value: your `sk-ant-...` key
   The key lives here encrypted — never in the code — so a public repo is safe.

4. **Turn on Pages.** Repo **Settings → Pages → Build and deployment → Source:
   "Deploy from a branch" → Branch: `main` / `(root)` → Save.** After a minute
   GitHub shows your URL, like `https://yourname.github.io/your-repo/`.

5. **Run it once.** **Actions tab → Briefings → Run workflow.** It fetches,
   summarizes, and commits a real `data.json`. Refresh your Pages URL — the
   briefing appears.

6. **Share the Pages URL** with the team. Suggest they bookmark it or set it as
   their browser homepage. Done.

---

## Cost

Uses **Claude Haiku 4.5** at $1 / $5 per million input / output tokens.
A ~30-story briefing costs only a few cents per run. Running hourly on weekdays
(the default schedule) lands around **$6–15/month**. To spend less, run it less
often — edit the `cron` line in `.github/workflows/brief.yml`:

```yaml
# every hour, weekdays, business hours (default)
- cron: "0 12-23 * * 1-5"
# example: every 3 hours, weekdays
- cron: "0 12,15,18,21 * * 1-5"
```

Set a monthly spend limit in the Anthropic console for peace of mind.

---

## Customizing

- **Sources:** edit the `FEEDS` list at the top of `fetch_news.py`. Each entry is
  `(feed_url, "Name", "Category", max_items)`. Most sites' feeds are their URL
  + `/feed/`. If you add a source, also give it a color in `SOURCE_COLORS` near
  the top of the `<script>` in `index.html` (optional — defaults to navy).
- **Brand colors:** the `BRAND PALETTE` block at the top of `index.html` already
  holds TCR's navy (`#00263D`) and brass (`#C6964B`). Swap in exact brand-guide
  values if needed.
- **Summary style:** the prompt inside `summarize()` in `fetch_news.py` controls
  tone, bullet count, and length — tweak it to taste.
- **Rates shown:** `YAHOO_TICKERS` in `fetch_news.py` (plus SOFR from the NY Fed).

---

## Privacy note

With a **public** repo, the dashboard and `data.json` are reachable by anyone who
has the URL (the URL isn't advertised, but it isn't secret either). The content is
public real-estate news, so that's usually fine. Your **API key is never exposed** —
it's stored as an encrypted GitHub secret, not in any file.

If you need it private, GitHub Pages on a private repo requires a paid GitHub plan
(Pro/Team), or you can host the two files (`index.html` + `data.json`) somewhere
internal and keep only the scheduled job on GitHub.
