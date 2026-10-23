# ia/ — módulo de reconocimiento (en desarrollo, separado de `pc/`)

Lee 5 fotos de una caja, extrae lote y fecha de vencimiento (OCR + reglas) y devuelve
el destino FEFO o `ERROR_REVISION_MANUAL`.

```bash
cd ia
bash scripts/setup_venv.sh && source .venv/bin/activate
python -m unittest discover -s tests -t .
claude          # agente de esta carpeta: lee CLAUDE.md y docs/DISENO.md
```

- Diseño, supuestos y decisiones abiertas: [`docs/DISENO.md`](docs/DISENO.md)
- Configuración (rangos FEFO placeholder, umbrales): [`config/reconocimiento.json`](config/reconocimiento.json)
- Aún **no está conectado** a `pc/`: ver `smartpharma_ia/adaptador_pc.py`.
