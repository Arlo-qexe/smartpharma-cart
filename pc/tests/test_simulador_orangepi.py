"""El simulador de la Orange Pi contra un servidor real (placeholder, base en memoria)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simular_orangepi as sim  # noqa: E402


def test_cargar_cajas_desde_carpeta_ordena_las_caras(tmp_path):
    for k in (3, 1, 2):
        (tmp_path / f"caja007_cara{k}.jpg").write_bytes(bytes([k]))
    (tmp_path / "otra.txt").write_text("x")
    assert sim.cargar_cajas(tmp_path) == {"caja007": [b"\x01", b"\x02", b"\x03"]}


def test_cargar_cajas_desde_zip(tmp_path):
    import zipfile
    z = tmp_path / "f.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("train/images/caja001_cara2.jpg", b"b")
        zf.writestr("train/images/caja001_cara1.jpg", b"a")
    assert sim.cargar_cajas(z) == {"caja001": [b"a", b"b"]}


def test_dataset_por_defecto_trae_cajas_de_cinco_fotos():
    if not sim.FOTOS_DEFECTO.is_file():
        import pytest
        pytest.skip("no está el conjunto de fotos")
    cajas = sim.cargar_cajas(sim.FOTOS_DEFECTO)
    assert cajas and all(len(v) == 5 for v in cajas.values())
