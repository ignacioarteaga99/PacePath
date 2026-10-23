"""Cuentas (correo, Google, Microsoft), beta por usuario y CRUD del panel."""
import pytest
from fastapi.testclient import TestClient

from pacepath import acceso, api, cuentas, oauth, registro
from pacepath.api import app

ADMIN = {"X-Admin-Clave": "clave-larga-de-prueba"}


@pytest.fixture
def base(monkeypatch, tmp_path):
    """Base nueva (como un servidor recién desplegado), beta activa."""
    monkeypatch.setenv("PACEPATH_ADMIN", "clave-larga-de-prueba")
    monkeypatch.delenv("PACEPATH_ACCESO_LIBRE", raising=False)
    for k in ("PACEPATH_CORREO_ACTIVO", "GOOGLE_CLIENT_ID", "MICROSOFT_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    anterior = registro._escritor
    registro.configurar(str(tmp_path / "base.sqlite"))
    acceso.olvidar()
    cuentas._sesiones.clear()
    cuentas._intentos.clear()
    yield
    registro._escritor = anterior
    acceso.olvidar()
    cuentas._sesiones.clear()


def registrar(cli, correo="ana@ejemplo.cl", clave="corre-mucho-2026", nombre="Ana"):
    return cli.post("/api/auth/registro", json={"correo": correo, "clave": clave, "nombre": nombre})


def test_beta_cerrada_por_defecto_y_por_usuario(base):
    cli = TestClient(app)
    r = cli.get("/")
    assert r.status_code == 200 and "Beta privada" in r.text and "window.PP_" not in r.text
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 401
    # Crear cuenta: queda registrada y con sesión, pero sin beta todavía.
    r = registrar(cli)
    assert r.status_code == 200 and r.json()["usuario"]["beta"] is False
    assert cli.get("/api/cuenta").json()["usuario"]["correo"] == "ana@ejemplo.cl"
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 401
    # El admin la habilita: ahora entra.
    uid = cli.get("/api/admin/usuarios", headers=ADMIN).json()[0]["id"]
    assert cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": True}).json()["beta"] is True
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200
    assert "Beta privada" not in cli.get("/").text
    # Beta desactivada: entra cualquier cuenta, también sin beta; sin cuenta, no.
    cli.post("/api/admin/acceso", headers=ADMIN, json={"activo": False})
    sin_beta = TestClient(app)
    registrar(sin_beta, correo="beto@ejemplo.cl")
    assert sin_beta.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200
    assert sin_beta.get("/api/cuenta").json()["pendiente"] is None
    anonimo = TestClient(app)
    assert anonimo.post("/api/sesion", json={"texto": "6x1k"}).status_code == 401
    assert anonimo.get("/api/cuenta").json()["pendiente"] == "sin_sesion"


def test_entrar_salir_y_errores(base):
    cli = TestClient(app)
    assert registrar(cli).status_code == 200
    assert registrar(TestClient(app)).status_code == 409                       # correo repetido
    assert registrar(TestClient(app), correo="otro@ejemplo.cl", clave="corta").status_code == 400
    assert registrar(TestClient(app), correo="no-es-correo").status_code == 400
    otro = TestClient(app)
    r = otro.post("/api/auth/entrar", json={"correo": "ANA@ejemplo.cl ", "clave": "corre-mucho-2026"})
    assert r.status_code == 200 and otro.cookies.get(cuentas.COOKIE)
    assert otro.post("/api/auth/entrar", json={"correo": "ana@ejemplo.cl", "clave": "mala-clave"}).status_code == 401
    otro.post("/api/auth/salir")
    assert otro.get("/api/cuenta").json()["usuario"] is None
    # Muchos intentos fallidos: freno.
    for _ in range(cuentas.MAX_INTENTOS):
        otro.post("/api/auth/entrar", json={"correo": "ana@ejemplo.cl", "clave": "mala-clave"})
    assert otro.post("/api/auth/entrar", json={"correo": "ana@ejemplo.cl", "clave": "corre-mucho-2026"}).status_code == 429


def test_crud_del_panel(base):
    cli = TestClient(app)
    assert cli.get("/api/admin/usuarios").status_code == 404                 # sin la clave de admin
    u = cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "tester@ejemplo.cl", "beta": True}).json()
    assert u["beta"] and u["metodos"] == []                                    # pendiente: aún no entra
    assert cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "tester@ejemplo.cl"}).status_code == 409
    # Editar: nombre y clave nueva; con la clave entra.
    u = cli.patch(f"/api/admin/usuarios/{u['id']}", headers=ADMIN, json={"nombre": "Tester", "clave": "nueva-clave-123"}).json()
    assert u["nombre"] == "Tester" and u["metodos"] == ["clave"]
    t = TestClient(app)
    assert t.post("/api/auth/entrar", json={"correo": "tester@ejemplo.cl", "clave": "nueva-clave-123"}).status_code == 200
    assert t.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200
    # Desactivar: la sesión deja de valer al tiro.
    cli.patch(f"/api/admin/usuarios/{u['id']}", headers=ADMIN, json={"activo": False})
    assert t.post("/api/sesion", json={"texto": "6x1k"}).status_code == 401
    assert t.post("/api/auth/entrar", json={"correo": "tester@ejemplo.cl", "clave": "nueva-clave-123"}).status_code == 401
    # Borrar.
    assert cli.delete(f"/api/admin/usuarios/{u['id']}", headers=ADMIN).json() == {"ok": True}
    assert cli.get("/api/admin/usuarios", headers=ADMIN).json() == []
    assert cli.delete(f"/api/admin/usuarios/{u['id']}", headers=ADMIN).status_code == 404
    # Las claves nunca tal cual en la base.
    assert "nueva-clave-123" not in str(registro.sql("SELECT * FROM pp_usuarios", (), traer=True))


def _vuelta(monkeypatch, datos):
    monkeypatch.setattr(oauth, "configurados", lambda: {"google": True, "microsoft": True})
    monkeypatch.setattr(oauth, "terminar", lambda *a, **k: datos)


def test_google_vincula_por_correo_verificado(base, monkeypatch):
    cli = TestClient(app)
    # El admin habilitó el correo antes de que la persona entrara.
    cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "maria@gmail.com", "beta": True})
    _vuelta(monkeypatch, {"sub": "g-123", "correo": "maria@gmail.com", "nombre": "María", "correo_confiable": True})
    r = cli.get("/api/auth/google/vuelta?code=x&state=y", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/"
    yo = cli.get("/api/cuenta").json()["usuario"]
    assert yo["correo"] == "maria@gmail.com" and yo["beta"] and yo["nombre"] == "María" and "google" in yo["metodos"]
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200


def test_google_quita_la_clave_de_quien_registro_un_correo_ajeno(base, monkeypatch):
    # Alguien registró con clave el correo de otra persona; la dueña entra con Google.
    registrar(TestClient(app), correo="dueña@gmail.com", clave="clave-del-intruso")
    _vuelta(monkeypatch, {"sub": "g-9", "correo": "dueña@gmail.com", "nombre": None, "correo_confiable": True})
    TestClient(app).get("/api/auth/google/vuelta?code=x&state=y", follow_redirects=False)
    intruso = TestClient(app)
    assert intruso.post("/api/auth/entrar", json={"correo": "dueña@gmail.com", "clave": "clave-del-intruso"}).status_code == 401


def test_microsoft_de_empresa_no_vincula_por_correo(base, monkeypatch):
    cli = TestClient(app)
    cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "jefe@ejemplo.cl", "beta": True})
    # Cuenta de empresa: el correo lo pone su administrador (no es confiable).
    _vuelta(monkeypatch, {"sub": "m-1", "correo": "jefe@ejemplo.cl", "nombre": "X", "correo_confiable": False})
    r = cli.get("/api/auth/microsoft/vuelta?code=x&state=y", follow_redirects=False)
    assert r.headers["location"] == "/?cuenta=correo_existe_otro_metodo"
    assert cli.get("/api/cuenta").json()["usuario"] is None
    # Un correo nuevo sí crea la cuenta (sin beta), y se reconoce por su id de Microsoft.
    _vuelta(monkeypatch, {"sub": "m-2", "correo": "nuevo@empresa.cl", "nombre": "Nuevo", "correo_confiable": False})
    cli.get("/api/auth/microsoft/vuelta?code=x&state=y", follow_redirects=False)
    assert cli.get("/api/cuenta").json()["usuario"]["correo"] == "nuevo@empresa.cl"


def test_estado_oauth_y_paginas_abiertas(base, monkeypatch):
    cli = TestClient(app)
    for ruta in ("/entrar", "/admin", "/privacidad", "/api/salud", "/api/cuenta", "/api/auth/proveedores"):
        assert cli.get(ruta).status_code == 200, ruta
    assert cli.get("/api/auth/google", follow_redirects=False).status_code == 503   # sin credenciales
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secreto")
    r = cli.get("/api/auth/google", follow_redirects=False)
    assert r.status_code == 307 and "accounts.google.com" in r.headers["location"] and "code_challenge" in r.headers["location"]
    estado = cli.cookies.get(oauth.COOKIE_ESTADO)
    assert estado and estado.startswith("google.")
    with pytest.raises(oauth.ErrorOAuth):                                     # estado que no coincide
        oauth.terminar("google", "code", "otro-estado", estado, "http://x/vuelta")


def test_correo_apagado_por_defecto(base):
    cli = TestClient(app)
    registrar(cli)
    c = cli.get("/api/cuenta").json()
    assert c["correo_activo"] is False and c["pendiente"] == "beta"           # no se pide verificar
    assert cli.post("/api/auth/recuperar", json={"correo": "ana@ejemplo.cl"}).status_code == 503


@pytest.fixture
def con_correo(base, monkeypatch):
    for k, v in {"PACEPATH_CORREO_ACTIVO": "1", "PACEPATH_CORREO_SMTP": "smtp.x:465", "PACEPATH_CORREO_USUARIO": "u",
                 "PACEPATH_CORREO_CLAVE": "c", "PACEPATH_CORREO_DE": "PacePath <support@pacepathapp.com>"}.items():
        monkeypatch.setenv(k, v)
    enviados = []
    from pacepath import correo
    monkeypatch.setattr(correo, "enviar", lambda para, asunto, texto, html_=None: enviados.append((para, asunto, texto)))
    import contextvars
    from pacepath import idioma

    def al_tiro(fn, *a, lang=None):                                          # sin hilos: para revisarlo al tiro
        ctx = contextvars.copy_context()
        if lang:
            ctx.run(idioma.usar, lang)
        ctx.run(fn, *a)
    monkeypatch.setattr(correo, "en_fondo", al_tiro)
    return enviados


def _enlace(texto):
    import re
    return re.search(r"https?://\S+", texto).group(0)


def test_verificar_correo(con_correo):
    cli = TestClient(app)
    cli.post("/api/admin/acceso", headers=ADMIN, json={"activo": False})
    registrar(cli)
    assert cli.get("/api/cuenta").json()["pendiente"] == "verificar"
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 401
    para, asunto, texto = con_correo[-1]
    assert para == "ana@ejemplo.cl" and "Confirma" in asunto
    r = cli.get(_enlace(texto).replace("http://testserver", ""), follow_redirects=False)
    assert r.headers["location"] == "/?cuenta=verificado"
    assert cli.get("/api/cuenta").json()["pendiente"] is None
    assert cli.get(_enlace(texto).replace("http://testserver", ""), follow_redirects=False).headers["location"] == "/?cuenta=codigo_invalido"


def test_recuperar_clave(con_correo):
    cli = TestClient(app)
    registrar(cli)
    assert cli.post("/api/auth/recuperar", json={"correo": "nadie@ejemplo.cl"}).json() == {"ok": True}   # no delata
    n = len(con_correo)
    assert cli.post("/api/auth/recuperar", json={"correo": "ana@ejemplo.cl"}).json() == {"ok": True}
    assert len(con_correo) == n + 1
    codigo = _enlace(con_correo[-1][2]).split("recuperar=")[1]
    otro = TestClient(app)
    assert otro.post("/api/auth/nueva-clave", json={"codigo": codigo, "clave": "corta"}).status_code == 400
    assert otro.post("/api/auth/nueva-clave", json={"codigo": codigo, "clave": "clave-nueva-2026"}).status_code == 200
    assert otro.post("/api/auth/nueva-clave", json={"codigo": codigo, "clave": "otra-clave-2026"}).status_code == 400   # se usa una vez
    assert cli.get("/api/cuenta").json()["usuario"] is None                    # la sesión vieja se cerró
    assert TestClient(app).post("/api/auth/entrar", json={"correo": "ana@ejemplo.cl", "clave": "clave-nueva-2026"}).status_code == 200


def test_consumo_por_cuenta(base):
    """Cada consulta guarda su cuenta y sus bytes; el teléfono informa las teselas;
    el panel ve el tamaño de la base y quién es cada cuenta. Al borrar la cuenta se desliga."""
    cli = TestClient(app)
    u = cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "tester@ejemplo.cl", "beta": True,
                                                              "clave": "nueva-clave-123"}).json()
    cli.post("/api/auth/entrar", json={"correo": "tester@ejemplo.cl", "clave": "nueva-clave-123"})
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200
    r = cli.post("/api/consumo", json={"mapas": [{"tipo": "mapa_propio", "teselas": 120, "bytes": 3_600_000},
                                                 {"tipo": "esri", "teselas": 40, "bytes": 1_000_000, "estimado": True},
                                                 {"tipo": "raro", "teselas": 5, "bytes": 10}]})
    assert r.json() == {"ok": True}
    assert TestClient(app).post("/api/consumo", json={"mapas": []}).status_code == 401   # sin sesión no
    registro.vaciar()
    j = cli.get("/api/admin/eventos", headers=ADMIN).json()
    filas = [dict(zip(j["columnas"], f)) for f in j["filas"]]
    sesion = next(f for f in filas if f["consulta"] == "interpretar")
    assert sesion["cuenta"] == u["id"] and sesion["bytes_out"] > 0 and sesion["bytes_in"] > 0
    mapas = {f["tipo"]: f for f in filas if f["consulta"] == "consumo_mapa"}
    assert set(mapas) == {"mapa_propio", "esri"}
    assert mapas["mapa_propio"]["externas"] == 120 and mapas["mapa_propio"]["bytes_out"] == 3_600_000
    assert mapas["esri"]["detalle"] == "estimado" and mapas["esri"]["cuenta"] == u["id"]

    assert cli.get("/api/admin/consumo").status_code == 404
    c = cli.get("/api/admin/consumo", headers=ADMIN).json()
    assert c["cuentas"] == {u["id"]: "tester@ejemplo.cl"}
    assert c["base"]["motor"] == "sqlite" and c["base"]["total_bytes"] > 0
    assert any(t["tabla"] == "pp_eventos" and t["filas"] >= 3 for t in c["base"]["tablas"])

    cli.delete(f"/api/admin/usuarios/{u['id']}", headers=ADMIN)
    assert registro.sql("SELECT COUNT(*) FROM pp_eventos WHERE cuenta = ?", (u["id"],), traer=True)[0][0] == 0


def test_con_beta_cerrada_el_registro_avisa_de_la_lista_de_espera(con_correo, monkeypatch):
    """Registro con correo y la beta cerrada: el correo de confirmar y la pantalla
    dicen que queda en lista de espera; al equipo le llega el aviso; al confirmar,
    la bienvenida (en espera); al habilitarla, «ya tienes acceso»."""
    monkeypatch.setenv("PACEPATH_CORREO_AVISOS", "support@pacepathapp.com")
    cli = TestClient(app)
    registrar(cli)
    c = cli.get("/api/cuenta").json()
    assert c["pendiente"] == "verificar" and c["beta_cerrada"] and not c["usuario"]["beta"]
    (p1, a1, t1), (p2, a2, t2) = con_correo
    assert p1 == "ana@ejemplo.cl" and "Confirma" in a1 and "lista de espera" in t1
    assert p2 == "support@pacepathapp.com" and "ana@ejemplo.cl" in a2 and "beta pendiente" in a2
    cli.get(_enlace(t1).replace("http://testserver", ""), follow_redirects=False)
    para, asunto, texto = con_correo[-1]
    assert para == "ana@ejemplo.cl" and "Bienvenido" in asunto and "lista de espera" in texto
    assert cli.get("/api/cuenta").json()["pendiente"] == "beta"
    uid = c["usuario"]["id"]
    n = len(con_correo)
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": True})
    assert len(con_correo) == n + 1 and "Ya puedes entrar" in con_correo[-1][1]
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"nombre": "Ana B."})   # ya estaba: no repite
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": False})
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": True, "avisar": False})
    assert len(con_correo) == n + 1


def test_invitacion_y_bienvenida_con_google(con_correo, monkeypatch):
    cli = TestClient(app)
    cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "invitada@ejemplo.cl", "beta": True})
    assert con_correo[-1][0] == "invitada@ejemplo.cl" and "invitamos" in con_correo[-1][1]
    n = len(con_correo)
    cli.post("/api/admin/usuarios", headers=ADMIN, json={"correo": "otra@ejemplo.cl", "beta": True, "avisar": False})
    assert len(con_correo) == n
    # Cuenta nueva con Google (verificada): bienvenida al tiro.
    _vuelta(monkeypatch, {"sub": "g-9", "correo": "pedro@gmail.com", "nombre": "Pedro", "correo_confiable": True})
    cli.get("/api/auth/google/vuelta?code=x&state=y", follow_redirects=False)
    assert con_correo[-1][0] == "pedro@gmail.com" and "Bienvenido" in con_correo[-1][1]
    n = len(con_correo)
    TestClient(app).get("/api/auth/google/vuelta?code=x&state=y", follow_redirects=False)   # vuelve: ya no es nueva
    assert len(con_correo) == n


def test_plantilla_de_correo(monkeypatch):
    from pacepath import correo
    monkeypatch.setenv("PACEPATH_APORTE_URL", "https://buymeacoffee.com/pacepath")
    texto, html = correo._correo("¡Registro exitoso!", ["Hola, <Ana>:"], ("Abrir PacePath", "https://pacepathapp.com"))
    assert "&lt;Ana&gt;" in html and "<Ana>" not in html                     # escapa lo del usuario
    for enlace in ("/terminos", "/privacidad", "/beta", "buymeacoffee.com/pacepath", "mailto:support@pacepathapp.com"):
        assert enlace in html
    assert "Apoya PacePath: https://buymeacoffee.com/pacepath" in texto
    monkeypatch.delenv("PACEPATH_APORTE_URL")
    assert "Apoya" not in correo._correo("x", ["y"])[1]


def test_resend_por_https_y_prueba_desde_el_panel(base, monkeypatch):
    """Con Resend se manda por su API (Render gratis bloquea SMTP); si falla,
    el panel muestra el error."""
    import io, json as _json, urllib.error
    from pacepath import correo
    for k, v in {"PACEPATH_CORREO_ACTIVO": "1", "PACEPATH_CORREO_SMTP": "smtp.resend.com:465", "PACEPATH_CORREO_USUARIO": "resend",
                 "PACEPATH_CORREO_CLAVE": "re_x", "PACEPATH_CORREO_DE": "PacePath <support@pacepathapp.com>"}.items():
        monkeypatch.setenv(k, v)
    pedidos = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def urlopen(req, timeout):
        pedidos.append(req)
        if _json.loads(req.data)["to"] == ["mal@ejemplo.cl"]:
            raise urllib.error.HTTPError(req.full_url, 403, "x", {}, io.BytesIO(b'{"message":"The pacepathapp.com domain is not verified"}'))
        return R(b'{"id":"1"}')
    monkeypatch.setattr(correo.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(correo.smtplib, "SMTP_SSL", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no SMTP")))
    cli = TestClient(app)
    assert cli.post("/api/admin/correo/prueba", json={"para": "yo@ejemplo.cl"}).status_code == 404
    assert cli.post("/api/admin/correo/prueba", headers=ADMIN, json={"para": "yo@ejemplo.cl"}).json() == {"ok": True}
    req = pedidos[-1]
    cuerpo = _json.loads(req.data)
    assert req.full_url == "https://api.resend.com/emails" and req.headers["Authorization"] == "Bearer re_x"
    assert cuerpo["from"] == "PacePath <support@pacepathapp.com>" and "<html" in cuerpo["html"] and cuerpo["text"]
    r = cli.post("/api/admin/correo/prueba", headers=ADMIN, json={"para": "mal@ejemplo.cl"})
    assert r.status_code == 502 and "not verified" in r.json()["detail"]["mensaje"]
    assert "not verified" in cli.get("/api/admin/acceso", headers=ADMIN).json()["correo_estado"]["error"]


def test_correos_en_el_idioma_de_la_cuenta(con_correo, monkeypatch):
    """Quien se registra en inglés recibe todo en inglés (también lo que llega
    después: la bienvenida al tocar el enlace y el acceso que da el panel en
    español); el aviso al equipo, en español."""
    monkeypatch.setenv("PACEPATH_CORREO_AVISOS", "support@pacepathapp.com")
    cli = TestClient(app)
    cli.post("/api/auth/registro", headers={"X-Idioma": "en"},
             json={"correo": "john@example.com", "clave": "runs-a-lot-2026", "nombre": "John"})
    (_, a1, t1), (p2, a2, _) = con_correo
    assert a1 == "Confirm your email on PacePath" and "waiting list" in t1
    assert p2 == "support@pacepathapp.com" and a2.startswith("PacePath · cuenta nueva")
    TestClient(app).get(_enlace(t1).replace("http://testserver", ""), follow_redirects=False)   # otro navegador, sin idioma
    assert con_correo[-1][1] == "Welcome to PacePath!" and "Terms" in con_correo[-1][2]
    uid = cli.get("/api/cuenta").json()["usuario"]["id"]
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": True})
    assert con_correo[-1][1] == "You can now use PacePath"
    # Cambia la app a español: lo que siga, en español.
    cli.get("/api/cuenta", headers={"X-Idioma": "es"})
    cli.post("/api/auth/recuperar", json={"correo": "john@example.com"})          # pedido sin idioma: español
    assert con_correo[-1][1] == "Cambia tu clave de PacePath"
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": False})
    cli.patch(f"/api/admin/usuarios/{uid}", headers=ADMIN, json={"beta": True})
    assert con_correo[-1][1] == "Ya puedes entrar a PacePath"
    # Google desde la página en inglés: la vuelta trae el idioma por cookie.
    _vuelta(monkeypatch, {"sub": "g-7", "correo": "mary@gmail.com", "nombre": "Mary", "correo_confiable": True})
    monkeypatch.setattr(oauth, "iniciar", lambda p, v, c=None: ("https://accounts.google.com/x", "estado"))
    g = TestClient(app)
    g.get("/api/auth/google?lang=en", follow_redirects=False)
    g.get("/api/auth/google/vuelta?code=x&state=y", follow_redirects=False)
    assert [a for p, a, _ in con_correo if p == "mary@gmail.com"] == ["Welcome to PacePath!"]


def test_cuenta_de_google_con_clave_dice_que_entre_con_google(base, monkeypatch):
    """La clave de Gmail nunca llega a PacePath: quien escribe su correo de una
    cuenta creada con Google recibe el aviso (y el botón lleva su correo)."""
    cli = TestClient(app)
    _vuelta(monkeypatch, {"sub": "g-55", "correo": "luz@gmail.com", "nombre": "Luz", "correo_confiable": True})
    cli.get("/api/auth/google/vuelta?code=x&state=y", follow_redirects=False)
    otro = TestClient(app)
    r = otro.post("/api/auth/entrar", json={"correo": "Luz@gmail.com", "clave": "mi-clave-de-gmail"})
    assert r.status_code == 401 and r.json()["detail"]["codigo"] == "usa_google"
    assert "Continuar con Google" in r.json()["detail"]["mensaje"]
    assert not cuentas._intentos.get("luz@gmail.com")                 # no cuenta como intento fallido
    # El botón de Google llega con el correo escrito (login_hint).
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-de-prueba")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secreto")
    ir = otro.get("/api/auth/google?lang=es&correo=luz@gmail.com", follow_redirects=False)
    assert ir.status_code == 307 and "login_hint=luz%40gmail.com" in ir.headers["location"]
    assert "login_hint" not in otro.get("/api/auth/google?correo=no-es-correo", follow_redirects=False).headers["location"]
    # Si se crea una clave propia (desde el panel o «¿Olvidaste tu clave?»), entra con las dos.
    uid = cuentas.por_correo("luz@gmail.com")["id"]
    cuentas.actualizar(uid, clave="clave-de-pacepath")
    assert otro.post("/api/auth/entrar", json={"correo": "luz@gmail.com", "clave": "clave-de-pacepath"}).status_code == 200
