# phlo-oauth2-proxy

OAuth2/OIDC authentication proxy for Phlo services.

Provides a forward-auth gateway using [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/).
When paired with `phlo-traefik`, protects `phlo-api` (and optionally other surfaces)
behind SSO authentication.

## Quick Start

1. Configure your IdP credentials in `.phlo/.env.local`:

   ```env
   OAUTH2_PROXY_OIDC_ISSUER_URL=https://your-idp.example.com
   OAUTH2_PROXY_CLIENT_ID=your-client-id
   OAUTH2_PROXY_CLIENT_SECRET=your-client-secret
   OAUTH2_PROXY_COOKIE_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
   ```

2. Start with the proxy profile:

   ```bash
   phlo services start --profile proxy
   ```

See `docs/guides/secure-the-stack.md` for the full operator walkthrough.
