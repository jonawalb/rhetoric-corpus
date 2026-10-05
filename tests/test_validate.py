"""Human-validation helpers: top-up allocation, xlsx/csv reading of returned coding files, Krippendorff's alpha and
multi-coder scoring. No models or databases are loaded."""
from __future__ import annotations

import csv
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.semantic.config import DIMS  # noqa: E402
from scripts.semantic.validate import (KEY_COLS, allocate, krippendorff_alpha, lang_deficits,  # noqa: E402
                                       read_coded, report_md, score)


def test_lang_deficits_only_langs_with_data_below_min() -> None:
    assert lang_deficits({"en": 180, "fa": 6, "tr": 30}, ["en", "fa", "tr", "ar"], 30) == {"ar": 30, "fa": 24}


def test_allocate_sums_and_is_sqrt_proportional() -> None:
    out = allocate(24, {"IR": 400, "AF": 100, "XX": 0})
    assert sum(out.values()) == 24 and out == {"IR": 16, "AF": 8}
    assert allocate(0, {"IR": 5}) == {} and allocate(3, {}) == {}
    assert sum(allocate(7, {"a": 1, "b": 1, "c": 1}).values()) == 7


def test_krippendorff_alpha_known_values() -> None:
    assert krippendorff_alpha([["1", "1"], ["0", "0"], ["1", "1"]]) == 1.0
    # chance-level agreement on a balanced set: Do = 0.5, De = 32/56 -> 0.125; systematic disagreement -> negative
    assert krippendorff_alpha([["1", "0"], ["0", "1"], ["1", "1"], ["0", "0"]]) == 0.125
    assert krippendorff_alpha([["1", "0"], ["0", "1"], ["1", "0"]]) == pytest.approx(-0.6667, abs=1e-4)
    # hand computation: o(0,0)=6, o(1,1)=2, o(0,1)=o(1,0)=1, n=10, n0=7, n1=3 -> 1 - (2/10)/(42/90) = 0.5714
    units = [["0", "0"]] * 3 + [["1", "1"], ["0", "1"], ["0", None]]
    assert krippendorff_alpha(units) == pytest.approx(0.5714, abs=1e-4)
    assert krippendorff_alpha([["1", "1"], ["1", None]]) is None          # no variation -> undefined


def _write_xlsx(path: Path, rows: list) -> None:
    """Minimal workbook with a 'Coding' sheet using shared strings, numbers and an inline string."""
    strings: list = []
    def sst(v: str) -> int:
        if v not in strings:
            strings.append(v)
        return strings.index(v)
    xml_rows = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, v in enumerate(row):
            ref = f"{chr(65 + ci)}{ri}"
            if v == "":
                continue
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            elif ci == 1:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{sst(v)}</v></c>')
        xml_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets><sheet name="Instructions" sheetId="1" r:id="rId1"/>'
                                      f'<sheet name="Coding" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
                   '</Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")


HEAD = ["sample_id", "country", *DIMS, "coder"]


def _key(tmp: Path, n: int = 12) -> Path:
    p = tmp / "key.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(KEY_COLS)
        for i in range(n):
            pos = i % 2
            s = [0.9 if pos else 0.1] * len(DIMS)
            t = [""] * len(DIMS) if i == 0 else [0.8 if pos else 0.2] * len(DIMS)   # one row without teacher scores
            w.writerow([f"v{i:04d}", f"d{i}", i, "random" if i < 6 else "enriched", "en", "RU", *s, *t])
    return p


def test_read_coded_xlsx_picks_coding_sheet_and_normalises() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.xlsx"
        _write_xlsx(p, [HEAD, ["v0000", "RU", 1.0, 0, "", 1, 0, 0, "ann"], ["", "", "", "", "", "", "", "", ""]])
        rows = read_coded(str(p))
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "v0000" and rows[0]["country"] == "RU" and rows[0]["coder"] == "ann"
    assert [rows[0][d] for d in DIMS] == ["1", "0", "", "1", "0", "0"]


def test_score_two_coders_xlsx_and_csv(tmp_path: Path) -> None:
    key = _key(tmp_path)
    a = tmp_path / "coderA.xlsx"
    _write_xlsx(a, [HEAD] + [[f"v{i:04d}", "RU", *[i % 2] * len(DIMS), "ann"] for i in range(12)])
    b = tmp_path / "coderB.csv"
    with b.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for i in range(12):
            v = (1 - i % 2) if i == 3 else i % 2               # one disagreement
            w.writerow([f"v{i:04d}", "RU", *[v] * len(DIMS), "bob"])
    res = score([str(a), str(b)], key)
    assert res["coders"] == {"ann": 12, "bob": 12}
    h = res["dims"]["hostility"]
    assert h["per_coder"]["ann"]["human_vs_student"]["auc"] == 1.0
    assert h["per_coder"]["ann"]["human_vs_student"]["f1"] == 1.0
    assert h["per_coder"]["ann"]["human_vs_teacher"]["n"] == 11          # row without teacher score skipped
    assert h["n_double_coded"] == 12 and 0 < h["krippendorff_alpha"] < 1
    assert h["consensus"]["human_vs_student"]["n"] == 11
    md = report_md(res, [str(a), str(b)])
    assert "Krippendorff" in md and "| hostility | consensus | 11 |" in md


def test_score_single_coder_has_no_alpha(tmp_path: Path) -> None:
    key = _key(tmp_path)
    b = tmp_path / "solo.csv"
    with b.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows([[f"v{i:04d}", "RU", *[i % 2] * len(DIMS), ""] for i in range(12)])
    res = score([str(b)], key)
    assert list(res["coders"]) == ["solo"] and "krippendorff_alpha" not in res["dims"]["threat"]
    assert "Krippendorff" not in report_md(res, [str(b)])

