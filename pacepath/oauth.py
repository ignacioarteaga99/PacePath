"""Entrar con Google o Microsoft (OpenID Connect, flujo de código con PKCE).

Credenciales en variables de entorno (Render), nunca en el repositorio:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET          (Google Cloud Console)
  MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET    (Microsoft Entra ID)
Dirección de vuelta que hay que registrar en cada una:
  {PACEPATH_URL}/api/auth/google/vuelta   ·   {PACEPATH_URL}/api/auth/microsoft/vuelta

El id_token se lee sin verificar la firma: llega directo del servidor de
Google o Microsoft por HTTPS como respuesta a nuestro pedido (OpenID Connect
lo permite en ese caso), no desde el navegador.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

COOKIE_ESTADO = "pp_oauth"

PROVEEDORES = {
    "google": {
        "autorizar": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
        "extra": {"prompt": "select_account"},
    },
    "microsoft": {
        "autorizar": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "openid email profile",
        "extra": {"prompt": "select_account"},
    },
}


class ErrorOAuth(Exception):
    pass


def _credenciales(proveedor: str) -> tuple[str, str]:
    p = proveedor.upper()
    return os.environ.get(f"{p}_CLIENT_ID", "").strip(), os.environ.get(f"{p}_CLIENT_SECRET", "").strip()


def configurados() -> dict[str, bool]:
    return {p: all(_credenciales(p)) for p in PROVEEDORES}


def iniciar(proveedor: str, vuelta: str) -> tuple[str, str]:
    """(URL a la que mandar al usuario, valor de la cookie de estado)."""
    cid, _ = _credenciales(proveedor)
    estado, verificador = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    p = PROVEEDORES[proveedor]
    q = {"client_id": cid, "redirect_uri": vuelta, "response_type": "code", "scope": p["scope"],
         "state": estado, "code_challenge": reto, "code_challenge_method": "S256", **p["extra"]}
    return f"{p['autorizar']}?{urllib.parse.urlencode(q)}", f"{proveedor}.{estado}.{verificador}"


def _claims(id_token: str) -> dict:
    try:
        cuerpo = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except (IndexError, ValueError) as exc:
        raise ErrorOAuth("id_token ilegible") from exc


def terminar(proveedor: str, code: str, state: str, cookie: str | None, vuelta: str) -> dict:
    """Canjea el código. Devuelve {sub, correo, nombre, correo_confiable}."""
    try:
        prov, estado, verificador = (cookie or "").split(".")
    except ValueError as exc:
        raise ErrorOAuth("sin cookie de estado (¿venció?)") from exc
    if prov != proveedor or not secrets.compare_digest(estado, state or ""):
        raise ErrorOAuth("el estado de la vuelta no coincide")
    cid, secreto = _credenciales(proveedor)
    datos = urllib.parse.urlencode({"client_id": cid, "client_secret": secreto, "code": code,
                                    "redirect_uri": vuelta, "grant_type": "authorization_code",
                                    "code_verifier": verificador}).encode()
    req = urllib.request.Request(PROVEEDORES[proveedor]["token"], data=datos,
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "PacePath/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — URL fija
            resp = json.load(r)
    except urllib.error.HTTPError as exc:
        raise ErrorOAuth(f"{proveedor} respondió {exc.code}: {exc.read()[:200]!r}") from exc
    except OSError as exc:
        raise ErrorOAuth(f"no pude hablar con {proveedor}: {exc}") from exc
    c = _claims(resp.get("id_token", ""))
    if proveedor == "google":
        return {"sub": c.get("sub"), "correo": c.get("email"), "nombre": c.get("name"),
                "correo_confiable": bool(c.get("email_verified"))}
    # Microsoft: el correo sólo es confiable en cuentas personales (ver cuentas.py).
    from .cuentas import TENANT_PERSONAL
    return {"sub": c.get("sub"), "correo": c.get("email") or c.get("preferred_username"),
            "nombre": c.get("name"), "correo_confiable": c.get("tid") == TENANT_PERSONAL}
