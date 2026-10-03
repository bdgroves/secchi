// secchi hourly trigger: a Cloudflare Worker whose only job is to start
// two GitHub workflows on time.
//
// Why: GitHub runs scheduled workflows late and drops some. In late
// September and early October 2026, fetch.yml's "41 * * * *" schedule
// landed every 4-8 hours, about four runs a day. Cloudflare's cron
// triggers fire on the minute. GitHub's own schedules stay in place as a
// backstop; fetch.yml queues rather than overlaps (concurrency group).
//
// Needs one secret, GITHUB_TOKEN: a fine-grained personal access token
// limited to bdgroves/secchi with "Actions: read and write" and nothing
// else. It can start and cancel workflow runs on that one repo; it cannot
// push code or read anything private. See README.md.

const REPO = "bdgroves/secchi";
const BY_CRON = {
  "41 * * * *": "fetch.yml",   // ingest and commit the hourly snapshot
  "55 * * * *": "pages.yml",   // publish the page from it
};

async function dispatch(workflow, env) {
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "secchi-hourly-trigger",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  );
  // 204 is success. Anything else is thrown, so it shows as a failed
  // invocation in the Worker's logs instead of passing silently.
  if (resp.status !== 204) {
    throw new Error(`${workflow}: GitHub answered ${resp.status} ${await resp.text()}`);
  }
  return `${workflow} dispatched`;
}

export default {
  async scheduled(event, env, ctx) {
    const workflow = BY_CRON[event.cron];
    if (!workflow) throw new Error(`no workflow for cron "${event.cron}"`);
    ctx.waitUntil(dispatch(workflow, env).then(console.log));
  },

  // Visiting the Worker's URL only says what it is. It never dispatches:
  // a public URL that starts workflows would be an open trigger.
  async fetch() {
    return new Response(
      "secchi hourly trigger: starts fetch.yml at :41 and pages.yml at :55.\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  },
};
