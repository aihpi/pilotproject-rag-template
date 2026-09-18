# Security Policy

## Status: prototype

This repository is a **research and teaching prototype**, not a hardened
product. It has not been security-audited, has no supported release line, and
carries **no security guarantee for production use**. If you deploy it beyond a
local or internal experiment, treat it as your own system: review the code,
review the dependencies, and put it behind your own authentication, network and
logging controls.

## Reporting a vulnerability

There is no dedicated security mailbox for this project. Please open a
[GitHub issue](https://github.com/aihpi/pilotproject-rag-template/issues) and
describe the problem. Be aware that issues are **public**, so for anything you
consider sensitive, open a minimal issue asking for a private contact channel and
wait for a maintainer to reply before posting details. Reports are handled on a
best-effort basis; there is no guaranteed response time.

## Before you deploy

- **Change the default credentials.** `.env.example` ships
  `CHAINLIT_AUTH_USERNAME=admin` / `CHAINLIT_AUTH_PASSWORD=admin` as a local
  development convenience. Change them for every deployment, or drop the
  fallback login entirely and use OAuth.
- **Set `CHAINLIT_AUTH_SECRET`** to a long random value, unique per environment.
  The placeholder in `.env.example` is not a secret.
- **Keep API keys in `.env` only.** `LITELLM_API_KEY`, `QDRANT_API_KEY`,
  `OAUTH_GITHUB_CLIENT_SECRET` and friends belong in the gitignored `.env`, never
  in a YAML config, a commit or a docs example. Rotate anything that has been
  committed by mistake.
- **Do not expose Qdrant or Postgres.** The bundled `docker-compose.yml`
  publishes their ports for convenience on a developer machine.
- **Mind your corpus.** Everything you ingest becomes retrievable by any user who
  can log in, and chunks are sent to whichever model gateway you configure.
  Profiles and retrieval filters are a convenience, not an access-control layer.
