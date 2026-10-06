"""
assistant.py - offline command assistant (LLM -> validated tool calls).

Capa de asistente conversacional para el operador del panel de control en la PC
del SmartPharma Cart. El LLM nunca ejecuta nada directamente: solo devuelve JSON
que es o bien
    {"action": "reply",   "text": "..."}
    {"action": "command", "name": "<registered name>", "args": {...}}
y este módulo valida ese JSON y lo ejecuta a través de funciones Python registradas.

Instalación:
    pip install llama-cpp-python pydantic
    # Build con GPU NVIDIA:
    #   CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --no-cache-dir

Notas:
- Los parámetros de cada comando deben ser PLANOS (str/int/float/bool/Literal). Modelos
  pydantic anidados generan $ref/$defs en el schema, lo cual puede romper la generación
  por gramática en llama.cpp.
- Uso desde la GUI: llama a handle()/confirm() desde un hilo de trabajo (worker thread),
  nunca desde el hilo de la interfaz.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError

log = logging.getLogger("assistant")


# --------------------------------------------------------------------------
# Registro de comandos
# --------------------------------------------------------------------------
class NoArgs(BaseModel):
    pass


@dataclass
class Command:
    name: str
    description: str
    params: type[BaseModel]
    func: Callable[..., Any]
    needs_confirm: bool
    examples: list[str]


REGISTRY: dict[str, Command] = {}


def command(
    name: str,
    description: str,
    params: type[BaseModel] | None = None,
    needs_confirm: bool = False,
    examples: tuple[str, ...] = (),
):
    """Registra una función como comando invocable por el asistente.

    needs_confirm=True  -> el operador debe confirmar con un botón antes de ejecutar
    needs_confirm=False -> de solo lectura; se ejecuta de inmediato y el resultado
                            vuelve al modelo
    """

    def deco(func: Callable[..., Any]):
        REGISTRY[name] = Command(
            name, description, params or NoArgs, func, needs_confirm, list(examples)
        )
        return func

    return deco


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.lower()))


def select_commands(query: str, max_n: int = 8) -> list[Command]:
    """Mantiene el prompt pequeño: solo se ofrecen al modelo los comandos más relevantes."""
    cmds = list(REGISTRY.values())
    if len(cmds) <= max_n:
        return cmds
    q = _tokens(query)

    def score(c: Command) -> int:
        text = f"{c.name.replace('_', ' ')} {c.description} {' '.join(c.examples)}"
        return len(q & _tokens(text))

    return sorted(cmds, key=score, reverse=True)[:max_n]


# --------------------------------------------------------------------------
# Construcción de schema + prompt
# --------------------------------------------------------------------------
def _strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(x) for x in schema]
    return schema


def build_schema(cmds: list[Command]) -> dict:
    """oneOf: una respuesta de texto plano, o exactamente uno de los comandos ofrecidos."""
    options: list[dict] = [
        {
            "type": "object",
            "properties": {"action": {"const": "reply"}, "text": {"type": "string"}},
            "required": ["action", "text"],
        }
    ]
    for c in cmds:
        options.append(
            {
                "type": "object",
                "properties": {
                    "action": {"const": "command"},
                    "name": {"const": c.name},
                    "args": _strip_titles(c.params.model_json_schema()),
                },
                "required": ["action", "name", "args"],
            }
        )
    return {"oneOf": options}


def _signature(c: Command) -> str:
    schema = c.params.model_json_schema()
    required = set(schema.get("required", []))
    parts = []
    for k, v in schema.get("properties", {}).items():
        if "enum" in v:
            t = "|".join(map(str, v["enum"]))
        else:
            t = v.get("type", "any")
        parts.append(f"{k}: {t}{'' if k in required else '?'}")
    return f"{c.name}({', '.join(parts)})"


SYSTEM_TEMPLATE = """Eres el asistente del operador dentro del panel de control del SmartPharma Cart.
El carro fotografía varias caras de cada caja de medicamento; la PC extrae la fecha de
vencimiento y luego el manipulador esférico clasifica la caja según la regla FEFO
(First-Expired, First-Out), o levanta una alarma si corresponde revisión manual. Ayudas
al operador a manejar el programa: o bien ejecutas un comando, o respondes en texto.

Reglas:
- Responde SOLO con JSON que cumpla el schema dado.
- Usa action "command" cuando el operador pide hacer algo que coincide con un comando.
- Usa action "reply" para preguntas, explicaciones de uso, aclaraciones, o si ningún
  comando aplica.
- Si falta un argumento requerido o no es claro, pregunta al operador con un reply. Nunca
  inventes IDs de caja, IDs de alarma ni fechas.
- NO lees ni juzgas fechas de vencimiento por tu cuenta; reporta lo que devuelve el sistema.
- Mantén las respuestas breves.

Comandos disponibles:
{commands}

Estado actual de la aplicación:
{state}
{docs}"""


# --------------------------------------------------------------------------
# Recuperación de documentación (keyword match simple; cambiar a embeddings si hace falta)
# --------------------------------------------------------------------------
def make_docs_retriever(md_path: str, k: int = 3) -> Callable[[str], list[str]]:
    text = Path(md_path).read_text(encoding="utf-8")
    chunks = [c.strip() for c in re.split(r"\n(?=#{1,3} )", text) if c.strip()]
    chunk_tokens = [_tokens(c) for c in chunks]

    def retrieve(query: str) -> list[str]:
        q = _tokens(query)
        scored = sorted(
            ((len(q & t), c) for t, c in zip(chunk_tokens, chunks)),
            key=lambda x: x[0],
            reverse=True,
        )
        return [c[:800] for s, c in scored[:k] if s > 0]

    return retrieve


# --------------------------------------------------------------------------
# Resultados devueltos a la GUI
# --------------------------------------------------------------------------
@dataclass
class Reply:
    text: str


@dataclass
class Proposal:
    """Un comando esperando confirmación del operador."""

    name: str
    args: BaseModel
    summary: str


# --------------------------------------------------------------------------
# El asistente
# --------------------------------------------------------------------------
class Assistant:
    def __init__(
        self,
        model_path: str | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,  # -1 = todas las capas en GPU; 0 para forzar CPU
        max_steps: int = 3,
        docs_retriever: Optional[Callable[[str], list[str]]] = None,
        history_messages: int = 12,
        llm: Any = None,
    ):
        """`llm` permite inyectar un modelo ya construido (o uno falso en las pruebas):
        solo necesita `create_chat_completion(messages, response_format, temperature,
        max_tokens)` y devolver el mismo dict que llama-cpp-python. Si no se da, se
        carga `model_path` con llama-cpp-python."""
        if llm is not None:
            self.llm = llm
        else:
            if model_path is None:
                raise ValueError("Se necesita `model_path` o un `llm` ya construido.")
            from llama_cpp import Llama  # import local para que el módulo cargue sin la lib

            self.llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )
        self.max_steps = max_steps
        self.docs_retriever = docs_retriever
        self.history_messages = history_messages
        self.history: list[dict] = []
        self._last_user_text = ""

    # ---- API pública -------------------------------------------------------
    def handle(self, user_text: str, app_state: dict | None = None) -> Reply | Proposal:
        self._last_user_text = user_text
        self.history.append({"role": "user", "content": user_text})
        return self._loop(app_state or {})

    def confirm(self, proposal: Proposal, app_state: dict | None = None) -> Reply | Proposal:
        """El operador presionó 'Ejecutar': corre el comando, devuelve el resultado al modelo."""
        result = self._run(REGISTRY[proposal.name], proposal.args)
        self.history.append(
            {"role": "user", "content": f"[resultado de herramienta: {proposal.name}] {result[:1500]}"}
        )
        return self._loop(app_state or {})

    def cancel(self, proposal: Proposal) -> Reply:
        self.history.append(
            {"role": "user", "content": f"[operador canceló {proposal.name}]"}
        )
        return Reply("Cancelado.")

    # ---- internos ---------------------------------------------------------
    def _system_prompt(self, cmds: list[Command], app_state: dict) -> str:
        lines = []
        for c in cmds:
            tag = " [pide confirmación]" if c.needs_confirm else ""
            lines.append(f"- {_signature(c)}{tag}: {c.description}")
            for ex in c.examples[:2]:
                lines.append(f'    ej. "{ex}"')
        docs = ""
        if self.docs_retriever:
            chunks = self.docs_retriever(self._last_user_text)
            if chunks:
                docs = "\nExtractos del manual:\n" + "\n---\n".join(chunks)
        return SYSTEM_TEMPLATE.format(
            commands="\n".join(lines),
            state=json.dumps(app_state, ensure_ascii=False),
            docs=docs,
        )

    def _ask(self, system: str, schema: dict) -> dict:
        messages = [{"role": "system", "content": system}] + self.history[
            -self.history_messages :
        ]
        out = self.llm.create_chat_completion(
            messages=messages,
            response_format={"type": "json_object", "schema": schema},
            temperature=0.1,
            max_tokens=300,
        )
        text = out["choices"][0]["message"]["content"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.error("El modelo devolvió JSON inválido: %r", text)
            return {"action": "reply", "text": "No pude procesar eso. ¿Puedes reformularlo?"}

    def _run(self, cmd: Command, args: BaseModel) -> str:
        log.info("EJECUTA %s %s", cmd.name, args.model_dump())
        try:
            result = cmd.func(**args.model_dump())
            return "OK" if result is None else str(result)
        except Exception as e:  # se reporta al modelo/operador en vez de tumbar la app
            log.exception("Comando %s falló", cmd.name)
            return f"ERROR: {e}"

    def _loop(self, app_state: dict) -> Reply | Proposal:
        cmds = select_commands(self._last_user_text)
        schema = build_schema(cmds)
        system = self._system_prompt(cmds, app_state)

        for _ in range(self.max_steps):
            data = self._ask(system, schema)
            self.history.append(
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}
            )

            if data.get("action") == "reply":
                return Reply(data.get("text", ""))

            cmd = REGISTRY.get(data.get("name", ""))
            if cmd is None:
                self.history.append({"role": "user", "content": "[error] comando desconocido"})
                continue

            try:
                args = cmd.params(**data.get("args", {}))
            except ValidationError as e:
                self.history.append(
                    {"role": "user", "content": f"[error] argumentos inválidos para {cmd.name}: {e.errors()}"}
                )
                continue

            if cmd.needs_confirm:
                return Proposal(cmd.name, args, f"{cmd.name}({args.model_dump()})")

            # de solo lectura: se ejecuta ya y se deja que el modelo resuma / encadene
            result = self._run(cmd, args)
            self.history.append(
                {"role": "user", "content": f"[resultado de herramienta: {cmd.name}] {result[:1500]}"}
            )

        return Reply("No pude completar eso. ¿Puedes reformular o dar más detalle?")
