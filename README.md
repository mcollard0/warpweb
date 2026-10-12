# Warp mobile client
A password-gated, mobile-friendly web client for browsing Warp agent runs, reading conversation cards, and creating or continuing local Oz CLI sessions.

FastAPI fetches history and metadata through Warp REST using a server-only API key. Local tasks run through the Warp CLI with private prompt files; desktop runs remain read-only.

Both task inputs support **Ctrl+Enter** to send; Enter adds a newline. Submitted tasks appear immediately as User cards. Tool commands/inputs stay visible while output is expandable, and prose renders sanitized Markdown. Send buttons use rocket icons.

The **Options** gear saves settings in this browser: recent directories (default 20), history window (8 KiB), conversation-card preview (12 lines), and cost refresh every N accepted prompts (default 1). Directories are remembered only after accepted submissions; options can be reset and recent directories cleared. Cost is per run, not an account balance or conversation total.

Finished local CLI conversations can be continued even when created outside warpweb. Active CLI runs must finish first. When Warp supplies a shared-session link, the client offers it for desktop/active-session access; steering there requires Warp edit access. No public active-run messaging endpoint is used.


Start with **[INSTALL.md](INSTALL.md)** for the Personal-token setup, interactive `INSTALL.sh`, private credential storage, user systemd service, and optional HTTPS hosting. Run `./INSTALL.sh --check` for a non-destructive prerequisite check.

See [architecture.md](architecture.md) for API details and limitations. For development, install server dependencies with `.venv/bin/python -m pip install -r requirements.txt` and test dependencies with `.venv/bin/python -m pip install -r tests/requirements.txt`. Run `.venv/bin/python -m pytest -q tests`; mocked browser tests use Playwright and an installed `chromium` executable without contacting Warp or running agents.

The October 12 UX changes are deployed: the approved service restart completed on October 12, 2026, at approximately 13:32 UTC, with no active warpweb CLI jobs detected beforehand. Public assets/session health, authentication protection on the new endpoints, HTTPS redirect, and security headers passed smoke checks. Log in again after refreshing the page; authenticated live interaction still requires your password. Compatibility fallbacks remain available for future rolling asset/backend updates.
