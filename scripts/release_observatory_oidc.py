"""Disposable real OIDC login against the installed Observatory and API.

Keycloak is test infrastructure, not a release artifact. The proxy image comes
from the qualified BOM. Passwords, realm imports and raw logs remain private.
No bearer header, forged identity, TLS bypass or source mount signs the user in.
"""

from __future__ import annotations

import base64
import json
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from contextlib import suppress
from pathlib import Path

import release_golden_path as golden
import release_observatory_acceptance as observatory

KEYCLOAK = "quay.io/keycloak/keycloak:26.3.3@sha256:6a7217a100bd3e5de4063a27a538ef999a3c5a88c4b4ec0ffc0a642aee7b2597"
VIEWER = "0213f1f0-6757-4ce9-947d-7184c22bb900"


def port() -> int:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def prepare(
    config: golden.RunConfig, binding: dict, proxy_image: str
) -> tuple[Path, str, str, str]:
    """Configure only the owned acceptance project and its isolated login fixture."""
    root = Path(tempfile.mkdtemp(prefix="acceptance-oidc-", dir=config.project_dir / ".phlo"))
    public = root / "public"
    public.mkdir(mode=0o755)
    password, client_secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    issuer_port, proxy_port = port(), port()
    issuer = f"https://idp.phlo.localhost:{issuer_port}/realms/acceptance"
    base = f"https://observatory.phlo.localhost:{proxy_port}"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(root / "tls.key"),
            "-out",
            str(public / "ca.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=Phlo disposable acceptance",
            "-addext",
            "subjectAltName=DNS:idp.phlo.localhost,DNS:observatory.phlo.localhost,IP:127.0.0.1",
        ],
        capture_output=True,
        check=True,
    )
    (root / "tls.key").chmod(0o600)
    (public / "ca.pem").chmod(0o644)
    realm = {
        "realm": "acceptance",
        "enabled": True,
        "sslRequired": "all",
        "registrationAllowed": False,
        "clients": [
            {
                "clientId": "phlo-acceptance",
                "secret": client_secret,
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "redirectUris": [base + "/oauth2/callback"],
                "protocolMappers": [
                    {
                        "name": "audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "phlo-acceptance",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                        },
                    },
                    {
                        "name": "canonical-viewer-group",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-group-membership-mapper",
                        "config": {
                            "claim.name": "groups",
                            "full.path": "false",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                        },
                    },
                ],
            }
        ],
        "groups": [{"name": "viewers"}],
        "users": [
            {
                "id": VIEWER,
                "username": "viewer@example.test",
                "enabled": True,
                "email": "viewer@example.test",
                "emailVerified": True,
                "firstName": "Acceptance",
                "lastName": "Viewer",
                "groups": ["/viewers"],
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            }
        ],
    }
    observatory.private_file(root / "import/realm.json", json.dumps(realm))
    observatory.private_file(root / "login.json", json.dumps({"password": password, "base": base}))
    document = golden.compose_config_json(config)
    network = next(iter(document["networks"].values()))["name"]
    api_image = document["services"]["phlo-api"]["image"]
    # The denial target is real, not an alias or placeholder environment.
    private_ref = "acceptance-denied-" + secrets.token_hex(6)
    nessie = golden.service_url(config, "nessie", 19120)
    source = observatory.request(
        nessie + "/api/v1/trees/tree/" + urllib.parse.quote(binding["nessie_ref"], safe="")
    )
    observatory.request(
        nessie + "/api/v1/trees/tree",
        payload={"name": private_ref, "type": "BRANCH", "hash": source["hash"]},
        expected=(200,),
    )
    # Root can read the operator's private inputs; only owned volumes are
    # chowned. Keycloak and the proxy never receive the host private directory.
    fixture = {
        "services": {
            "login-init": {
                "image": api_image,
                "user": "0:0",
                "network_mode": "none",
                "read_only": True,
                "entrypoint": ["python", "-c"],
                "command": [
                    "import shutil,os; from pathlib import Path; "
                    "t=Path('/target'); shutil.copytree('/source',t,dirs_exist_ok=True); "
                    "shutil.copyfile('/source/import/realm.json','/realm/realm.json'); os.chown('/realm',1000,1000); os.chown('/realm/realm.json',1000,1000); os.chmod('/realm/realm.json',0o600); "
                    "[(os.chown(p,1000,1000),os.chmod(p,0o755 if p.is_dir() else 0o644 if p.suffix=='.pem' else 0o600)) for p in [t,*t.rglob('*')]]"
                ],
                "volumes": [str(root) + ":/source:ro", "login-data:/target", "realm-data:/realm"],
            },
            "idp": {
                "image": KEYCLOAK,
                "user": "1000:1000",
                "mem_limit": "768m",
                "command": ["start", "--import-realm"],
                "depends_on": {"login-init": {"condition": "service_completed_successfully"}},
                "environment": {
                    "JAVA_OPTS_KC_HEAP": "-Xms64m -Xmx384m",
                    "KC_HOSTNAME": issuer.removesuffix("/realms/acceptance"),
                    "KC_HTTPS_PORT": str(issuer_port),
                    "KC_HTTP_ENABLED": "false",
                    "KC_HTTPS_CERTIFICATE_FILE": "/fixture/public/ca.pem",
                    "KC_HTTPS_CERTIFICATE_KEY_FILE": "/fixture/tls.key",
                },
                "ports": [f"127.0.0.1:{issuer_port}:{issuer_port}"],
                "volumes": ["login-data:/fixture:ro", "realm-data:/opt/keycloak/data/import:ro"],
                "networks": {"default": {"aliases": ["idp.phlo.localhost"]}},
            },
            "login-proxy": {
                "image": proxy_image,
                "user": "1000:1000",
                "environment": {
                    "OAUTH2_PROXY_HTTPS_ADDRESS": "0.0.0.0:4443",
                    "OAUTH2_PROXY_TLS_CERT_FILE": "/fixture/public/ca.pem",
                    "OAUTH2_PROXY_TLS_KEY_FILE": "/fixture/tls.key",
                    "OAUTH2_PROXY_PROVIDER_CA_FILES": "/fixture/public/ca.pem",
                    "OAUTH2_PROXY_PROVIDER": "oidc",
                    "OAUTH2_PROXY_OIDC_ISSUER_URL": issuer,
                    "OAUTH2_PROXY_CLIENT_ID": "phlo-acceptance",
                    "OAUTH2_PROXY_CLIENT_SECRET": client_secret,
                    "OAUTH2_PROXY_COOKIE_SECRET": base64.urlsafe_b64encode(
                        secrets.token_bytes(32)
                    ).decode(),
                    "OAUTH2_PROXY_COOKIE_SECURE": "true",
                    "OAUTH2_PROXY_REDIRECT_URL": base + "/oauth2/callback",
                    "OAUTH2_PROXY_EMAIL_DOMAINS": "example.test",
                    "OAUTH2_PROXY_SCOPE": "openid email profile",
                    "OAUTH2_PROXY_UPSTREAMS": "http://observatory:3000/,http://phlo-api:4000/api/",
                    "OAUTH2_PROXY_PASS_ACCESS_TOKEN": "true",
                    "OAUTH2_PROXY_PASS_AUTHORIZATION_HEADER": "true",
                    "OAUTH2_PROXY_SKIP_PROVIDER_BUTTON": "true",
                },
                "ports": [f"127.0.0.1:{proxy_port}:4443"],
                "volumes": ["login-data:/fixture:ro"],
            },
        },
        "volumes": {"login-data": {}, "realm-data": {}},
        "networks": {"default": {"external": True, "name": network}},
    }
    observatory.private_file(root / "fixture.json", json.dumps(fixture))
    observatory.installed(
        config,
        """
import json,sys,yaml
from pathlib import Path
root=Path.cwd(); issuer,viewer,env,ca,private_ref=sys.argv[1:]
path=root/'phlo.yaml'; document=yaml.safe_load(path.read_text())
document['authentication']={'provider':'jwt','jwt':{'enabled':True,'issuer':issuer,
    'audience':'phlo-acceptance','jwks_url':issuer+'/protocol/openid-connect/certs','ca_file':'/acceptance-ca/ca.pem'}}
workspace=yaml.safe_load((root/'.phlo/dagster/workspace.yaml').read_text())
definition=dict(workspace['load_from'][0]['python_module'])
definition['location_name']=private_ref
workspace['load_from'].append({'python_module':definition})
fixture=Path(ca).parent
(fixture/'workspace.yaml').write_text(yaml.safe_dump(workspace))
document['environments']['denied']={**document['environments'][env],
    'dagster_location':private_ref,'nessie_ref':private_ref,'compose_project':None,'preview':None}
settings=document['services']['phlo-api']['environment']
settings.update({'PHLO_AUTHENTICATION_PROVIDER':'jwt','PHLO_AUTH_DEV_MODE':'false',
    'PHLO_AUTH_JWT_SECRET':'',
    'PHLO_AUTH_JWT_ISSUER':issuer,'PHLO_AUTH_JWT_AUDIENCE':'phlo-acceptance',
    'PHLO_AUTH_JWT_JWKS_URL':issuer+'/protocol/openid-connect/certs','PHLO_AUTH_JWT_CA_FILE':'/acceptance-ca/ca.pem'})
path.write_text(yaml.safe_dump(document))
roles=root/'.phlo/authorization/roles.yaml'
roles.write_text(yaml.safe_dump({'roles':{'viewer':{}},'subjects':{'users':{viewer:['viewer']}}}))
policies=[{'policy_id':'acceptance-discovery','effect':'allow','principal':{'roles':['viewer']},
    'action':'platform_metadata.read','resource':{'type':'platform_metadata','id_pattern':'*'}}]
policies.extend({'policy_id':'acceptance-read-'+str(i),'effect':'allow','principal':{'roles':['viewer']},
    'action':'*.read','resource':{'type':'*','id_pattern':pattern}} for i,pattern in enumerate((f'env={env}',f'env={env}|*')))
(root/'.phlo/authorization/policies.yaml').write_text(yaml.safe_dump({'policies':policies}))
layer=root/'.phlo/overrides/compose.yaml'
# Only the two port resets written by this owned fixture need normalising on retry.
document=yaml.safe_load(layer.read_text().replace('ports: !reset []','ports: []'))
api=document.setdefault('services',{}).setdefault('phlo-api',{})
api.setdefault('environment',{}).update(settings)
api.setdefault('volumes',[]).append(ca+':/acceptance-ca:ro')
api['ports']='RESET'
document['services'].setdefault('observatory',{})['ports']='RESET'
document['services'].setdefault('dagster',{}).setdefault('volumes',[]).append(str(fixture/'workspace.yaml')+':/opt/dagster/workspace.yaml:ro')
layer.write_text(yaml.safe_dump(document).replace('ports: RESET','ports: !reset []'))
print('{}')
""",
        issuer,
        VIEWER,
        binding["env"],
        str(public),
        private_ref,
    )
    return root, issuer, base, private_ref


def wait_https(url: str, context: ssl.SSLContext) -> None:
    """Wait for the fixture's CA-verified HTTPS endpoint, not just its container."""
    for _ in range(120):
        try:
            with urllib.request.urlopen(url, context=context, timeout=3):
                return
        except OSError:
            time.sleep(1)
    raise golden.CandidateError("Disposable OIDC HTTPS endpoint did not become ready")


def run(config: golden.RunConfig, binding: dict, artifacts: Path, proxy_image: str) -> dict:
    root, issuer, base, private_ref = prepare(config, binding, proxy_image)
    fixture = [
        "docker",
        "compose",
        "-p",
        config.project_name + "-oidc",
        "-f",
        str(root / "fixture.json"),
    ]
    trust = "phlo-acceptance-" + secrets.token_hex(8)
    nss = Path.home() / ".pki/nssdb"
    nss.mkdir(parents=True, exist_ok=True)
    if not (nss / "cert9.db").exists():
        subprocess.run(
            ["certutil", "-N", "--empty-password", "-d", "sql:" + str(nss)],
            capture_output=True,
            check=True,
        )
    subprocess.run(
        [
            "certutil",
            "-A",
            "-n",
            trust,
            "-t",
            "C,,",
            "-i",
            str(root / "public/ca.pem"),
            "-d",
            "sql:" + str(nss),
        ],
        capture_output=True,
        check=True,
    )
    session = "oidc-" + secrets.token_hex(6)
    try:
        subprocess.run([*fixture, "up", "-d", "idp"], capture_output=True, check=True)
        context = ssl.create_default_context(cafile=str(root / "public/ca.pem"))
        wait_https(
            issuer.replace("idp.phlo.localhost", "127.0.0.1") + "/.well-known/openid-configuration",
            context,
        )
        # Ordinary configured authenticated startup must not claim browser proof.
        startup = subprocess.run(
            golden.command(str(config.operator_bin), "services", "start"),
            cwd=config.project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        observatory.private_file(root / "startup-private.log", startup.stdout + startup.stderr)
        if startup.returncode:
            raise golden.CandidateError(
                "Ordinary authenticated startup failed; private logs retained"
            )
        receipt = json.loads((config.project_dir / ".phlo/observatory-ready.json").read_text())
        if receipt["state"] != "verification_required" or receipt["browser_login_verified"]:
            raise golden.CandidateError("Authenticated startup falsely attested browser login")
        repositories = golden.graphql(
            golden.service_url(config, "dagster", 3000, "/graphql"),
            "{repositoriesOrError {... on RepositoryConnection {nodes {location {name}}}}}",
            {},
            "",
        )
        if private_ref not in {
            item["location"]["name"]
            for item in repositories.get("data", {}).get("repositoriesOrError", {}).get("nodes", [])
        }:
            raise golden.CandidateError("Denied environment lacks its actual Dagster code location")
        subprocess.run([*fixture, "up", "-d", "login-proxy"], capture_output=True, check=True)
        wait_https(base.replace("observatory.phlo.localhost", "127.0.0.1") + "/ping", context)
        for attempt in range(3):
            try:
                observatory._browser(session, "open", base + "/?env=" + binding["env"])
                break
            except subprocess.CalledProcessError as exc:
                # Docker link changes can invalidate Chrome's first navigation.
                # Do not retry login failures, HTTP failures or TLS errors.
                if attempt == 2 or "ERR_NETWORK_CHANGED" not in (exc.stdout or "") + (
                    exc.stderr or ""
                ):
                    raise
                time.sleep(2)
        observatory._browser(session, "set", "viewport", "1280", "800", "2")
        observatory._browser(session, "wait", "#username")
        password = json.loads((root / "login.json").read_text())["password"]
        script = (
            "document.querySelector('#username').value='viewer@example.test';"
            "document.querySelector('#password').value=" + json.dumps(password) + ";"
            "document.querySelector('#kc-login').click(); void 0"
        )
        # Credentials travel on stdin, not printable CLI arguments or evidence.
        subprocess.run(
            ["agent-browser", "--session", session, "eval", "--stdin", "--json"],
            input=script,
            capture_output=True,
            text=True,
            check=True,
        )
        observatory._browser(
            session, "wait", "--fn", "location.hostname==='observatory.phlo.localhost'"
        )
        identity = observatory._browser(
            session, "eval", "fetch('/oauth2/userinfo').then(r=>r.json())"
        )
        observatory.private_file(root / "identity-private.json", json.dumps(identity))
        state = observatory._browser(
            session,
            "eval",
            "(async()=>{const r=await fetch('/api/v1/me');return {status:r.status,body:await r.json()}})()",
        ).get("result", {})
        if (
            state.get("status") != 200
            or state["body"].get("subject") != VIEWER
            or state["body"].get("roles") != ["viewer"]
        ):
            raise golden.CandidateError(
                "Browser login did not reach independently verified canonical viewer"
            )
        observatory._browser(session, "wait", "--text", "Overview")
        for path in ("/api/v1/overview?env=denied", "/api/v1/admin/members"):
            status = observatory._browser(
                session, "eval", "fetch(" + json.dumps(path) + ").then(r=>r.status)"
            ).get("result")
            if status != 403:
                raise golden.CandidateError(
                    "Authenticated viewer bypassed environment or admin denial"
                )
        observatory._browser(session, "screenshot", str(artifacts / "oidc-viewer.png"))
        # Stay on a public endpoint after clearing the proxy cookie; returning
        # to a protected page would legitimately initiate another IdP SSO login.
        observatory._browser(session, "open", base + "/oauth2/sign_out?rd=/ping")
        status = observatory._browser(
            session,
            "eval",
            "fetch('/oauth2/auth',{headers:{'X-Auth-Request-User':'forged','X-Auth-Request-Access-Token':'forged'}}).then(r=>r.status)",
        ).get("result")
        if status != 401:
            raise golden.CandidateError("Logout or forged-header proxy denial failed")
        observatory.compose(
            config,
            "exec",
            "-T",
            "phlo-api",
            "python",
            "-c",
            "import urllib.request,urllib.error; "
            "request=urllib.request.Request('http://127.0.0.1:4000/api/v1/me',headers={'X-Auth-Request-User':'forged'});\n"
            "try: urllib.request.urlopen(request); raise AssertionError('Unauthenticated API allowed')\n"
            "except urllib.error.HTTPError as error: assert error.code==401",
        )
        return {
            "browser_password_login": True,
            "api_jwt_verified": True,
            "canonical_role": "viewer",
            "environment_denied": True,
            "admin_denied": True,
            "startup_receipt": "verification_required",
            "tls_verified": True,
            "logout_denied": True,
            "forged_header_denied": True,
            "api_no_token_denied": True,
        }
    except Exception:
        # Login failures can contain credentials and callback parameters. Keep
        # diagnostics only with the private fixture, never in public evidence.
        try:
            state = observatory._browser(
                session, "eval", "({path:location.pathname,text:document.body.innerText})"
            )
            observatory.private_file(root / "browser-private.json", json.dumps(state))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        raise
    finally:
        # Never inherit raw provider logs into CI, including failed startup.
        logs = subprocess.run([*fixture, "logs", "--no-color"], capture_output=True, text=True)
        observatory.private_file(root / "provider-private.log", logs.stdout + logs.stderr)
        # A crashed browser must not prevent owned provider cleanup.
        with suppress(subprocess.TimeoutExpired):
            subprocess.run(
                ["agent-browser", "--session", session, "close"],
                capture_output=True,
                check=False,
                timeout=30,
            )
        subprocess.run(
            [*fixture, "down", "--volumes", "--remove-orphans"], capture_output=True, check=True
        )
        subprocess.run(
            ["certutil", "-D", "-n", trust, "-d", "sql:" + str(nss)],
            capture_output=True,
            check=True,
        )
