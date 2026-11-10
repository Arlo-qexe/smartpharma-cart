"""Render por GPU (OpenGL 3.3 sin ventana, vía EGL o GLFW) del modelo CAD con la malla fina de datos/malla_gpu.npz.

Frente al rasterizador por software de modelo3d.py: z-buffer real (sin artefactos de orden), normales suaves con aristas vivas, malla sin decimar
(~1.2 M triángulos, fiel al STEP), antialiasing MSAA y ~100× más rápido (unos pocos ms por fotograma). Solo necesita PyOpenGL y un driver
OpenGL/EGL (Mesa funciona); si algo falla, `RenderGL.crear()` devuelve None y se usa el render por software.
"""
import ctypes
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform != "win32" and os.environ.get("SPM_GL") != "glfw":            # Linux: EGL sin ventana; en Windows PyOpenGL usa WGL con el contexto de GLFW
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
import numpy as np

from .geometria import RUTA_DATOS
from .modelo3d import _color, GRUPOS

FONDO = (0x0d / 255, 0x11 / 255, 0x17 / 255)

_VS = """#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNor;
uniform mat4 uModel; uniform mat4 uView; uniform mat4 uProj;
out vec3 vN; out vec3 vV;
void main(){
    vec4 vp = uView * (uModel * vec4(aPos, 1.0));
    vV = vp.xyz;
    vN = mat3(uView) * (mat3(uModel) * aNor);
    gl_Position = uProj * vp;
}
"""
_FS = """#version 330 core
in vec3 vN; in vec3 vV;
uniform vec3 uColor; uniform float uAlpha;
out vec4 frag;
void main(){
    vec3 N = normalize(vN); vec3 V = normalize(-vV);
    if (dot(N, V) < 0.0) N = -N;                               // iluminación a dos caras
    vec3 L1 = normalize(vec3(-0.45, 0.60, 0.65));              // luces fijas a la cámara
    vec3 L2 = normalize(vec3( 0.65,-0.15, 0.50));
    vec3 L3 = normalize(vec3( 0.00,-0.55,-0.80));
    float d = 0.55*max(dot(N,L1),0.0) + 0.26*max(dot(N,L2),0.0) + 0.10*max(dot(N,L3),0.0);
    float hemi = 0.5 + 0.5*N.y;
    float s = pow(max(dot(N, normalize(L1+V)), 0.0), 48.0) * 0.22;
    vec3 c = uColor * (0.26 + 0.20*hemi + d) + vec3(s);
    frag = vec4(c, uAlpha);
}
"""


def _perspectiva(fovy, aspecto, cerca, lejos):
    f = 1.0 / math.tan(fovy / 2)
    return np.array([[f / aspecto, 0, 0, 0], [0, f, 0, 0], [0, 0, (lejos + cerca) / (cerca - lejos), 2 * lejos * cerca / (cerca - lejos)], [0, 0, -1, 0]], np.float32)


def _mira(ojo, objetivo, arriba=(0, 0, 1)):
    f = objetivo - ojo; f /= np.linalg.norm(f)
    s = np.cross(f, arriba); s /= np.linalg.norm(s)
    u = np.cross(s, f)
    M = np.eye(4, dtype=np.float32); M[0, :3], M[1, :3], M[2, :3] = s, u, -f
    M[:3, 3] = -M[:3, :3] @ ojo
    return M


class RenderGL:
    @classmethod
    def crear(cls, spm, **kw):
        """RenderGL o None si no hay OpenGL/EGL, PyOpenGL o la malla fina."""
        try:
            return cls(spm, **kw)
        except Exception as e:                                           # sin driver, sin PyOpenGL, sin datos...
            cls.ultimo_error = "%s: %s" % (type(e).__name__, e)
            return None

    def __init__(self, spm, ruta=None, msaa=4):
        from OpenGL import GL
        self.GL = GL
        self.EGL = None; self.ventana = None
        self.spm = spm; self.O = spm.g.O
        ruta = Path(ruta) if ruta else RUTA_DATOS / "malla_gpu.npz"
        if not ruta.exists():
            raise FileNotFoundError("falta %s (herramientas/exportar_malla_gpu.py)" % ruta)
        self._contexto()
        self._programa()
        self._cargar(ruta)
        self.msaa = min(msaa, int(GL.glGetIntegerv(GL.GL_MAX_SAMPLES)))
        self._tam = None
        self.renderer = GL.glGetString(GL.GL_RENDERER).decode()

    # ------------------------------------------------------------------ contexto, shaders, datos
    def _contexto(self):
        """Contexto OpenGL 3.3 core sin ventana visible: EGL (Linux/Mesa) y, si no, GLFW con una ventana oculta (Windows/macOS; SPM_GL=glfw lo fuerza)."""
        errores = []
        if sys.platform != "win32" and os.environ.get("SPM_GL") != "glfw":
            try:
                return self._contexto_egl()
            except Exception as e:
                errores.append("EGL: %s" % e)
        try:
            return self._contexto_glfw()
        except Exception as e:
            errores.append("GLFW: %s" % e)
        raise RuntimeError("; ".join(errores))

    def _contexto_glfw(self):
        import glfw
        if not glfw.init():
            raise RuntimeError("glfw.init falló")
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3); glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE); glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
        self.ventana = glfw.create_window(16, 16, "spm", None, None)
        if not self.ventana:
            raise RuntimeError("no se pudo crear el contexto OpenGL 3.3 (¿controlador de la tarjeta gráfica desactualizado?)")
        glfw.make_context_current(self.ventana)

    def _contexto_egl(self):
        from OpenGL import EGL
        self.EGL = EGL
        self.dpy = EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY)
        mj, mn = EGL.EGLint(), EGL.EGLint()
        if not EGL.eglInitialize(self.dpy, ctypes.byref(mj), ctypes.byref(mn)):
            raise RuntimeError("eglInitialize falló")
        at = [EGL.EGL_SURFACE_TYPE, EGL.EGL_PBUFFER_BIT, EGL.EGL_RENDERABLE_TYPE, EGL.EGL_OPENGL_BIT, EGL.EGL_RED_SIZE, 8, EGL.EGL_GREEN_SIZE, 8,
              EGL.EGL_BLUE_SIZE, 8, EGL.EGL_DEPTH_SIZE, 24, EGL.EGL_NONE]
        cfg, n = EGL.EGLConfig(), EGL.EGLint()
        if not EGL.eglChooseConfig(self.dpy, (EGL.EGLint * len(at))(*at), ctypes.byref(cfg), 1, ctypes.byref(n)) or n.value < 1:
            raise RuntimeError("sin configuración EGL")
        self.surf = EGL.eglCreatePbufferSurface(self.dpy, cfg, (EGL.EGLint * 5)(EGL.EGL_WIDTH, 16, EGL.EGL_HEIGHT, 16, EGL.EGL_NONE))
        EGL.eglBindAPI(EGL.EGL_OPENGL_API)
        ca = [EGL.EGL_CONTEXT_MAJOR_VERSION, 3, EGL.EGL_CONTEXT_MINOR_VERSION, 3, EGL.EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL.EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL.EGL_NONE]
        self.ctx = EGL.eglCreateContext(self.dpy, cfg, EGL.EGL_NO_CONTEXT, (EGL.EGLint * len(ca))(*ca))
        if not self.ctx or not EGL.eglMakeCurrent(self.dpy, self.surf, self.surf, self.ctx):
            raise RuntimeError("no se pudo crear el contexto OpenGL 3.3")

    def _hacer_actual(self):
        if self.ventana is not None:
            import glfw
            glfw.make_context_current(self.ventana)
        else:
            self.EGL.eglMakeCurrent(self.dpy, self.surf, self.surf, self.ctx)

    def _programa(self):
        GL = self.GL

        def comp(tipo, src):
            s = GL.glCreateShader(tipo); GL.glShaderSource(s, src); GL.glCompileShader(s)
            if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
                raise RuntimeError("shader: " + GL.glGetShaderInfoLog(s).decode())
            return s
        p = GL.glCreateProgram()
        GL.glAttachShader(p, comp(GL.GL_VERTEX_SHADER, _VS)); GL.glAttachShader(p, comp(GL.GL_FRAGMENT_SHADER, _FS)); GL.glLinkProgram(p)
        if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
            raise RuntimeError("enlace: " + GL.glGetProgramInfoLog(p).decode())
        self.prog = p
        self.u = {n: GL.glGetUniformLocation(p, n) for n in ("uModel", "uView", "uProj", "uColor", "uAlpha")}

    def _cargar(self, ruta):
        GL = self.GL
        z = np.load(ruta)
        self.partes = []; mn = np.full(3, np.inf); mx = np.full(3, -np.inf); self.n_triangulos = 0
        for k in z.files:
            cuerpo, nombre, campo = k.split("|")
            if campo != "V":
                continue
            V = z[k]; N = z["%s|%s|N" % (cuerpo, nombre)]; I = z["%s|%s|I" % (cuerpo, nombre)]
            mn = np.minimum(mn, V.min(0)); mx = np.maximum(mx, V.max(0))
            n4 = np.zeros((len(N), 4), np.int8); n4[:, :3] = N
            vao = GL.glGenVertexArrays(1); GL.glBindVertexArray(vao)
            vb, nb, eb = GL.glGenBuffers(3)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vb); GL.glBufferData(GL.GL_ARRAY_BUFFER, V.nbytes, np.ascontiguousarray(V), GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(0); GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 12, None)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, nb); GL.glBufferData(GL.GL_ARRAY_BUFFER, n4.nbytes, n4, GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(1); GL.glVertexAttribPointer(1, 3, GL.GL_BYTE, True, 4, None)
            ind = np.ascontiguousarray(I.reshape(-1).astype(np.uint32))
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, eb); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, ind.nbytes, ind, GL.GL_STATIC_DRAW)
            GL.glBindVertexArray(0)
            col, al = _color(nombre)
            self.partes.append({"cuerpo": cuerpo, "nombre": nombre, "vao": vao, "n": len(ind), "color": col, "alfa": al})
            self.n_triangulos += len(ind) // 3
        self.centro = (mn + mx) / 2
        self.radio = float(np.linalg.norm(mx - mn) / 2)

    # ------------------------------------------------------------------ poses
    @staticmethod
    def grupo_de(cuerpo):
        return "fijo" if cuerpo == "fijo" else "plataforma" if cuerpo == "plataforma" else "nnr" if cuerpo.startswith("nnr") else "brazov"

    def _modelos(self, R, theta):
        d = self.spm.orientaciones_cuerpos(R, theta)
        rot = {"fijo": np.eye(3), "plataforma": d["plataforma"]}
        for i in (1, 2, 3):
            rot["nnr%d" % i] = d["nnr_brasop_%d" % i]; rot["brazov%d" % i] = d["brazov_%d" % i]
        out = {}
        for c, Rb in rot.items():
            M = np.eye(4, dtype=np.float32); M[:3, :3] = Rb; M[:3, 3] = self.O - Rb @ self.O
            out[c] = M
        return out

    # ------------------------------------------------------------------ framebuffers
    def _fbos(self, w, h):
        GL = self.GL
        if self._tam == (w, h):
            return
        if self._tam is not None:
            GL.glDeleteFramebuffers(2, [self.fbo_ms, self.fbo_rs]); GL.glDeleteRenderbuffers(3, [self.rb_c, self.rb_d, self.rb_r])
        self.fbo_ms, self.fbo_rs = GL.glGenFramebuffers(2); self.rb_c, self.rb_d, self.rb_r = GL.glGenRenderbuffers(3)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo_ms)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.rb_c); GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, self.msaa, GL.GL_RGBA8, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, self.rb_c)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.rb_d); GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, self.msaa, GL.GL_DEPTH_COMPONENT24, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, self.rb_d)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo_rs)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.rb_r); GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, self.rb_r)
        self._tam = (w, h)

    # ------------------------------------------------------------------ dibujo
    def renderizar(self, R, theta, azim=-55.0, elev=22.0, zoom=1.0, pan=(0.0, 0.0), ancho=900, alto=700, grupos_ocultos=(), ocultar=()):
        """Imagen RGB (alto, ancho, 3) uint8 del mecanismo en la pose (R, theta)."""
        GL = self.GL
        self._hacer_actual(); ancho, alto = int(max(64, ancho)), int(max(64, alto)); self._fbos(ancho, alto)
        a, e = math.radians(azim), math.radians(elev)
        dirc = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
        fov = math.radians(26.0)
        dist = self.radio * 0.80 / math.tan(fov / 2) / zoom
        der = np.cross(-dirc, [0, 0, 1]); der /= np.linalg.norm(der); arr = np.cross(der, -dirc)
        objetivo = self.centro + (der * pan[0] + arr * pan[1]) * self.radio
        V = _mira(objetivo + dirc * dist, objetivo)
        P = _perspectiva(fov, ancho / alto, max(1.0, dist - self.radio * 1.3), dist + self.radio * 1.3)
        modelos = self._modelos(R, theta)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo_ms); GL.glViewport(0, 0, ancho, alto)
        GL.glClearColor(*FONDO, 1.0); GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_DEPTH_TEST); GL.glEnable(GL.GL_MULTISAMPLE); GL.glDisable(GL.GL_CULL_FACE)
        GL.glUseProgram(self.prog)
        GL.glUniformMatrix4fv(self.u["uView"], 1, True, V); GL.glUniformMatrix4fv(self.u["uProj"], 1, True, P)
        visibles = [p for p in self.partes if self.grupo_de(p["cuerpo"]) not in grupos_ocultos and p["nombre"] not in ocultar]
        for transparentes in (False, True):                           # primero lo opaco; lo translúcido al final sin escribir profundidad
            if transparentes:
                GL.glEnable(GL.GL_BLEND); GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA); GL.glDepthMask(GL.GL_FALSE)
            for p in visibles:
                if (p["alfa"] < 1.0) != transparentes:
                    continue
                GL.glUniformMatrix4fv(self.u["uModel"], 1, True, modelos[p["cuerpo"]])
                GL.glUniform3f(self.u["uColor"], *p["color"]); GL.glUniform1f(self.u["uAlpha"], p["alfa"])
                GL.glBindVertexArray(p["vao"]); GL.glDrawElements(GL.GL_TRIANGLES, p["n"], GL.GL_UNSIGNED_INT, None)
        GL.glDepthMask(GL.GL_TRUE); GL.glDisable(GL.GL_BLEND); GL.glBindVertexArray(0)
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo_ms); GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.fbo_rs)
        GL.glBlitFramebuffer(0, 0, ancho, alto, 0, 0, ancho, alto, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo_rs); GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        datos = GL.glReadPixels(0, 0, ancho, alto, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        return np.frombuffer(datos, np.uint8).reshape(alto, ancho, 3)[::-1].copy()

    # ------------------------------------------------------------------ archivos
    def guardar_png(self, R, theta, archivo, texto=None, **kw):
        from PIL import Image
        img = Image.fromarray(self.renderizar(R, theta, **kw))
        if texto:
            self._texto(img, texto)
        img.save(archivo)

    @staticmethod
    def _texto(img, texto):
        from PIL import ImageDraw, ImageFont
        f = None
        for nombre in ("DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf", "consola.ttf", "Menlo.ttc"):
            try:
                f = ImageFont.truetype(nombre, 15); break
            except OSError:
                pass
        f = f or ImageFont.load_default()
        ImageDraw.Draw(img).multiline_text((12, 10), texto, fill=(201, 209, 217), font=f)

    def video(self, tray, archivo, ancho=960, alto=720, fps=30, texto=lambda k, tr: "", **kw):
        """mp4 (H.264, vía ffmpeg) con el CAD siguiendo la trayectoria a velocidad real."""
        from PIL import Image
        n = max(2, int(round(tray.duracion * fps)) + 1)
        if shutil.which("ffmpeg") is None:                                  # sin ffmpeg (p. ej. Windows recién instalado): GIF con Pillow
            archivo = Path(archivo).with_suffix(".gif"); fps = min(fps, 15); n = max(2, int(round(tray.duracion * fps)) + 1); cuadros = []
            for i in range(n):
                k = int(min(np.searchsorted(tray.t, i / fps), len(tray.t) - 1))
                img = Image.fromarray(self.renderizar(tray.R[k], tray.theta[k], ancho=ancho, alto=alto, **kw))
                self._texto(img, texto(k, tray)); cuadros.append(img.convert("P", palette=Image.ADAPTIVE))
            cuadros[0].save(archivo, save_all=True, append_images=cuadros[1:], duration=int(1000 / fps), loop=0)
            return archivo
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (ancho, alto), "-r", str(fps), "-i", "-",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(archivo)], stdin=subprocess.PIPE)
        try:
            for i in range(n):
                k = int(min(np.searchsorted(tray.t, i / fps), len(tray.t) - 1))
                img = Image.fromarray(self.renderizar(tray.R[k], tray.theta[k], ancho=ancho, alto=alto, **kw))
                self._texto(img, texto(k, tray)); ff.stdin.write(img.tobytes())
        finally:
            ff.stdin.close(); ff.wait()
        return archivo
