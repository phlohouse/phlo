# phlo-oauth2-proxy

OAuth2/OIDC authentication proxy for Phlo services.

Provides a forward-auth gateway using [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/).
When paired with `phlo-traefik`, protects `phlo-api` (and optionally other surfaces)
behind SSO authentication.

## Quick Start

1. Set the issuer and client ID in `phlo.yaml` under `env:`:

   ```yaml
   env:
     OAUTH2_PROXY_OIDC_ISSUER_URL: https://your-idp.example.com
     OAUTH2_PROXY_CLIENT_ID: your-client-id
   ```

   `phlo services init` writes a client secret placeholder and a generated
   cookie key to `.phlo/secrets/.env`. Replace the placeholder with your
   identity provider's client secret:

   ```env
   OAUTH2_PROXY_CLIENT_SECRET=your-client-secret
   ```

   Regenerating the stack keeps both values.

2. Start with the proxy profile:

   ```bash
   phlo services start --profile proxy
   ```

See `docs/guides/secure-the-stack.md` for the full operator walkthrough.
