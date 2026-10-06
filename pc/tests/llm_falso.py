"""LLM falso para las pruebas del asistente: imita `Llama.create_chat_completion`
con respuestas programadas (compartido por test_assistant.py y test_asistente_panel.py)."""
import json


class LLMFalso:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def create_chat_completion(self, messages, response_format, temperature, max_tokens):
        self.llamadas.append({"messages": messages, "schema": response_format["schema"]})
        r = self.respuestas.pop(0)
        texto = r if isinstance(r, str) else json.dumps(r)
        return {"choices": [{"message": {"content": texto}}]}


def reply(texto):
    return {"action": "reply", "text": texto}


def cmd(nombre, **args):
    return {"action": "command", "name": nombre, "args": args}
