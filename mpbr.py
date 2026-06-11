#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://mprs.sefaz.rs.gov.br/API/ConsultaMenorPrecoBrasil/api/v1/"
AUTHORIZE_URL = "https://sso.acesso.gov.br/authorize"
TOKEN_URL = BASE + "Login/token"
REFRESH_URL = BASE + "Login/refresh_token"
CLIENT_ID = "p-mprs.sefaz.rs.gov.br"
REDIRECT_URI = "br.gov.rs.procergs.mpbr://oauth/auth"
SCOPE = "openid email profile govbr_confiabilidades"
AMBIENTE = "Producao"
ORIGEM = "ios"


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
TOKENS_PATH = HERE / "tokens.json"
STATE_PATH = HERE / "state.json"

UA = "MenorPrecoBrasil/2.0.8 (mpbr-price-alert)"


# --- tiny json helpers -----------------------------------------------------
def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_json(r) -> dict:
    try:
        return r.json()
    except ValueError:
        return {}


# --- auth ------------------------------------------------------------------
def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256, as gov.br requires."""
    import base64
    import hashlib
    import secrets

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def login() -> None:
    """Interactive login via gov.br with PKCE. Prints the authorize URL, takes
    the redirected code, exchanges it at the sefaz Login/token endpoint."""
    import secrets

    verifier, challenge = _pkce_pair()
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(6),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    print("\n1) Open this URL in a browser and log in with your CPF (gov.br):\n")
    print("   " + url + "\n")
    print("2) After login the browser is redirected to a URL starting with")
    print(f"   {REDIRECT_URI}?code=...   (the browser can't open that custom")
    print("   scheme, so it shows an error / blank page — just copy the address bar).\n")
    raw = input("Paste the full redirected URL (or just the code): ").strip()

    code = raw
    if "code=" in raw:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [raw])[0]

    body = (
        f"grant_type=authorization_code&code={code}"
        f"&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
        f"&ambiente={AMBIENTE}&code_verifier={verifier}"
    )
    r = requests.post(
        TOKEN_URL,
        data=body,
        headers={
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "capacitor://localhost",
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f"\n✗ Token endpoint returned HTTP {r.status_code}")
        print("  Response body:", r.text[:1000])
        print("\n  Codes are single-use and short-lived — paste it immediately after login.")
        print("  If gov.br blocks the browser flow, fall back to:  python mpbr.py import-ticket")
        sys.exit(1)
    tok = r.json()
    if "access_token" not in tok:
        sys.exit(f"Login failed: {tok}")
    _store_tokens(tok)
    exp_days = round(int(tok.get("expires_in", 0)) / 86400, 1)
    print(f"\n✓ Logged in. Token saved to tokens.json (valid ~{exp_days} days).")


def _store_tokens(tok: dict) -> None:
    tok["_obtained_at"] = int(time.time())
    save_json(TOKENS_PATH, tok)


def import_ticket(raw: str | None = None) -> None:
    """Import a token captured from the REAL app (fallback path).

    Prefer `login` (gov.br + PKCE). Use this only if the browser login flow is
    blocked and you instead captured the token from the running app — e.g. the
    JSON body of the Login/token response via mitmproxy. Paste either:
      - the JSON body of the Login/token response (captured via mitmproxy), or
      - the value of the Capacitor Preferences key "ticket"
        (Android: shared_prefs/CapacitorStorage.xml on a rooted device/emulator).
    Both look like: {"access_token":"...","refresh_token":"...","expires_in":...}
    """
    if raw is None:
        print("Paste the ticket JSON (single line), then press Enter:")
        raw = input().strip()
    try:
        tok = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"Not valid JSON: {e}")
    if "access_token" not in tok:
        sys.exit("JSON has no 'access_token' — wrong object.")

    # honor the app's own creation_date if present, so expiry math stays accurate
    cd = tok.get("creation_date")
    if cd:
        try:
            dt = datetime.fromisoformat(cd.replace("Z", "+00:00"))
            tok["_obtained_at"] = int(dt.timestamp())
            save_json(TOKENS_PATH, tok)
            print("✓ Ticket imported to tokens.json (using app creation_date).")
            return
        except ValueError:
            pass
    _store_tokens(tok)
    print("✓ Ticket imported to tokens.json")


def refresh_access_token() -> str:
    """Return a valid access_token, refreshing if needed."""
    tok = load_json(TOKENS_PATH)
    if not tok:
        sys.exit("No tokens. Run:  python mpbr.py login")

    obtained = tok.get("_obtained_at", 0)
    expires_in = int(tok.get("expires_in", 0))
    # refresh a bit early (app refreshes when <24h remain; we use a 5-min margin)
    if expires_in and time.time() < obtained + expires_in - 300:
        return tok["access_token"]

    rt = tok.get("refresh_token")
    if not rt:
        sys.exit("Token expired and no refresh_token. Run:  python mpbr.py login")

    body = (
        f"grant_type=refresh_token&refresh_token={rt}"
        f"&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&ambiente={AMBIENTE}"
    )
    r = requests.post(
        REFRESH_URL,
        data=body,
        headers={
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        timeout=30,
    )
    if r.status_code != 200 or "access_token" not in (new := _safe_json(r)):
        sys.exit(
            f"Refresh failed (HTTP {r.status_code}): {r.text[:300]}\n"
            "Re-capture a fresh ticket:  python mpbr.py import-ticket"
        )
    # keep the old refresh_token if the response omits a new one
    new.setdefault("refresh_token", rt)
    _store_tokens(new)
    return new["access_token"]


# --- search ----------------------------------------------------------------
def search_gtin(gtin: str, cfg: dict, token: str) -> list[dict]:
    params = {
        "pesquisa.gtin": gtin,
        "pesquisa.latitude": cfg["latitude"],
        "pesquisa.longitude": cfg["longitude"],
        "pesquisa.nroKmDistancia": cfg.get("raio_km", 10),
        "pesquisa.nroDiaPrz": cfg.get("dias", 3),
        "pesquisa.origem": ORIGEM,
    }
    r = requests.get(
        BASE + "Item/PorGtin",
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": "bearer " + token,
            "User-Agent": UA,
        },
        timeout=30,
    )
    if r.status_code == 401:
        sys.exit("401 Unauthorized — token rejected. Re-run:  python mpbr.py login")
    r.raise_for_status()
    data = r.json()
    return data.get("itens", []) or []


def cheapest(itens: list[dict]) -> dict | None:
    valid = [i for i in itens if i.get("vlrItem") is not None]
    return min(valid, key=lambda i: i["vlrItem"]) if valid else None


# --- alerting --------------------------------------------------------------
def notify(cfg: dict, subject: str, body: str) -> None:
    print(f"\n🔔 {subject}\n{body}\n")
    hook = cfg.get("webhook_url")
    if hook:
        try:
            requests.post(hook, json={"text": f"*{subject}*\n{body}"}, timeout=15)
        except Exception as e:  # noqa: BLE001 - alerting must never crash the run
            print(f"   (webhook failed: {e})")


def fmt_store(est: dict) -> str:
    name = est.get("nomeFant") or est.get("nomeContrib") or "?"
    parts = [est.get("nomeLograd"), est.get("nroLograd"), est.get("nomeBairro"),
             est.get("nomeMunic"), est.get("siglaUf")]
    addr = ", ".join(str(p) for p in parts if p)
    km = est.get("kmDistancia")
    km_s = f" — {km:.1f} km" if isinstance(km, (int, float)) else ""
    return f"{name} ({addr}{km_s})"


# --- run -------------------------------------------------------------------
def run() -> None:
    cfg = load_json(CONFIG_PATH)
    if not cfg:
        sys.exit(f"No config. Copy config.example.json to config.json and edit it.")
    token = refresh_access_token()
    state = load_json(STATE_PATH, default={})

    for watch in cfg["produtos"]:
        gtin = str(watch["gtin"]).strip()
        threshold = watch.get("preco_alvo")  # alert if price <= this; None = alert on any drop
        label = watch.get("nome", gtin)

        try:
            itens = search_gtin(gtin, cfg, token)
        except requests.HTTPError as e:
            print(f"[{label}] request error: {e}")
            continue

        best = cheapest(itens)
        if not best:
            print(f"[{label}] no recent prices in the last {cfg.get('dias', 3)} day(s).")
            continue

        price = best["vlrItem"]
        store = fmt_store(best.get("estabelecimento", {}))
        desc = best.get("texDesc", label)
        when = best.get("dthEmiNFe", "")
        prev = state.get(gtin, {}).get("price")

        print(f"[{label}] cheapest R$ {price:.2f} @ {store}"
              + (f"  (was R$ {prev:.2f})" if prev is not None else ""))

        hit_threshold = threshold is not None and price <= threshold
        dropped = prev is not None and price < prev
        if hit_threshold or dropped:
            reason = (f"≤ alvo R$ {threshold:.2f}" if hit_threshold
                      else f"caiu de R$ {prev:.2f}")
            notify(
                cfg,
                f"Menor preço: {desc} — R$ {price:.2f}",
                f"{desc}\nGTIN {gtin}\nR$ {price:.2f} ({reason})\n{store}\nNFC-e: {when}",
            )

        state[gtin] = {
            "price": price,
            "store": store,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    save_json(STATE_PATH, state)


# --- cli -------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Menor Preço Brasil (RS) price-drop alerts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="interactive CPF login (often blocked by gov.br federation — see import-ticket)")
    p_imp = sub.add_parser("import-ticket", help="RECOMMENDED: import a token captured from the real app")
    p_imp.add_argument("json", nargs="?", help="ticket JSON; if omitted, read from stdin prompt")
    sub.add_parser("run", help="poll watched GTINs and alert on price drops")
    sub.add_parser("token", help="print a valid access_token (debug)")
    args = ap.parse_args()

    if args.cmd == "login":
        login()
    elif args.cmd == "import-ticket":
        import_ticket(args.json)
    elif args.cmd == "run":
        run()
    elif args.cmd == "token":
        print(refresh_access_token())


if __name__ == "__main__":
    main()
