# Installing Warpweb
Warpweb is a password-gated browser client for Warp agent runs. It runs on your Linux machine, uses your Personal Warp token on the server, and can launch local CLI agents under your user account. It is not a multi-user sandbox.
## Prerequisites
- Linux with a working **systemd user session** and coreutils (`/usr/bin/env`). Run installation as your normal logged-in user, not with `sudo`.
- Python 3.10 or newer, including `venv` and `pip` support. Some distributions package these separately.
- Git, a writable checkout, and network access to download the Python dependencies.
- The Warp CLI with `warp agent run`, `--conversation`, and `ndjson` support. Install it from [Warp's official CLI documentation](https://docs.warp.dev/reference/cli). If discovery selects the wrong executable, pass `--cli /absolute/path/to/warp`.
- A Warp account with access to the runs you want to browse and credits for tasks you submit.
- For access from other devices: your own HTTPS hostname and a TLS reverse proxy or Cloudflare Tunnel. The installer does not provision DNS, TLS certificates, tunnels, or firewall rules.
Clone/download the repository and enter its directory. Keep this checkout in a stable location: the service's working directory and executable paths point to it.
## Generate a Personal token
1. Sign in at **https://oz.warp.dev/settings**.
2. Click **Generate New Token**.
3. Choose **Personal**.
4. Give the token a descriptive name, select an appropriate expiration, and generate it.
5. Copy it privately; you will paste it into the installer's hidden prompt. Warp tokens normally start with `wk-`.
UI labels can vary by release; the [API-key documentation](https://docs.warp.dev/reference/cli/api-keys) also describes the equivalent Create API Key flow. Use a Personal token for this personal-run viewer rather than a Team token.
Do not paste the token into a command, source file, issue, commit, or chat. The installer never asks for your Warp-account password. Its separate web password is only for logging in to Warpweb.
## Check and install
Inspect `INSTALL.sh` and `tools/install.py`, then check prerequisites without changing anything:
```bash
./INSTALL.sh --check
```
Install the default **local-only** service:
```bash
./INSTALL.sh
```
The script explains four stages and asks for confirmation:
1. Create/reuse `.venv` and install `requirements.txt`.
2. Prompt for the Personal token without echo, then run the password hasher and ask twice for a nonempty web password.
3. Write private credentials and generate a user systemd unit with paths for your actual checkout.
4. Ask before enabling and starting/restarting the service; run a local health check.
The token, password, and verifier are never printed by the installer or put in process arguments. The verifier uses salted PBKDF2-HMAC-SHA3-256; the web password itself is not stored. Token validation checks format only, not remote account access.
Default browser URL: **http://127.0.0.1:8797**. Use that exact origin, not an alternative hostname, because login and other POSTs validate Origin. Local-only HTTP disables Secure cookies and HTTPS enforcement for this loopback origin only. The listener always remains bound to `127.0.0.1`.
By default, selectable working directories must be inside this checkout. Allow other existing workspace roots explicitly:
```bash
./INSTALL.sh --workdir-root "$HOME/Programming" --workdir-root "$HOME/Projects"
```
Repeat `--workdir-root` as needed. This validates the requested cwd; it does **not** sandbox the agent's filesystem or command permissions. Treat access to the web client as access to an agent running under your Linux account.
Optional controls:
```bash
./INSTALL.sh --help
./INSTALL.sh --port 8800
./INSTALL.sh --no-start
```
`--no-start` installs files and reloads systemd's unit definitions but does not enable, start, or restart the service.
## Private files and persistence
- `~/.config/warpweb/warpweb.env`: mode **0600**, inside a mode-0700 directory. Contains `WARP_API_KEY`, `WARP_API_HASH`, browser origin/HTTPS/cookie settings, workspace roots, and CLI path.
- `~/.config/systemd/user/warpweb.service`: generated user unit referencing the environment file. The unit contains no token or password verifier.
- `~/.config/warpweb/backups/`: private dated backups when replacing existing configuration; retain at most 25 copies per file. These backups contain sensitive old tokens/verifiers; protect them and revoke old tokens after rotation.
- `~/.local/state/warpweb/jobs/`: private task files used while CLI jobs execute; removed on normal completion.
Both credentials are persisted through `EnvironmentFile`, so rebooting does not require reimporting a password hash into the systemd manager. Do not `source` the environment file into a shell or print it. Use your editor locally when necessary, and keep mode 0600.
If an environment file or installed unit already exists, the installer requires the exact word **REPLACE** before proceeding. Reinstallation requests fresh credentials and replaces settings with the options you supply; repeat `--origin`, `--port`, `--workdir-root`, and `--cli` as appropriate. It does not silently preserve old settings. A later start/restart confirmation is separate.
## HTTPS access from another device
Choose your own origin, for example:
```bash
./INSTALL.sh --origin https://agents.example.com --workdir-root "$HOME/Programming"
```
HTTPS mode installs:
- `WARPWEB_ALLOWED_ORIGINS=https://agents.example.com`
- `WARPWEB_PUBLIC_URL=https://agents.example.com`
- `WARPWEB_PUBLIC_HTTPS=1`
- `WARPWEB_COOKIE_SECURE=1`
Origins must not contain credentials, an application path, query, or fragment. Public origins must use HTTPS; invalid HTTPS redirect configuration fails closed. A trailing `/` is normalized away.
Configure your HTTPS proxy/tunnel to forward to **http://127.0.0.1:8797** (or your chosen `--port`). The local listener does not use TLS; the proxy handles it. Only loopback proxy addresses are trusted for forwarded headers. For an Nginx proxy, explicitly set `Host`, `X-Real-IP`, `X-Forwarded-Proto`, and `X-Forwarded-For` as in `deploy/nginx-warpweb.conf.example`; allow requests up to 256 KiB for JSON-encoded Markdown rendering. For Cloudflare Tunnel, add the ingress rule in `deploy/cloudflared-config.yml.example` to your existing config before its catch-all; retain its existing tunnel identity and other rules.
The examples are references, not an automatic proxy installation. Replace example hostnames and certificate paths. Do not publish tunnel credential JSON files or your complete personal ingress configuration.
The service may start before your proxy is ready; the installer checks the local backend, not public DNS/TLS or authenticated Warp calls.
## Manage the service
```bash
systemctl --user --no-pager status warpweb.service
systemctl --user enable --now warpweb.service
systemctl --user restart warpweb.service
systemctl --user stop warpweb.service
journalctl --user -u warpweb.service --no-pager -n 100
```
**Restarting clears browser logins and in-memory job records and terminates service-owned CLI jobs.** Check that nothing important is running before restarting. `enable --now` starts an inactive service but does not restart an already active one; use `restart` after changing credentials or settings.
User services ordinarily depend on your login session. If the service must run without an interactive login, arrange lingering with your administrator:
```bash
loginctl enable-linger "$USER"
```
This may require authorization and is not run by the installer.
## Rotate credentials
To rotate the Personal token and web password together, generate a new Personal token and rerun `INSTALL.sh` with the original installation options. Confirm **REPLACE**, then start/restart only when safe. Existing configuration is backed up privately. Revoke the previous Warp token afterward.
To change only the web password from the checkout without displaying its verifier:
```bash
.venv/bin/python -m tools.hash_password --env-file "$HOME/.config/warpweb/warpweb.env"
systemctl --user restart warpweb.service
```
This preserves other environment-file entries and atomically replaces `WARP_API_HASH`. It does not create an additional backup; back up the private file securely first if you need to retain it. Never remove the `--env-file` option unless you intentionally want the legacy hasher to print its verifier.
## Verify and troubleshoot
Refresh the browser and log in with your web password. Confirm that sessions load, Options works, and a finished `CLI / LOCAL` session permits continuation. Active/desktop runs remain read-only inside Warpweb; available shared-session links may support steering with Warp edit access.
- Login disabled: check that the environment file contains both required keys and that you restarted after changes. Do not share their values.
- Warp credentials lack access: check token expiration, Personal scope, and account permissions.
- Invalid Origin: open the exact configured URL, and check proxy/origin settings.
- Directory rejected: use an existing directory under one of the configured workspace roots.
- Browser reaches HTTP redirects or cannot keep a login: ensure HTTPS proxy headers and Secure-cookie settings match your selected mode.
- Cannot start the service: inspect user-service status and journal, check the listener port and generated checkout/CLI paths.
- Checkout moved: rerun installation from its new location with the intended options.
Development checks:
```bash
.venv/bin/python -m pip install -r tests/requirements.txt
.venv/bin/python -m pytest -q tests
```
Browser regressions require system Chromium. Tests use mocked API responses and do not require your token or password.
## Public repository hygiene
`.gitignore` excludes virtual environments, Python/native build artifacts, caches, logs, runtime state, backup folders, credential/environment files, private keys, installed personal deployment snapshots, and the current local screenshot captures. Generic `.example` deployment files and other assets remain trackable.
Ignore rules are not a secret scanner and do not remove already-tracked secrets. Review `git diff --cached` before committing and use a credential scanner before publishing. If a credential was committed or otherwise exposed, revoke/rotate it; deleting it in a later commit is insufficient.
