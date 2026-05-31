# Keeping your GitHub account secure

Basic, widely-recommended account hygiene that applies to everyone.

## Two-factor authentication (2FA)

Turn it on. GitHub now **requires 2FA** for accounts that contribute code.
Prefer an authenticator app or a hardware security key (like a YubiKey)
over SMS, which is the weakest second factor.

## Authenticating with repositories

- **Password authentication for Git operations is not supported.** Use a
  **personal access token (PAT)** or **SSH keys** instead.
- **SSH keys** — generate a key pair, add the public key to your GitHub
  account, and you can push/pull without typing a token.
- **Personal access tokens** — prefer *fine-grained* tokens scoped to the
  specific repositories and permissions you need, with an expiry date.

## Good habits

- Never commit secrets (API keys, passwords) into a repository. If you do,
  consider them compromised — revoke and rotate immediately.
- Review which **OAuth apps** and **PATs** have access to your account
  periodically and remove ones you don't use.
- Enable **secret scanning** so accidentally committed credentials are
  caught automatically (Advanced Security / public repos).

## Signing your commits

You can cryptographically **sign commits** with GPG, SSH, or S/MIME so
GitHub shows a "Verified" badge, proving the commit really came from you.
