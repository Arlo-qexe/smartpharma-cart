"""Cliente HTTP mínimo para las pruebas del panel: guarda la cookie de sesión como un navegador."""
import json
import urllib.error
import urllib.request


class Cliente:
    """Un navegador mínimo: guarda la cookie de sesión que le da el servidor."""

    def __init__(self, base):
        self.base = base
        self.cookie = None

    def _pedir(self, metodo, ruta, cuerpo=None, cabeceras=None):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else (b"" if metodo == "POST" else None)
        req = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        for k, v in (cabeceras or {}).items():
            req.add_header(k, v)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                estado, cab, crudo = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            estado, cab, crudo = e.code, e.headers, e.read()
        for valor in cab.get_all("Set-Cookie") or []:
            nombre_valor = valor.split(";")[0]
            self.cookie = None if nombre_valor.endswith("=") else nombre_valor
        return estado, (json.loads(crudo) if crudo else {}), cab

    def get(self, ruta):
        e, c, _ = self._pedir("GET", ruta)
        return e, c

    def post(self, ruta, cuerpo=None, cabeceras=None):
        e, c, _ = self._pedir("POST", ruta, cuerpo, cabeceras)
        return e, c

    def post_con_cabeceras(self, ruta, cuerpo=None):
        return self._pedir("POST", ruta, cuerpo)
