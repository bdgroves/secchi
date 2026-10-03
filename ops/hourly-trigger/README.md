# Hourly trigger (Cloudflare Worker)

GitHub's scheduler runs `fetch.yml` every 4-8 hours instead of hourly.
This Worker starts `fetch.yml` at :41 and `pages.yml` at :55, every hour,
on time. GitHub's own schedules stay as a backstop.

## Switching it on (about ten minutes, once)

**1. Make a token on GitHub** that can only start workflows on this repo.
GitHub → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** → Generate new token:

- Name: `secchi-hourly-trigger`; expiration: 1 year (put a reminder in
  your calendar to renew it)
- Repository access: **Only select repositories** → `bdgroves/secchi`
- Permissions → Repository permissions → **Actions: Read and write**.
  Nothing else.

Copy the token (it's shown once).

**2. Deploy the Worker** from this folder, in PowerShell (needs Node):

```powershell
cd C:\data\01_Projects\secchi\ops\hourly-trigger
npx wrangler login
npx wrangler secret put GITHUB_TOKEN     # paste the token when asked
npx wrangler deploy
```

`wrangler secret put` stores the token in Cloudflare, encrypted; it never
goes in the repo.

**3. Check it.** After the next :41, the repo's Actions tab shows a `fetch`
run with event `workflow_dispatch`, and `pixi run status` shows the count
of snapshots in the last 24 hours climbing toward 24. Cloudflare's dashboard
(Workers → secchi-hourly-trigger → Logs) shows each run; a failed dispatch
appears as an error with GitHub's answer.

## When the token expires

Dispatches start failing with 401 in the Worker's logs, `status` shows the
snapshot count falling back to ~4, and GitHub's schedules carry on as
before. Make a new token (step 1) and run `npx wrangler secret put
GITHUB_TOKEN` again. No redeploy needed.

## Turning it off

`npx wrangler delete` in this folder, or delete the token on GitHub.
