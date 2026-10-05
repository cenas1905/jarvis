"""Create a separate, macro-free Excel workbook from a validated research table."""
from pathlib import Path

from actions.desktop_control import _lock


def _text(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_research(query, result, path):
    import pythoncom
    import win32com.client
    target = Path(path).resolve()
    if target.suffix.lower() != ".xlsx" or target.exists():
        raise ValueError("Yeni ve kullanılmamış bir .xlsx yolu gerekli")
    target.parent.mkdir(parents=True, exist_ok=True)
    headers, rows = result["headers"], result["rows"]
    if not 2 <= len(headers) <= 10 or not 1 <= len(rows) <= 30 or any(len(r) != len(headers) for r in rows):
        raise ValueError("Geçersiz araştırma tablosu")
    pythoncom.CoInitialize()
    app = book = sheet = source_sheet = None
    try:
        with _lock:
            # Separate Excel instance protects the user's open workbooks.
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False
            app.AutomationSecurity = 3
            book = app.Workbooks.Add()
            sheet = book.Worksheets.Item(1)
            sheet.Name = "Karşılaştırma"
            width = len(headers)
            sheet.Range(sheet.Cells(1, 1), sheet.Cells(1, width)).Merge()
            sheet.Cells(1, 1).Value2 = "JARVIS · DeepSeek araştırması"
            sheet.Cells(1, 1).Font.Size = 20
            sheet.Range(sheet.Cells(2, 1), sheet.Cells(2, width)).Merge()
            sheet.Cells(2, 1).Value2 = _text(query[:800])
            sheet.Rows(2).RowHeight = 48
            sheet.Range(sheet.Cells(3, 1), sheet.Cells(3, width)).Merge()
            sheet.Cells(3, 1).Value2 = "Fikirler öneridir; gelir ve talep varsayımları doğrulanmalıdır. Kaynak kapsamı ikinci sayfadadır."
            sheet.Rows(3).RowHeight = 32
            values = tuple(tuple(_text(v) for v in row) for row in [headers] + rows)
            grid = sheet.Range(sheet.Cells(5, 1), sheet.Cells(5 + len(rows), width))
            grid.NumberFormat = "@"
            grid.Value2 = values
            grid.Font.Name = "Calibri"
            grid.Font.Size = 11
            grid.WrapText = True
            grid.VerticalAlignment = -4160
            grid.ColumnWidth = 29
            header = sheet.Range(sheet.Cells(5, 1), sheet.Cells(5, width))
            header.Interior.Color = 0x49331B
            header.Font.Color = 0xFFFFFF
            header.Font.Bold = True
            header.AutoFilter()
            grid.Rows.AutoFit()
            sheet.UsedRange.WrapText = True
            source_sheet = book.Worksheets.Add(After=sheet)
            source_sheet.Name = "Kaynaklar"
            source_rows = [["Kimlik", "Kaynak", "Bağlantı", "Okuma kapsamı"]] + [
                [s["id"], s["title"], s["url"], s["coverage"]] for s in result["sources"]]
            source_grid = source_sheet.Range(source_sheet.Cells(1, 1), source_sheet.Cells(len(source_rows), 4))
            source_grid.NumberFormat = "@"
            source_grid.Value2 = tuple(tuple(_text(v) for v in row) for row in source_rows)
            source_grid.ColumnWidth = 38
            source_grid.WrapText = True
            source_grid.Rows.AutoFit()
            source_sheet.Rows(1).Font.Bold = True
            sheet.Activate()
            app.ActiveWindow.SplitRow = 5
            app.ActiveWindow.FreezePanes = True
            if str(sheet.Cells(5, 1).Value2) != str(headers[0]):
                raise RuntimeError("Excel'e yazılan başlık doğrulanamadı")
            book.SaveAs(str(target), FileFormat=51)
            if not target.is_file():
                raise RuntimeError("Excel dosyası kaydedilemedi")
            app.Visible = True
            return str(target)
    except Exception:
        if book is not None:
            book.Close(SaveChanges=False)
        if app is not None:
            app.Quit()
        raise
    finally:
        source_sheet = sheet = book = app = None
        pythoncom.CoUninitialize()
