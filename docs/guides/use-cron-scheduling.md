# Use cron scheduling

Schedule recurring tasks that Hermes runs automatically and delivers to a messaging platform.

Cron jobs are useful for:
- Daily digests (news summary, weather, calendar review)
- Nightly maintenance (backups, log cleanup, dependency updates)
- Periodic monitoring (server health checks, uptime alerts)
- Weekly reports (project status, expense summaries)

## Prerequisites

- Hermes gateway running (`hermes gateway start`)
- At least one messaging platform configured
- Home channel set (`/sethome` in a conversation, or `TELEGRAM_HOME_CHANNEL` in `.env`)

## Create a cron job

From inside any conversation:

```
you: schedule a daily summary of my GitHub notifications at 9am
hermes: Creating a cron job: daily at 9:00 AM — summarize GitHub notifications and send to this channel.
        Job ID: cron_abc123
```

Or use the `create_cron_job` tool directly:

```
you: /cron create "0 9 * * *" "Check GitHub notifications and give me a morning summary"
```

From the CLI:

```bash
hermes cron create "0 9 * * *" "Summarize GitHub notifications"
hermes cron create "0 0 * * *" "Run nightly backup of the database and report status"
hermes cron create "0 8 * * 1" "Give me a Monday morning weekly planning brief"
```

## Cron syntax

Standard 5-field cron syntax:

```
┌─── minute (0–59)
│  ┌─── hour (0–23)
│  │  ┌─── day of month (1–31)
│  │  │  ┌─── month (1–12)
│  │  │  │  ┌─── day of week (0–7, 0 and 7 = Sunday)
│  │  │  │  │
*  *  *  *  *
```

Common patterns:

| Expression | Meaning |
|------------|---------|
| `0 9 * * *` | Daily at 9:00 AM |
| `0 0 * * *` | Daily at midnight |
| `0 8 * * 1` | Every Monday at 8:00 AM |
| `0 9 1 * *` | 1st of every month at 9:00 AM |
| `*/30 * * * *` | Every 30 minutes |
| `0 9,18 * * *` | At 9:00 AM and 6:00 PM daily |

## List cron jobs

```bash
hermes cron list
```

Or from a conversation:

```
/cron list
```

Output example:

```
ID            Schedule      Description
cron_abc123   0 9 * * *     Summarize GitHub notifications
cron_def456   0 0 * * *     Nightly database backup
cron_ghi789   0 8 * * 1     Monday morning planning brief
```

## Delete a cron job

```bash
hermes cron delete cron_abc123
```

Or from a conversation:

```
/cron delete cron_abc123
```

## Delivery channels

By default, cron job output is delivered to the home channel. Override per-job:

```bash
hermes cron create "0 9 * * *" "Daily digest" --channel telegram:123456789
hermes cron create "0 9 * * *" "Daily digest" --channel slack:#engineering
```

## How cron jobs execute

1. The scheduler in `cron/scheduler.py` triggers when a job's cron expression matches the current time.
2. A new `AIAgent` instance is created for the job (isolated from any active conversation).
3. The job prompt is the description you provided — the agent executes it as a task.
4. The result is delivered via `cron/delivery.py` to the configured channel.
5. The job's execution history is logged.

## Cron job prompts

Write the description as a task instruction for the agent:

```bash
# Good — clear task with expected output
hermes cron create "0 9 * * *" \
  "Check my GitHub notifications. Summarize any open PRs needing my review, issues mentioning me, and unread notifications. Keep it concise — 3-5 bullet points max."

# Less good — vague
hermes cron create "0 9 * * *" "do github stuff"
```

## Verification

1. Create a test job that runs in 2 minutes (adjust the time):

```bash
hermes cron create "42 14 * * *" "Say 'cron test successful' and list today's date"
```

2. Wait for it to trigger.
3. Check the home channel for the delivered message.
4. Delete the test job.

## Troubleshooting

**Job not triggering**
- Verify the gateway is running: `hermes gateway status`
- Check the cron expression is valid: `hermes cron validate "0 9 * * *"`
- Verify a home channel is set: `/status` in a conversation

**Job triggers but no message delivered**
- Check that the home channel is reachable (bot has permission to post there)
- Look at gateway logs: `hermes logs`

**Job triggers but output is wrong**
- Review the job prompt — be more specific about what format you want
- Add "respond in English" or specific format requirements to the description
