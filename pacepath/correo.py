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
import json
import os
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid

from . import idioma
from .idioma import tr


VARIABLES = ("PACEPATH_CORREO_SMTP", "PACEPATH_CORREO_USUARIO", "PACEPATH_CORREO_CLAVE", "PACEPATH_CORREO_DE")


def activo() -> bool:
    return not faltan()


def faltan() -> list[str]:
    """Qué le falta al envío para encenderse (sólo los nombres, nunca los valores)."""
    out = [] if os.environ.get("PACEPATH_CORREO_ACTIVO", "").strip() == "1" else ["PACEPATH_CORREO_ACTIVO=1"]
    return out + [k for k in VARIABLES if not os.environ.get(k, "").strip()]


def enviar(para: str, asunto: str, texto: str, html_: str | None = None, responder_a: str | None = None) -> None:
    if not activo():
        raise RuntimeError("el envío de correos está apagado")
    host, _, puerto = os.environ["PACEPATH_CORREO_SMTP"].partition(":")
    try:
        # Render gratis no deja salir conexiones a los puertos SMTP (25/465/587):
        # con Resend se manda por su API web (HTTPS), con la misma clave.
        if "resend" in host:
            _por_resend(para, asunto, texto, html_, responder_a)
        else:
            _por_smtp(host, int(puerto or 465), para, asunto, texto, html_, responder_a)
    except Exception as exc:
        estado.update(error=f"{type(exc).__name__}: {exc}"[:300], error_ts=time.time())
        print(f"[correo] falló «{asunto}»: {estado['error']}", flush=True)
        raise
    estado.update(ok_ts=time.time(), error=None)
    print(f"[correo] enviado «{asunto}» a …@{para.rpartition('@')[2]} (por {'Resend' if 'resend' in host else host})", flush=True)


# Último envío (en memoria), para mostrarlo en el panel.
estado: dict = {"ok_ts": None, "error": None, "error_ts": None}


def _por_resend(para: str, asunto: str, texto: str, html_: str | None, responder_a: str | None = None) -> None:
    cuerpo = {"from": os.environ["PACEPATH_CORREO_DE"], "to": [para], "subject": asunto, "text": texto}
    if responder_a:
        cuerpo["reply_to"] = responder_a
    if html_:
        cuerpo["html"] = html_
    req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(cuerpo).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {os.environ['PACEPATH_CORREO_CLAVE']}",
                                          "Content-Type": "application/json", "User-Agent": "PacePath/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:300]
        try:
            detalle = json.loads(detalle).get("message", detalle)
        except ValueError:
            pass
        raise RuntimeError(f"Resend respondió {exc.code}: {detalle}") from None


def _por_smtp(host: str, puerto: int, para: str, asunto: str, texto: str, html_: str | None,
              responder_a: str | None = None) -> None:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = os.environ["PACEPATH_CORREO_DE"], para, asunto
    if responder_a:
        m["Reply-To"] = responder_a
    m["Message-ID"] = make_msgid(domain="pacepathapp.com")
    m.set_content(texto)
    if html_:
        m.add_alternative(html_, subtype="html")
    contexto = ssl.create_default_context()
    if puerto in (465, 2465):
        with smtplib.SMTP_SSL(host, puerto, context=contexto, timeout=15) as s:
            s.login(os.environ["PACEPATH_CORREO_USUARIO"], os.environ["PACEPATH_CORREO_CLAVE"])
            s.send_message(m)
    else:
        with smtplib.SMTP(host, puerto, timeout=15) as s:
            s.starttls(context=contexto)
            s.login(os.environ["PACEPATH_CORREO_USUARIO"], os.environ["PACEPATH_CORREO_CLAVE"])
            s.send_message(m)


def en_fondo(fn, *args, lang: str | None = None) -> None:
    """Manda sin hacer esperar al pedido. En `lang` (el idioma de la cuenta:
    es/en) o, si no se sabe, en el del pedido. Un error sólo queda en el log:
    el correo caído no debe romper nada."""
    ctx = contextvars.copy_context()
    if lang:
        ctx.run(idioma.usar, lang)

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
    enlaces = [(tr("Abrir PacePath", "Open PacePath",
                                     "Abrir o PacePath"), b), (tr("Guía beta", "Beta guide",
                                                                            "Guia beta"), f"{b}/beta"),
               (tr("Términos", "Terms",
                               "Termos"), f"{b}/terminos"), (tr("Privacidad", "Privacy",
                                                                             "Privacidade"), f"{b}/privacidad")]
    if (aporte := os.environ.get("PACEPATH_APORTE_URL", "").strip()).startswith("https://"):
        enlaces.append(("☕ " + tr("Apoya PacePath", "Support PacePath",
                                                    "Apoie o PacePath"), aporte))
    return enlaces


def _correo(titulo: str, parrafos: list[str], boton: tuple[str, str] | None = None,
            nota: str | None = None, lista: list[str] | None = None, motivo: str | None = None,
            kpis: list[tuple[str, str]] | None = None) -> tuple[str, str]:
    """(texto plano, HTML). Los párrafos son texto (se escapa); `lista` son
    viñetas; `nota` va chica bajo el botón; `motivo`, al pie, por qué le llega."""
    e = html.escape
    pie = _pie()
    soporte = "support@pacepathapp.com"
    motivo = motivo or tr("Recibes este correo por tu cuenta en PacePath.",
                          "You're getting this email because of your PacePath account.",
                          "Você recebe este e-mail por causa da sua conta no PacePath.")
    # Texto plano
    t = [titulo, ""] + [p + "\n" for p in parrafos]
    t += [f"{k}: {v}" for k, v in (kpis or [])] + ([""] if kpis else [])
    t += [f"  · {x}" for x in (lista or [])] + ([""] if lista else [])
    if boton:
        t += [f"{boton[0]}: {boton[1]}", ""]
    if nota:
        t += [nota, ""]
    t += ["—", "PacePath · " + tr("la ruta perfecta para el entrenamiento de hoy",
                                  "the perfect route for today's workout",
                                  "a rota perfeita para o treino de hoje"),
          " · ".join(f"{n}: {u}" for n, u in pie), motivo,
          tr(f"¿Dudas? Escríbenos a {soporte}", f"Questions? Write to {soporte}",
                                                f"Dúvidas? Escreva para {soporte}")]
    texto = "\n".join(t)
    # HTML (tablas y estilos en línea: es lo que entienden Gmail, Outlook y Apple Mail)
    fuente = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    cuerpo = "".join(f'<p style="margin:0 0 14px;font-size:16px;line-height:1.55;color:{TINTA}">{e(p)}</p>'
                     for p in parrafos)
    if kpis:
        # Fichas de 3 por fila (tablas: es lo que respetan todos los clientes de correo).
        filas = [kpis[i:i + 3] for i in range(0, len(kpis), 3)]
        cuerpo += '<table role="presentation" width="100%" cellpadding="0" cellspacing="6" style="margin:0 -6px 12px">' + "".join(
            "<tr>" + "".join(f'<td width="33%" style="background:{FONDO};border-radius:12px;padding:10px 12px;vertical-align:top">'
                             f'<div style="font-size:11px;font-weight:700;letter-spacing:.3px;text-transform:uppercase;color:{SUAVE}">{e(k)}</div>'
                             f'<div style="font-size:17px;font-weight:800;color:{TINTA};margin-top:2px;white-space:nowrap">{e(v)}</div></td>'  for k, v in fila)
            + "".join('<td width="33%"></td>' for _ in range(3 - len(fila))) + "</tr>" for fila in filas) + "</table>"
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
                   + tr("Si el botón no funciona, copia este enlace:", "If the button doesn't work, copy this link:",
                                                                       "Se o botão não funcionar, copie este link:")
                   + f'<br><a href="{e(boton[1])}" style="color:{SUAVE};word-break:break-all">{e(boton[1])}</a></p>')
    if nota:
        cuerpo += f'<p style="margin:0;font-size:13px;line-height:1.5;color:{SUAVE}">{e(nota)}</p>'
    enlaces = ' <span style="color:#c4c4cc">·</span> '.join(
        f'<a href="{e(u)}" style="color:{SUAVE};text-decoration:underline">{e(n)}</a>' for n, u in pie)
    html_ = f"""<!doctype html><html lang="{tr('es', 'en',
                                                     'pt-BR')}"><head><meta charset="utf-8">
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
  <div>{tr("¿Dudas? Escríbenos a", "Questions? Write to",
                                   "Dúvidas? Escreva para")} <a href="mailto:{soporte}" style="color:{SUAVE}">{soporte}</a></div>
  <div style="margin-top:8px;color:#9a9aa3">{tr("Hecho en Santiago de Chile", "Made in Santiago, Chile",
                                                                              "Feito em Santiago, Chile")} 🇨🇱</div>
</td></tr>
</table></td></tr></table></body></html>"""
    return texto, html_


def _hola(nombre: str | None) -> str:
    return tr(f"Hola, {nombre}:", f"Hi {nombre},",
                                  f"Olá, {nombre}!") if nombre else tr("Hola:", "Hi,",
                                                                              "Olá!")


# ── Los correos ─────────────────────────────────────────────────────────────

def verificar(para: str, nombre: str | None, enlace: str, beta_pendiente: bool = False) -> None:
    parrafos = [_hola(nombre), tr("Confirma tu correo para terminar de crear tu cuenta de PacePath.",
                                  "Confirm your email to finish creating your PacePath account.",
                                  "Confirme seu e-mail para terminar de criar sua conta no PacePath.")]
    if beta_pendiente:
        parrafos.append(tr("PacePath está en beta privada: después de confirmar, tu cuenta queda en la lista de "
                           "espera y te avisamos por correo apenas te habilitemos.",
                           "PacePath is in private beta: after you confirm, your account goes on the waiting "
                           "list and we'll email you as soon as you're in.",
                           "O PacePath está em beta fechado: depois de confirmar, sua conta entra na lista de "
                           "espera e avisamos por e-mail assim que você for liberado."))
    texto, h = _correo(tr("Confirma tu correo", "Confirm your email",
                                                "Confirme seu e-mail"), parrafos,
                       (tr("Confirmar mi correo", "Confirm my email",
                                                  "Confirmar meu e-mail"), enlace),
                       tr("El enlace vale 48 horas. Si no creaste una cuenta, ignora este correo.",
                          "The link is valid for 48 hours. If you didn't create an account, ignore this email.",
                          "O link vale por 48 horas. Se você não criou uma conta, ignore este e-mail."))
    enviar(para, tr("Confirma tu correo en PacePath", "Confirm your email on PacePath",
                                                      "Confirme seu e-mail no PacePath"), texto, h)


def recuperar(para: str, nombre: str | None, enlace: str) -> None:
    texto, h = _correo(tr("Cambia tu clave", "Reset your password",
                                             "Troque sua senha"),
                       [_hola(nombre), tr("Pediste cambiar tu clave de PacePath. Elige una nueva aquí:",
                                          "You asked to reset your PacePath password. Choose a new one here:",
                                          "Você pediu para trocar sua senha do PacePath. Escolha uma nova aqui:")],
                       (tr("Elegir una clave nueva", "Choose a new password",
                                                     "Escolher uma senha nova"), enlace),
                       tr("El enlace vale 1 hora y sirve una sola vez. Si no lo pediste, ignora este correo: tu clave no cambia.",
                          "The link is valid for 1 hour and works once. If you didn't ask for it, ignore this email: "
                          "your password stays the same.",
                          "O link vale por 1 hora e funciona uma vez. Se você não pediu, ignore este e-mail: "
                          "sua senha continua a mesma."))
    enviar(para, tr("Cambia tu clave de PacePath", "Reset your PacePath password",
                                                   "Troque sua senha do PacePath"), texto, h)


def _consejos() -> list[str]:
    return [tr("Escribe tu entrenamiento como lo dirías: «6x1k», «tempo 8k», «fondo 15k plano» o «45 min suave».",
               "Type your workout the way you'd say it: \"6x1k\", \"tempo 8k\", \"long run 15k flat\" or \"45 min easy\".",
               "Escreva seu treino como você falaria: \"6x1k\", \"tempo 8k\", \"longão 15k plano\" ou \"45 min leve\"."),
            tr("PacePath busca calles con pocos semáforos y la distancia justa, desde donde estés.",
               "PacePath finds streets with few traffic lights and the right distance, from wherever you are.",
               "O PacePath encontra ruas com poucos semáforos e a distância certa, de onde você estiver."),
            tr("Conecta Strava para que la ruta prefiera tus calles de siempre y pinta tu ciudad.",
               "Connect Strava so routes prefer your usual streets, and paint your city.",
               "Conecte o Strava para que as rotas prefiram suas ruas de sempre, e pinte sua cidade."),
            tr("Mándala a tu reloj como GPX con «Enviar al reloj».",
               "Send it to your watch as a GPX with \"Send to watch\".",
               "Mande para o relógio como GPX com \"Enviar para o relógio\".")]


def bienvenida(para: str, nombre: str | None, beta_pendiente: bool) -> None:
    """Al quedar registrado (con correo: al confirmarlo; con Google/Microsoft: al crearla)."""
    if beta_pendiente:
        texto, h = _correo(tr("¡Registro exitoso!", "You're registered!",
                                                    "Cadastro feito!"),
                           [_hola(nombre), tr("Tu cuenta de PacePath ya está creada. Estamos en beta privada, así que "
                                              "quedaste en la lista de espera: te escribimos apenas te habilitemos.",
                                              "Your PacePath account is ready. We're in private beta, so you're on "
                                              "the waiting list: we'll write as soon as you're in.",
                                              "Sua conta no PacePath está pronta. Estamos em beta fechado, então você está na "
                                              "lista de espera: escrevemos assim que você for liberado."),
                            tr("Mientras, esto es lo que vas a poder hacer:", "Meanwhile, here's what you'll be able to do:",
                                                                              "Enquanto isso, veja o que você vai poder fazer:")],
                           lista=_consejos(),
                           nota=tr("¿Te invitó alguien? Respóndenos este correo y te habilitamos antes.",
                                   "Did someone invite you? Reply to this email and we'll let you in sooner.",
                                   "Alguém te convidou? Responda a este e-mail e liberamos você antes."))
    else:
        texto, h = _correo(tr("¡Registro exitoso!", "You're registered!",
                                                    "Cadastro feito!"),
                           [_hola(nombre), tr("Bienvenido a PacePath: la ruta perfecta para el entrenamiento de hoy.",
                                              "Welcome to PacePath: the perfect route for today's workout.",
                                              "Boas-vindas ao PacePath: a rota perfeita para o treino de hoje."),
                            tr("Para empezar:", "To get started:",
                                                "Para começar:")],
                           (tr("Abrir PacePath", "Open PacePath",
                                                 "Abrir o PacePath"), _base()), lista=_consejos())
    enviar(para, tr("¡Bienvenido a PacePath!", "Welcome to PacePath!",
                                               "Boas-vindas ao PacePath!"), texto, h)


def acceso_beta(para: str, nombre: str | None, invitado: bool) -> None:
    """Cuando el admin la habilita (o la crea ya habilitada: una invitación)."""
    b = _base()
    if invitado:
        texto, h = _correo(tr("Te invitamos a la beta", "You're invited to the beta",
                                                        "Você foi convidado para o beta"),
                           [_hola(nombre), tr(f"Te habilitamos para probar PacePath antes que nadie. Entra con este "
                                              f"correo ({para}): con Google, si es una cuenta de Google, o creando una clave.",
                                              f"You're in to try PacePath before anyone else. Sign in with this email "
                                              f"({para}): with Google if it's a Google account, or by creating a password.",
                                              f"Você foi liberado para testar o PacePath antes de todo mundo. Entre com este e-mail "
                                              f"({para}): com o Google se for uma conta Google, ou criando uma senha.")],
                           (tr("Entrar a PacePath", "Sign in to PacePath",
                                                    "Entrar no PacePath"), f"{b}/entrar"), lista=_consejos(),
                           motivo=tr("Recibes este correo porque te invitaron a la beta de PacePath.",
                                     "You're getting this email because you were invited to the PacePath beta.",
                                     "Você recebe este e-mail porque foi convidado para o beta do PacePath."))
    else:
        texto, h = _correo(tr("¡Ya tienes acceso!", "You're in!",
                                                    "Você está dentro!"),
                           [_hola(nombre), tr("Te habilitamos en la beta de PacePath. Ya puedes entrar y pedir tu primera ruta.",
                                              "You've been let into the PacePath beta. You can sign in and ask for your first route.",
                                              "Você foi liberado no beta do PacePath. Já pode entrar e pedir sua primeira rota.")],
                           (tr("Abrir PacePath", "Open PacePath",
                                                 "Abrir o PacePath"), b), lista=_consejos(),
                           nota=tr("Estamos afinándola: si algo falla o se te ocurre una mejora, usa «Enviar comentario» en la app.",
                                   "We're still fine-tuning it: if something breaks or you have an idea, use \"Send feedback\" in the app.",
                                   "Ainda estamos ajustando: se algo quebrar ou você tiver uma ideia, use \"Enviar comentário\" no app."))
    enviar(para, tr("Ya puedes entrar a PacePath", "You can now use PacePath",
                                                   "Você já pode usar o PacePath") if not invitado
           else tr("Te invitamos a la beta de PacePath", "You're invited to the PacePath beta",
                                                         "Você foi convidado para o beta do PacePath"), texto, h)


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


def zona_lista(para: str, nombre: str | None, zona: str, lat: float, lon: float) -> None:
    """Pidió una ruta donde aún no había mapa: ya está («preparando tu zona»)."""
    enlace = f"{_base()}/?lat={lat:.5f}&lon={lon:.5f}"
    texto, h = _correo(tr("Tu zona ya tiene mapa", "Your area is ready",
                                                   "Sua região está pronta"),
                       [_hola(nombre), tr(f"Terminamos de preparar el mapa de {zona}. Ya puedes armar tus rutas ahí: "
                                          "la primera vez que alguien corre en una zona nueva hay que construir su mapa, "
                                          "y desde ahora queda listo para ti y para todos.",
                                          f"We finished preparing the map for {zona}. You can now plan your routes "
                                          "there: the first time someone runs in a new area its map has to be built, "
                                          "and from now on it's ready for you and everyone else.",
                                          f"Terminamos de preparar o mapa de {zona}. Você já pode montar suas rotas "
                                          "lá: na primeira vez que alguém corre em uma região nova é preciso construir o mapa, "
                                          "e a partir de agora ele fica pronto para você e para todo mundo.")],
                       (tr("Armar mi ruta", "Plan my route",
                                            "Montar minha rota"), enlace),
                       motivo=tr("Te llega porque pediste una ruta en esa zona.",
                                 "You're getting this because you asked for a route in that area.",
                                 "Você recebe isto porque pediu uma rota nessa região."))
    enviar(para, tr(f"El mapa de {zona} está listo", f"The map for {zona} is ready",
                                                     f"O mapa de {zona} está pronto"), texto, h)


def sesion_entrenador(para: str, de_nombre: str, de_correo: str, entrenador: str | None, e: dict,
                      nota: str | None = None, compara: str | None = None) -> None:
    """Un corredor le manda a su entrenador una sesión de su bitácora: qué hizo
    y sus números. Se responde directo al corredor."""
    from datetime import date

    from . import bitacora
    d = date.fromisoformat(e["fecha"])
    dia = d.strftime("%d/%m/%Y")
    titulo_s = e.get("titulo") or tr("su sesión", "their session", "a sessão")
    turno = {"manana": tr("en la mañana", "in the morning", "de manhã"), "tarde": tr("en la tarde", "in the afternoon", "à tarde"),
             "noche": tr("en la noche", "at night", "à noite")}.get(e.get("turno") or "", "")
    parrafos = [_hola(entrenador),
                tr(f"{de_nombre} realizó «{titulo_s}» el {dia}{' ' + turno if turno else ''} y te comparte cómo le fue.",
                   f"{de_nombre} did «{titulo_s}» on {dia}{' ' + turno if turno else ''} and is sharing how it went.",
                   f"{de_nombre} fez «{titulo_s}» em {dia}{' ' + turno if turno else ''} e compartilha como foi.")]
    lista = []
    for b in e.get("bloques") or []:
        det = [f"{_m(b['obj'][0])}–{_m(b['obj'][1])}" if b.get("obj") and b["obj"][0] != b["obj"][1] else _m(b["obj"][0]) if b.get("obj") else "",
               tr(f"{bitacora._pausa(b['p_rep'])} entre reps", f"{bitacora._pausa(b['p_rep'])} between reps",
                  f"{bitacora._pausa(b['p_rep'])} entre reps") if b.get("p_rep") else "",
               tr(f"{bitacora._pausa(b['p_serie'])} entre series", f"{bitacora._pausa(b['p_serie'])} between sets",
                  f"{bitacora._pausa(b['p_serie'])} entre séries") if b.get("p_serie") and b.get("series", 1) > 1 else ""]
        det = ", ".join(x for x in det if x)
        lista.append(f"{bitacora.estructura(b)}" + (f" ({det})" if det else ""))
        for k, fila in enumerate(b.get("tiempos") or []):
            vals = [x for x in fila or [] if x]
            if vals:
                lista.append((f"S{k + 1}: " if b.get("series", 1) > 1 else "")
                             + " · ".join(f"{round(x)} m" if b.get("dur_s") else bitacora._mmss(x) for x in vals))
        s = bitacora.stats_bloque(b)
        if s:
            partes = [tr("promedio", "average", "média") + " " + (f"{round(s['prom'])} m" if b.get("dur_s") else bitacora._mmss(s["prom"]))]
            if s["ritmo_s_km"]:
                partes.append(f"{bitacora._mmss(s['ritmo_s_km'])}/km")
            if not b.get("dur_s"):
                partes.append(tr("mejor", "best", "melhor") + " " + bitacora._mmss(s["mejor"]))
                partes.append(tr("peor", "worst", "pior") + " " + bitacora._mmss(s["peor"]))
            if s["en_rango"] is not None:
                partes.append(tr(f"{s['en_rango']} de {s['n']} en el rango", f"{s['en_rango']} of {s['n']} on target",
                                 f"{s['en_rango']} de {s['n']} no alvo"))
            lista.append(" · ".join(partes))
    totales = [tr(f"calentamiento {e['cal_km']} km", f"warm-up {e['cal_km']} km", f"aquecimento {e['cal_km']} km") if e.get("cal_km") else "",
               tr(f"vuelta a la calma {e['vc_km']} km", f"cool-down {e['vc_km']} km", f"desaquecimento {e['vc_km']} km") if e.get("vc_km") else ""]
    totales = " · ".join(x for x in totales if x)
    if totales:
        lista.append(totales)
    if compara:
        lista.append("📈 " + compara)
    if e.get("sensaciones"):
        parrafos.append(tr("Sus sensaciones: ", "How it felt: ", "Sensações: ") + f"«{e['sensaciones']}»")
    if nota:
        parrafos.append(tr(f"Nota de {de_nombre}: ", f"Note from {de_nombre}: ", f"Nota de {de_nombre}: ") + nota)
    texto, h = _correo(tr(f"{de_nombre} completó su sesión", f"{de_nombre} completed a session",
                          f"{de_nombre} completou a sessão"), parrafos, lista=lista, kpis=bitacora.kpis(e),
                       nota=tr(f"Responde este correo para escribirle a {de_nombre}.",
                               f"Reply to this email to write to {de_nombre}.",
                               f"Responda este e-mail para escrever para {de_nombre}."),
                       motivo=tr(f"Te llega porque {de_nombre} ({de_correo}) te lo envió desde su bitácora de PacePath.",
                                 f"You're getting this because {de_nombre} ({de_correo}) sent it from their PacePath training log.",
                                 f"Você recebe isto porque {de_nombre} ({de_correo}) enviou do diário de treino no PacePath."))
    enviar(para, tr(f"{de_nombre} · {titulo_s} ({dia})", f"{de_nombre} · {titulo_s} ({dia})", f"{de_nombre} · {titulo_s} ({dia})"),
           texto, h, responder_a=de_correo)


def _m(s: float) -> str:
    from . import bitacora
    return bitacora._mmss(s)


def prueba(para: str) -> None:
    """Desde el panel: comprobar que el envío funciona."""
    texto, h = _correo("Prueba de correo", ["Si lees esto, PacePath ya puede mandar correos. 🎉"],
                       ("Abrir el panel", f"{_base()}/admin"), motivo="Prueba enviada desde el panel de PacePath.")
    enviar(para, "PacePath · prueba de correo", texto, h)