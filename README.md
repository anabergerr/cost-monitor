# Cost Monitor

Daily pick of **which AI models to use for code**, based on [Artificial Analysis](https://artificialanalysis.ai) scores — intelligence, engineering, and cost per task. Not price per million tokens.

Live site: [anabergerr.github.io/cost-monitor](https://anabergerr.github.io/cost-monitor/)

A GitHub Action pulls the AA API, ranks models, updates the page, and sends the same recap to Telegram.

## What you get

Three coding lanes, from the **green quadrant** (smart enough and cheap enough, same idea as the AA chart):

- **Complex** — architecture, hard bugs, big refactors
- **Medium** — features, reviews, everyday PRs
- **Simple** — scripts, boilerplate, small fixes

Plus:

- models still in the green box
- **expensive but worth it** — top 3 on the Pareto frontier to the right (you pay more, you actually get more intelligence)
- **expensive, skip** — above the cost cut and *not* on the frontier (a cheaper model is at least as smart)

## How the cut is made

The script does not invent scores. It reads AA’s Intelligence Index, Engineering Index, and cost per intelligence-index task.

Green is the top half of the intelligence axis and the left half of the log cost axis. Picks come from the Pareto frontier inside that box. Weak models are ignored when setting the axes so they do not drag the cost cut down.

## Run it yourself

Fork, then add these repository secrets:

| Secret | What it is |
| --- | --- |
| `ARTIFICIAL_ANALYSIS_API_KEY` | [Artificial Analysis](https://artificialanalysis.ai) API key |
| `TELEGRAM_BOT_TOKEN` | Bot token from [BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Chat that should receive the message |

Then:

1. **Actions → Monitor Diario de IA → Run workflow** (this repo does not use GitHub’s `schedule`; trigger it by hand or with an external cron that calls `workflow_dispatch`)
2. **Settings → Pages → Deploy from a branch → `main` / `/docs`**

Local:

```bash
python check_models.py --check   # no API, no Telegram
python check_models.py           # needs the env vars above
```

`check_models.py` writes `docs/index.html`. The workflow commits that file back to `main`.

---

feito com amor por Ana Laura e Cursor ♥
