# GPU Deal Bot

Watches r/buildapcsales and Slickdeals every 5 minutes and pings your phone
when a card in `deals_config.json` is posted at or below your target price.

## Setup (about 10 minutes, once)

1. **Get the alert app.** Install **ntfy** (free, iOS/Android). Tap +, subscribe to a
   topic with a long random name, e.g. `gpu-deals-k7q2m9xv4`. Anyone who knows the
   name can see your alerts, so don't use something guessable.
2. **Make a GitHub repo.** Create a free account at github.com, then a new repository
   and set it to **Public** (nothing private is stored in the files; your ntfy topic is kept as a secret).
3. **Upload the files.** "Add file → Upload files" and add `deal_bot.py`,
   `deals_config.json` and `README.md`. Then "Add file → Create new file", name it
   `.github/workflows/deal-bot.yml`, and paste in the workflow file's contents.
4. **Add your topic as a secret.** Settings → Secrets and variables → Actions →
   New repository secret. Name: `NTFY_TOPIC`, value: your topic name.
5. **Test it.** Actions tab → "GPU deal bot" → Run workflow. The first run may send
   alerts for any current deals under your targets.

Optional: add a `DISCORD_WEBHOOK` secret too if you'd rather get alerts in Discord.

## Changing what it looks for

Edit `deals_config.json` on GitHub. Change any `target_price`, or delete a card's line
to stop watching it. Changes take effect on the next run.

## Notes

- Keep the repository **public**. Actions are free for public repos; a private repo
  would run out of free minutes at a 5-minute schedule.
- GitHub's scheduled runs can be delayed by a few minutes at busy times.
- If a feed blocks GitHub's servers (Reddit sometimes does), the run logs a warning
  and keeps checking the other feeds. Check the Actions log if you never get alerts.
- The bot reads prices from post titles, so always confirm the price on the
  retailer's page before buying. It skips laptops, prebuilts, and used/refurb listings.
