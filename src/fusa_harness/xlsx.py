"""Small dependency-free XLSX writer for editable analysis tables."""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _cell(value: str, row: int, col: int, style: int = 0) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    ref = f"{letters}{row}"
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _visual(text: str) -> int:
    """显示宽度：CJK 字符按 2 个宽度单位计。"""
    return sum(2 if ord(char) > 127 else 1 for char in str(text))


def _width(values: list[str], cap: int = 48) -> float:
    """按内容估算列宽。"""
    widest = max((_visual(value) for value in values), default=0)
    return min(max(widest * 0.9 + 2, 10), cap)


def write_xlsx(path: Path, sheets: list[tuple[str, list[str], list[list[str]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", ZIP_DEFLATED) as book:
        names = []
        rels = []
        types = ['<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
        for index, (name, headers, rows) in enumerate(sheets, 1):
            safe = name[:31]
            names.append(f'<sheet name="{escape(safe)}" sheetId="{index}" r:id="rId{index}"/>')
            rels.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
            types.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            all_rows = [headers, *rows]
            width = max(1, len(headers))
            widths = [
                _width([headers[column - 1], *[row[column - 1] for row in rows if column - 1 < len(row)]])
                for column in range(1, width + 1)
            ]
            xml_rows = []
            for n, values in enumerate(all_rows, 1):
                lines = 1
                for column, value in enumerate(values[:width]):
                    capacity = max(widths[column] - 2, 6)
                    lines = max(lines, -(-_visual(value) // int(capacity)))
                height = 30 if n == 1 else min(max(lines * 14 + 4, 18), 240)
                xml_rows.append(
                    f'<row r="{n}" ht="{height}" customHeight="1">'
                    + "".join(_cell(v, n, c, 1 if n == 1 else 2) for c, v in enumerate(values, 1))
                    + '</row>'
                )
            cols = "".join(
                f'<col min="{column}" max="{column}" width="{widths[column - 1]}" customWidth="1"/>'
                for column in range(1, width + 1)
            )
            xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f'<dimension ref="A1:{_col(width)}{len(all_rows)}"/>'
                   '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                   f'<cols>{cols}</cols>'
                   f'<sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:{_col(width)}{len(all_rows)}"/></worksheet>')
            book.writestr(f"xl/worksheets/sheet{index}.xml", xml)
        book.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' + "".join(types) + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        book.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(names) + '</sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(rels) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        book.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs></styleSheet>')


def _col(number: int) -> str:
    result = ""
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result
