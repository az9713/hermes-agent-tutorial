# Add a skill

Create a new skill that gives Hermes procedural knowledge for a specific domain.

## Prerequisites

- Hermes installed and running

## 1. Create the skill directory

Skills live in `~/.hermes/skills/` (user-created skills) or `skills/` (bundled, for the repo). Create a new directory:

```bash
mkdir -p ~/.hermes/skills/my-nginx-skill
```

## 2. Write SKILL.md

`SKILL.md` is the only required file. Write it as instructions to Hermes — assume it will be read by the LLM, not a human.

```markdown
# nginx skill

You are equipped to work with nginx configuration files.

## Configuration file locations

- Main config: `/etc/nginx/nginx.conf`
- Site configs: `/etc/nginx/sites-available/` (enable via symlink to `sites-enabled/`)
- Logs: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`

## Common tasks

### Test configuration before applying

Always test before reloading:
```bash
sudo nginx -t
```

### Reload without downtime

```bash
sudo systemctl reload nginx
```

### Enable a site

```bash
sudo ln -s /etc/nginx/sites-available/mysite /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Reverse proxy template

When setting up a reverse proxy, use this template:

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

## Debugging 502 errors

1. Check the upstream service is running: `systemctl status <service>`
2. Verify the proxy_pass port matches what the app is listening on
3. Check nginx error log: `sudo tail -f /var/log/nginx/error.log`
4. Look for `Connection refused` — upstream is down
5. Look for `upstream timed out` — upstream is slow or blocking
```

### Slash command definition

Skills can define slash commands. Add them with this pattern:

```markdown
## /nginx-status

Shows nginx status, recent errors, and enabled sites.

Usage: /nginx-status
```

When the user types `/nginx-status`, this command is invoked as a user message, which triggers the agent to execute the described behavior.

## 3. Verify the skill loads

Restart Hermes and check:

```bash
hermes skills
```

The new skill should appear in the list with status `active`.

## 4. Test the skill

Start a conversation and ask about nginx:

```
you: set up a reverse proxy for my Node.js app on port 3000
hermes: [uses the nginx skill's template and step-by-step instructions]
```

## Platform filtering (optional)

If the skill only works on specific platforms, add a comment to `SKILL.md`:

```markdown
<!-- HERMES_SKILL_PLATFORMS: macos,linux -->
```

Valid values: `macos`, `linux`, `windows`. Omit entirely for all platforms.

If the skill requires specific tools:

```markdown
<!-- HERMES_SKILL_REQUIRES: run_command -->
```

## Adding helper scripts

Scripts in the skill directory are accessible to the agent:

```bash
~/.hermes/skills/my-nginx-skill/
├── SKILL.md
└── check-nginx.sh
```

In `SKILL.md`, reference the script by path so the agent knows it exists:

```markdown
## Quick diagnostic

Run the diagnostic script at `~/.hermes/skills/my-nginx-skill/check-nginx.sh` for a full health check.
```

## Installing a skill to the repo (for contribution)

To contribute a skill to the bundled set:

1. Add the skill directory under `skills/<category>/my-skill/`
2. Follow the existing skill structure in nearby examples
3. Add an `INSTALL.md` if the skill requires setup steps
4. Submit a PR — see [CONTRIBUTING.md](../../CONTRIBUTING.md)

## Troubleshooting

**Skill not appearing in `hermes skills`**
- Check the directory contains a `SKILL.md` file
- Verify the path is under `~/.hermes/skills/` or the repo's `skills/`
- Platform filters may be excluding it — check for `HERMES_SKILL_PLATFORMS` comments

**Skill loaded but agent ignores it**
- Check the `SKILL.md` content is specific enough — vague instructions are easy to ignore
- Add concrete examples showing exactly what to do
- Use imperative language: "Always run nginx -t before reloading" not "You might want to test first"
