"""Correos de la app: verificar la cuenta y recuperar la clave.

Está hecho pero APAGADO hasta configurar un remitente (p. ej.
support@pacepathapp.com) en un servicio de envío por SMTP (Resend, Brevo,
Amazon SES…). Variables en Render:

  PACEPATH_CORREO_ACTIVO=1             lo enciende (sin esto no se manda nada)
  PACEPATH_CORREO_SMTP=smtp.resend.com:465
  PACEPATH_CORREO_USUARIO=…            usuario SMTP del servicio
  PACEPATH_CORREO_CLAVE=…              clave o API key SMTP (nunca en el repo)
  PACEPATH_CORREO_DE=PacePath <support@pacepathapp.com>

Mientras esté apagado: no se pide verificar el correo para entrar y la opción
«¿Olvidaste tu clave?» no aparece (el admin puede poner una clave nueva).
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from .idioma import tr


def activo() -> bool:
    return os.environ.get("PACEPATH_CORREO_ACTIVO") == "1" and all(
        os.environ.get(k) for k in ("PACEPATH_CORREO_SMTP", "PACEPATH_CORREO_USUARIO",
                                    "PACEPATH_CORREO_CLAVE", "PACEPATH_CORREO_DE"))


def enviar(para: str, asunto: str, texto: str) -> None:
    if not activo():
        raise RuntimeError("el envío de correos está apagado")
    host, _, puerto = os.environ["PACEPATH_CORREO_SMTP"].partition(":")
    puerto = int(puerto or 465)
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = os.environ["PACEPATH_CORREO_DE"], para, asunto
    m["Message-ID"] = make_msgid(domain="pacepathapp.com")
    m.set_content(texto)
    contexto = ssl.create_default_context()
    if puerto == 465:
        with smtplib.SMTP_SSL(host, puerto, context=contexto, timeout=15) as s:
            s.login(os.environ["PACEPATH_CORREO_USUARIO"], os.environ["PACEPATH_CORREO_CLAVE"])
            s.send_message(m)
    else:
        with smtplib.SMTP(host, puerto, timeout=15) as s:
            s.starttls(context=contexto)
            s.login(os.environ["PACEPATH_CORREO_USUARIO"], os.environ["PACEPATH_CORREO_CLAVE"])
            s.send_message(m)


def verificar(para: str, nombre: str | None, enlace: str) -> None:
    hola = nombre or para
    enviar(para, tr("Confirma tu correo en PacePath", "Confirm your email on PacePath"), tr(
        f"Hola, {hola}:\n\nConfirma tu correo para terminar de crear tu cuenta de PacePath:\n\n{enlace}\n\n"
        "El enlace vale 48 horas. Si no creaste una cuenta, ignora este correo.\n\nPacePath · pacepathapp.com",
        f"Hi {hola},\n\nConfirm your email to finish creating your PacePath account:\n\n{enlace}\n\n"
        "The link is valid for 48 hours. If you didn't create an account, ignore this email.\n\nPacePath · pacepathapp.com"))


def recuperar(para: str, nombre: str | None, enlace: str) -> None:
    hola = nombre or para
    enviar(para, tr("Cambia tu clave de PacePath", "Reset your PacePath password"), tr(
        f"Hola, {hola}:\n\nPediste cambiar tu clave de PacePath. Elige una nueva aquí:\n\n{enlace}\n\n"
        "El enlace vale 1 hora y sirve una sola vez. Si no lo pediste, ignora este correo: tu clave no cambia.\n\n"
        "PacePath · pacepathapp.com",
        f"Hi {hola},\n\nYou asked to reset your PacePath password. Choose a new one here:\n\n{enlace}\n\n"
        "The link is valid for 1 hour and works once. If you didn't ask for it, ignore this email: your password "
        "stays the same.\n\nPacePath · pacepathapp.com"))
