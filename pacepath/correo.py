"""Correos de la app: verificar la cuenta, recuperar la clave, bienvenida,
acceso a la beta y el aviso al equipo de cada registro nuevo.

Todos con la misma plantilla (logo, tarjeta, botón naranjo y al pie los
enlaces de la app: Términos, Privacidad, Guía beta y «Apoya PacePath»), en
HTML con su versión en texto plano.

Está hecho pero APAGADO hasta configurar un remitente (p. ej.
support@pacepathapp.com) en un servicio de envío por SMTP (Resend, Brevo,
Amazon SES…). Variables en Render:

  PACEPATH_CORREO_ACTIVO=1             lo enciende (sin esto no se manda nada)
  PACEPATH_CORREO_SMTP=smtp.resend.com:465
  PACEPATH_CORREO_USUARIO=…            usuario SMTP del servicio
  PACEPATH_CORREO_CLAVE=…              clave o API key SMTP (nunca en el repo)
  PACEPATH_CORREO_DE=PacePath <support@pacepathapp.com>
  PACEPATH_CORREO_AVISOS=support@pacepathapp.com   (opcional) a quién avisar
                                       de cada cuenta nueva

Mientras esté apagado: no se pide verificar el correo para entrar y la opción
«¿Olvidaste tu clave?» no aparece (el admin puede poner una clave nueva).
"""
from __future__ import annotations

import contextvars
import html
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import make_msgid

from .idioma import tr


def activo() -> bool:
    return os.environ.get("PACEPATH_CORREO_ACTIVO") == "1" and all(
        os.environ.get(k) for k in ("PACEPATH_CORREO_SMTP", "PACEPATH_CORREO_USUARIO",
                                    "PACEPATH_CORREO_CLAVE", "PACEPATH_CORREO_DE"))


def enviar(para: str, asunto: str, texto: str, html_: str | None = None) -> None:
    if not activo():
        raise RuntimeError("el envío de correos está apagado")
    host, _, puerto = os.environ["PACEPATH_CORREO_SMTP"].partition(":")
    puerto = int(puerto or 465)
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = os.environ["PACEPATH_CORREO_DE"], para, asunto
    m["Message-ID"] = make_msgid(domain="pacepathapp.com")
    m.set_content(texto)
    if html_:
        m.add_alternative(html_, subtype="html")
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


def en_fondo(fn, *args) -> None:
    """Manda sin hacer esperar al pedido (con el idioma del pedido). Un error
    sólo queda en el log: el correo caído no debe romper nada."""
    ctx = contextvars.copy_context()

    def correr():
        try:
            ctx.run(fn, *args)
        except Exception as exc:  # noqa: BLE001
            print(f"[correo] no pude mandar {getattr(fn, '__name__', fn)}: {type(exc).__name__}: {exc}", flush=True)
    threading.Thread(target=correr, name="correo", daemon=True).start()


# ── Plantilla ───────────────────────────────────────────────────────────────
ACENTO, TINTA, SUAVE, FONDO = "#f2541b", "#1d1d21", "#6b6b75", "#f4f4f6"


def _base() -> str:
    return os.environ.get("PACEPATH_URL", "https://pacepathapp.com").rstrip("/")


def _pie() -> list[tuple[str, str]]:
    b = _base()
    enlaces = [(tr("Abrir PacePath", "Open PacePath"), b), (tr("Guía beta", "Beta guide"), f"{b}/beta"),
               (tr("Términos", "Terms"), f"{b}/terminos"), (tr("Privacidad", "Privacy"), f"{b}/privacidad")]
    if (aporte := os.environ.get("PACEPATH_APORTE_URL", "").strip()).startswith("https://"):
        enlaces.append(("☕ " + tr("Apoya PacePath", "Support PacePath"), aporte))
    return enlaces


def _correo(titulo: str, parrafos: list[str], boton: tuple[str, str] | None = None,
            nota: str | None = None, lista: list[str] | None = None, motivo: str | None = None) -> tuple[str, str]:
    """(texto plano, HTML). Los párrafos son texto (se escapa); `lista` son
    viñetas; `nota` va chica bajo el botón; `motivo`, al pie, por qué le llega."""
    e = html.escape
    pie = _pie()
    soporte = "support@pacepathapp.com"
    motivo = motivo or tr("Recibes este correo por tu cuenta en PacePath.",
                          "You're getting this email because of your PacePath account.")
    # Texto plano
    t = [titulo, ""] + [p + "\n" for p in parrafos]
    t += [f"  · {x}" for x in (lista or [])] + ([""] if lista else [])
    if boton:
        t += [f"{boton[0]}: {boton[1]}", ""]
    if nota:
        t += [nota, ""]
    t += ["—", "PacePath · " + tr("la ruta perfecta para el entrenamiento de hoy",
                                  "the perfect route for today's workout"),
          " · ".join(f"{n}: {u}" for n, u in pie), motivo,
          tr(f"¿Dudas? Escríbenos a {soporte}", f"Questions? Write to {soporte}")]
    texto = "\n".join(t)
    # HTML (tablas y estilos en línea: es lo que entienden Gmail, Outlook y Apple Mail)
    fuente = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    cuerpo = "".join(f'<p style="margin:0 0 14px;font-size:16px;line-height:1.55;color:{TINTA}">{e(p)}</p>'
                     for p in parrafos)
    if lista:
        cuerpo += '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 18px">' + "".join(
            f'<tr><td style="vertical-align:top;padding:4px 10px 4px 0;color:{ACENTO};font-size:16px">●</td>'
            f'<td style="padding:4px 0;font-size:15px;line-height:1.5;color:{TINTA}">{e(x)}</td></tr>' for x in lista) + "</table>"
    if boton:
        cuerpo += (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 18px"><tr>'
                   f'<td style="border-radius:12px;background:{ACENTO}"><a href="{e(boton[1])}" '
                   f'style="display:inline-block;padding:14px 26px;font:700 16px {fuente};color:#ffffff;'
                   f'text-decoration:none;border-radius:12px">{e(boton[0])}</a></td></tr></table>'
                   f'<p style="margin:0 0 14px;font-size:12px;line-height:1.5;color:{SUAVE}">'
                   + tr("Si el botón no funciona, copia este enlace:", "If the button doesn't work, copy this link:")
                   + f'<br><a href="{e(boton[1])}" style="color:{SUAVE};word-break:break-all">{e(boton[1])}</a></p>')
    if nota:
        cuerpo += f'<p style="margin:0;font-size:13px;line-height:1.5;color:{SUAVE}">{e(nota)}</p>'
    enlaces = ' <span style="color:#c4c4cc">·</span> '.join(
        f'<a href="{e(u)}" style="color:{SUAVE};text-decoration:underline">{e(n)}</a>' for n, u in pie)
    html_ = f"""<!doctype html><html lang="{tr('es', 'en')}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">
<title>{e(titulo)}</title></head>
<body style="margin:0;padding:0;background:{FONDO};font-family:{fuente}">
<div style="display:none;max-height:0;overflow:hidden">{e(parrafos[0] if parrafos else titulo)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{FONDO}"><tr><td align="center" style="padding:28px 14px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
<tr><td style="padding:0 6px 18px">
  <a href="{_base()}" style="text-decoration:none;color:{TINTA}"><img src="{_base()}/icons/icono-192.png" width="40" height="40" alt=""
   style="vertical-align:middle;border-radius:10px;border:0"> <span style="vertical-align:middle;font:800 20px {fuente};color:{TINTA};letter-spacing:-.3px">PacePath</span></a>
</td></tr>
<tr><td style="background:#ffffff;border-radius:20px;padding:30px 28px;border:1px solid #e7e7ec">
  <div style="width:44px;height:4px;border-radius:2px;background:{ACENTO};margin:0 0 18px"></div>
  <h1 style="margin:0 0 16px;font:800 26px/1.2 {fuente};color:{TINTA};letter-spacing:-.4px">{e(titulo)}</h1>
  {cuerpo}
</td></tr>
<tr><td style="padding:22px 10px 0;text-align:center;font-size:13px;line-height:1.7;color:{SUAVE}">
  <div style="margin-bottom:8px">{enlaces}</div>
  <div>{e(motivo)}</div>
  <div>{tr("¿Dudas? Escríbenos a", "Questions? Write to")} <a href="mailto:{soporte}" style="color:{SUAVE}">{soporte}</a></div>
  <div style="margin-top:8px;color:#9a9aa3">{tr("Hecho en Santiago de Chile", "Made in Santiago, Chile")} 🇨🇱</div>
</td></tr>
</table></td></tr></table></body></html>"""
    return texto, html_


def _hola(nombre: str | None) -> str:
    return tr(f"Hola, {nombre}:", f"Hi {nombre},") if nombre else tr("Hola:", "Hi,")


# ── Los correos ─────────────────────────────────────────────────────────────

def verificar(para: str, nombre: str | None, enlace: str, beta_pendiente: bool = False) -> None:
    parrafos = [_hola(nombre), tr("Confirma tu correo para terminar de crear tu cuenta de PacePath.",
                                  "Confirm your email to finish creating your PacePath account.")]
    if beta_pendiente:
        parrafos.append(tr("PacePath está en beta privada: después de confirmar, tu cuenta queda en la lista de "
                           "espera y te avisamos por correo apenas te habilitemos.",
                           "PacePath is in private beta: after you confirm, your account goes on the waiting "
                           "list and we'll email you as soon as you're in."))
    texto, h = _correo(tr("Confirma tu correo", "Confirm your email"), parrafos,
                       (tr("Confirmar mi correo", "Confirm my email"), enlace),
                       tr("El enlace vale 48 horas. Si no creaste una cuenta, ignora este correo.",
                          "The link is valid for 48 hours. If you didn't create an account, ignore this email."))
    enviar(para, tr("Confirma tu correo en PacePath", "Confirm your email on PacePath"), texto, h)


def recuperar(para: str, nombre: str | None, enlace: str) -> None:
    texto, h = _correo(tr("Cambia tu clave", "Reset your password"),
                       [_hola(nombre), tr("Pediste cambiar tu clave de PacePath. Elige una nueva aquí:",
                                          "You asked to reset your PacePath password. Choose a new one here:")],
                       (tr("Elegir una clave nueva", "Choose a new password"), enlace),
                       tr("El enlace vale 1 hora y sirve una sola vez. Si no lo pediste, ignora este correo: tu clave no cambia.",
                          "The link is valid for 1 hour and works once. If you didn't ask for it, ignore this email: "
                          "your password stays the same."))
    enviar(para, tr("Cambia tu clave de PacePath", "Reset your PacePath password"), texto, h)


def _consejos() -> list[str]:
    return [tr("Escribe tu entrenamiento como lo dirías: «6x1k», «tempo 8k», «fondo 15k plano» o «45 min suave».",
               "Type your workout the way you'd say it: \"6x1k\", \"tempo 8k\", \"long run 15k flat\" or \"45 min easy\"."),
            tr("PacePath busca calles con pocos semáforos y la distancia justa, desde donde estés.",
               "PacePath finds streets with few traffic lights and the right distance, from wherever you are."),
            tr("Conecta Strava para que la ruta prefiera tus calles de siempre y pinta tu ciudad.",
               "Connect Strava so routes prefer your usual streets, and paint your city."),
            tr("Mándala a tu reloj como GPX con «Enviar al reloj».",
               "Send it to your watch as a GPX with \"Send to watch\".")]


def bienvenida(para: str, nombre: str | None, beta_pendiente: bool) -> None:
    """Al quedar registrado (con correo: al confirmarlo; con Google/Microsoft: al crearla)."""
    if beta_pendiente:
        texto, h = _correo(tr("¡Registro exitoso!", "You're registered!"),
                           [_hola(nombre), tr("Tu cuenta de PacePath ya está creada. Estamos en beta privada, así que "
                                              "quedaste en la lista de espera: te escribimos apenas te habilitemos.",
                                              "Your PacePath account is ready. We're in private beta, so you're on "
                                              "the waiting list: we'll write as soon as you're in."),
                            tr("Mientras, esto es lo que vas a poder hacer:", "Meanwhile, here's what you'll be able to do:")],
                           lista=_consejos(),
                           nota=tr("¿Te invitó alguien? Respóndenos este correo y te habilitamos antes.",
                                   "Did someone invite you? Reply to this email and we'll let you in sooner."))
    else:
        texto, h = _correo(tr("¡Registro exitoso!", "You're registered!"),
                           [_hola(nombre), tr("Bienvenido a PacePath: la ruta perfecta para el entrenamiento de hoy.",
                                              "Welcome to PacePath: the perfect route for today's workout."),
                            tr("Para empezar:", "To get started:")],
                           (tr("Abrir PacePath", "Open PacePath"), _base()), lista=_consejos())
    enviar(para, tr("¡Bienvenido a PacePath!", "Welcome to PacePath!"), texto, h)


def acceso_beta(para: str, nombre: str | None, invitado: bool) -> None:
    """Cuando el admin la habilita (o la crea ya habilitada: una invitación)."""
    b = _base()
    if invitado:
        texto, h = _correo(tr("Te invitamos a la beta", "You're invited to the beta"),
                           [_hola(nombre), tr(f"Te habilitamos para probar PacePath antes que nadie. Entra con este "
                                              f"correo ({para}): con Google, si es una cuenta de Google, o creando una clave.",
                                              f"You're in to try PacePath before anyone else. Sign in with this email "
                                              f"({para}): with Google if it's a Google account, or by creating a password.")],
                           (tr("Entrar a PacePath", "Sign in to PacePath"), f"{b}/entrar"), lista=_consejos(),
                           motivo=tr("Recibes este correo porque te invitaron a la beta de PacePath.",
                                     "You're getting this email because you were invited to the PacePath beta."))
    else:
        texto, h = _correo(tr("¡Ya tienes acceso!", "You're in!"),
                           [_hola(nombre), tr("Te habilitamos en la beta de PacePath. Ya puedes entrar y pedir tu primera ruta.",
                                              "You've been let into the PacePath beta. You can sign in and ask for your first route.")],
                           (tr("Abrir PacePath", "Open PacePath"), b), lista=_consejos(),
                           nota=tr("Estamos afinándola: si algo falla o se te ocurre una mejora, usa «Enviar comentario» en la app.",
                                   "We're still fine-tuning it: if something breaks or you have an idea, use \"Send feedback\" in the app."))
    enviar(para, tr("Ya puedes entrar a PacePath", "You can now use PacePath") if not invitado
           else tr("Te invitamos a la beta de PacePath", "You're invited to the PacePath beta"), texto, h)


def aviso_registro(correo_: str, nombre: str | None, metodo: str, beta_pendiente: bool) -> None:
    """Al equipo (PACEPATH_CORREO_AVISOS): alguien se registró."""
    para = os.environ.get("PACEPATH_CORREO_AVISOS", "").strip()
    if not para:
        return
    estado = "esperando que lo habilites en la beta" if beta_pendiente else "ya puede usar la app"
    texto, h = _correo("Cuenta nueva", [f"{nombre or 'Alguien'} ({correo_}) creó una cuenta con {metodo}: {estado}."],
                       ("Abrir el panel", f"{_base()}/admin"),
                       motivo="Aviso del equipo de PacePath (PACEPATH_CORREO_AVISOS).")
    enviar(para, f"PacePath · cuenta nueva: {correo_}" + (" (beta pendiente)" if beta_pendiente else ""), texto, h)
