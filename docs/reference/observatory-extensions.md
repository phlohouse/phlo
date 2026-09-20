# Observatory extensions

An Observatory extension is trusted Python packaging plus browser code. Install it in the `phlo-api` environment, expose it through the `phlo.plugins.observatory` entry-point group, and restart the API so package metadata can see it.

## Manifest contract

The entry point must load an `ObservatoryExtensionPlugin` instance or a no-argument class. Its `manifest` property returns an `ObservatoryExtensionManifest` or a dictionary accepted by that Pydantic model. Its `asset_root` property returns an `importlib.resources.abc.Traversable` directory.

| Field | Type and default | Meaning |
| --- | --- | --- |
| `name` | string, required | Extension identity presented to the UI |
| `version` | string, required | Extension version |
| `compat.observatory_min` | string, required | Minimum installed `phlo-observatory` version |
| `settings.settings_schema` | JSON Schema object, required when `settings` exists | Validation schema for writes |
| `settings.defaults` | object, `{}` | Returned until a stored record exists |
| `settings.scope` | `extension` or `global`, default `extension` | Storage scope |
| `ui.routes[]` | `path`, `module`, `export`, default export `registerRoutes` | Route contribution |
| `ui.nav[]` | `title`, `to` | Navigation contribution |
| `ui.slots[]` | `slot_id`, `module`, `export`, default export `registerSlot` | Named UI slot contribution |
| `ui.settings[]` | `module`, `export`, default export `registerSettings` | Settings panel contribution |

See the exact [Pydantic models](../../src/phlo/plugins/observatory.py) and the matching [TypeScript API types](../../packages/phlo-observatory/src/phlo_observatory/src/observatory/api/extensions.ts).

## Assets and module URLs

The API advertises `/api/observatory/extensions/<plugin-metadata-name>/assets` as `assets_base_path`. Asset requests must be non-empty relative POSIX paths. Absolute paths and any `..` segment return HTTP 400; missing files return HTTP 404. The server joins the validated path to `asset_root`, extracts packaged resources when necessary, copies the selected file to a response-lifetime temporary directory, and removes that directory after the response.

Use root-relative module names such as `/example.js`. The browser prefixes non-HTTP module values with the API browser URL and the advertised asset base. An explicit `http://` or `https://` module value remains unchanged. However, the current registry only invokes modules included in Observatory's build-time `import.meta.glob`; an arbitrary remote or API-served URL resolves to an empty module and contributes nothing. This is a current implementation constraint, not a remote-module API. See the [asset endpoint](../../packages/phlo-api/src/phlo_api/observatory_api/extensions.py) and [browser registry](../../packages/phlo-observatory/src/phlo_observatory/src/extensions/registry.tsx).

## Discovery and compatibility

Discovery honours the global plugin enable switch, blacklist, and whitelist. A load error or wrong type logs a warning and skips that extension. The API caches compatible discoveries for five seconds.

If `phlo-observatory` package metadata is available, the API compares numeric version components and omits extensions whose `observatory_min` is greater than the installed version. If package metadata is unavailable, compatibility passes. This is a minimum-version check only. It does not enforce a maximum version, prerelease semantics, or a Phlo core range. Declare the package dependency range in your own `pyproject.toml` as the example does.

## Settings storage and scope

The settings API stores every extension under namespace `observatory.extension.<name>`. The manifest's `scope` chooses the `global` or `extension` partition, but both use the same `settings_store` capability. A write replaces the JSON object after JSON Schema validation. Invalid values return HTTP 422. Reads return manifest defaults only when no record exists; defaults are not copied into storage.

The default backend is the durable PostgreSQL capability supplied by `phlo-postgres`. If it cannot resolve, the API returns HTTP 503 and retries resolution on the next request. `PHLO_OBSERVATORY_SETTINGS_BACKEND=memory` selects a process-local singleton for development and tests; regulated startup rejects it. `PHLO_OBSERVATORY_SETTINGS_DB_URL` is the PostgreSQL provider's optional DSN override. See the [storage contract](../../src/phlo/plugins/observatory_settings.py) and [extension settings endpoints](../../packages/phlo-api/src/phlo_api/observatory_api/extension_settings.py).

## Trust and content security policy boundary

Installing an extension grants code execution in two places. Its Python entry point runs inside `phlo-api`, and registered browser modules run with Observatory's origin and user session. Manifests and assets are not a sandbox or an authorisation boundary. Install only packages you trust, pin and review their distributions, and put extension mutations through authenticated, authorised API endpoints.

The extension loader does not define or enforce a Content Security Policy (CSP). No extension-specific CSP allowlist exists in the current API or Observatory server. A deployment-level CSP can therefore block extension scripts, while weakening CSP to admit a remote module also expands the trust boundary. Prefer packaged, same-deployment assets and test the effective ingress/server CSP before promotion.

## Minimal package

```toml
[project]
name = "acme-observatory"
version = "1.0.0"
dependencies = ["phlo>=0.16.2,<0.17", "phlo-observatory>=0.1.0"]

[project.entry-points."phlo.plugins.observatory"]
acme = "acme_observatory.plugin:AcmeExtension"

[tool.hatch.build.targets.wheel]
include = ["src/acme_observatory/assets/*"]
packages = ["src/acme_observatory"]
```

```python
from importlib import resources

from phlo.plugins import PluginMetadata
from phlo.plugins.observatory import ObservatoryExtensionPlugin

class AcmeExtension(ObservatoryExtensionPlugin):
    metadata = PluginMetadata(name="acme", version="1.0.0")

    manifest = {
        "name": "acme",
        "version": "1.0.0",
        "compat": {"observatory_min": "0.1.0"},
        "settings": {
            "settings_schema": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "additionalProperties": False,
            },
            "defaults": {"enabled": True},
            "scope": "extension",
        },
        "ui": {
            "routes": [{
                "path": "/extensions/acme",
                "module": "/acme.js",
                "export": "registerRoutes",
            }]
        },
    }

    @property
    def asset_root(self):
        return resources.files("acme_observatory").joinpath("assets")
```

Use [`phlo-observatory-example`](../../packages/phlo-observatory-example) as the executable reference package. Its smoke test verifies the packaged `example.js` asset.
