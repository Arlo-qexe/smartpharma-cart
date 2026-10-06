"""
LLM de PRUEBA: **no es un modelo de lenguaje**. Reconoce unas pocas palabras clave y
responde con reglas fijas, con la misma interfaz que `llama_cpp.Llama`
(`create_chat_completion`). Sirve para probar el chat del panel, la confirmación y los
comandos reales sin descargar ningún modelo (modo `ASISTENTE_MODO=prueba`).

Solo ofrece comandos que estén en el schema que recibe, así que respeta lo registrado.
"""
import json
import re
import unicodedata

AYUDA = (
    "Modo de prueba (sin modelo): solo entiendo preguntas sobre las alarmas, el estado "
    "del sistema y las últimas clasificaciones."
)


def _norm(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


class LLMDePrueba:
    def create_chat_completion(self, messages, response_format, temperature=0, max_tokens=0):
        opciones = response_format["schema"]["oneOf"]
        permitidos = {o["properties"]["name"]["const"] for o in opciones if "name" in o["properties"]}
        decision = self._decidir(messages[-1]["content"], permitidos)
        return {"choices": [{"message": {"content": json.dumps(decision, ensure_ascii=False)}}]}

    @staticmethod
    def _decidir(ultimo: str, permitidos: set) -> dict:
        if ultimo.startswith("[resultado de herramienta:"):
            texto = re.sub(r"^\[resultado de herramienta: [^\]]*\]\s*", "", ultimo)
            return {"action": "reply", "text": texto}
        if ultimo.startswith("[error]"):
            return {"action": "reply", "text": "No pude ejecutar eso en modo de prueba."}

        t = _norm(ultimo)
        if "alarma" in t and "listar_alarmas" in permitidos:
            solo_activas = not any(p in t for p in ("todas", "resuelta", "historial"))
            return {"action": "command", "name": "listar_alarmas", "args": {"solo_activas": solo_activas}}
        if re.search(r"ultim|clasific|lote|resultado", t) and "ultima_clasificacion" in permitidos:
            n = re.search(r"\b(10|[1-9])\b", t)
            return {"action": "command", "name": "ultima_clasificacion",
                    "args": {"cantidad": int(n.group(1)) if n else 1}}
        if re.search(r"estado|sistema|como va|que pasa|todo bien", t) and "estado_sistema" in permitidos:
            return {"action": "command", "name": "estado_sistema", "args": {}}
        return {"action": "reply", "text": AYUDA}
