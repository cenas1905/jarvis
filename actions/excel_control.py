"""Bounded Excel COM operations. Never runs macros or silently overwrites files."""
import json
import re
from pathlib import Path
from actions.desktop_control import _lock

FUNCTIONS = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "COUNTIF", "COUNTIFS",
             "SUMIF", "SUMIFS", "IF", "IFERROR", "ROUND", "ABS", "PRODUCT", "SUMPRODUCT"}


def validate_address(address):
    address = str(address).upper()
    if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}(:[A-Z]{1,3}[1-9][0-9]{0,6})?", address):
        raise ValueError("A1 veya A1:D20 biçiminde hücre aralığı gerekli.")
    return address


def validate_formula(formula):
    if not formula.startswith("=") or len(formula) > 1000:
        raise ValueError("Formül = ile başlamalı, en çok 1000 karakter olmalı.")
    if any(c in formula for c in "[]!|\\\r\n"):
        raise ValueError("Dış bağlantı, DDE veya başka sayfa referansı desteklenmiyor.")
    names = set(re.findall(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(", formula))
    if any(n.upper() not in FUNCTIONS for n in names):
        raise ValueError("Bu formül işlevi güvenli temel işlev listesinde değil.")
    if not names and not re.fullmatch(r"=[A-Za-z0-9$:.+*/%() ^<>=,-]+", formula):
        raise ValueError("Formül biçimi desteklenmiyor.")
    return formula


def excel_control(action, workbook="", sheet="", address="A1", data_json="", formula="", path=""):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = book = ws = target = None
    try:
        with _lock:
            try:
                app = win32com.client.GetActiveObject("Excel.Application")
            except Exception:
                if action != "create":
                    return "Açık Excel oturumu bulunamadı. Excel'i açıp belgeyi seç veya create kullan."
                app = win32com.client.Dispatch("Excel.Application")
            app.Visible = True
            if action == "create":
                book = app.Workbooks.Add()
                return "Yeni kitap açıldı: " + book.Name + ". Henüz kaydedilmedi."
            if action == "list":
                return json.dumps([{"workbook":b.Name, "sheets":[s.Name for s in b.Worksheets]}
                                   for b in app.Workbooks], ensure_ascii=False)
            if not workbook:
                raise ValueError("Önce list ile kitap adını belirle; hedef kitap adı gerekli.")
            book = app.Workbooks.Item(workbook)
            if action == "save_copy":
                dest = Path(path).expanduser()
                if not dest.is_absolute() or dest.exists() or not dest.parent.is_dir():
                    raise ValueError("Var olan klasörde, kullanılmamış tam dosya yolu gerekli; üzerine yazılmaz.")
                if dest.suffix.lower() != ".xlsx" or book.HasVBProject:
                    raise ValueError("Yalnızca makrosuz .xlsx kopyası destekleniyor.")
                if int(book.FileFormat) != 51:
                    raise ValueError("Önce Excel'de .xlsx biçimine kaydet; dosya biçimi sessizce dönüştürülmez.")
                book.SaveCopyAs(str(dest))
                return "Kopya kaydedildi: " + str(dest)
            if not sheet:
                raise ValueError("Hedef sayfa adı gerekli; önce list kullan.")
            ws = book.Worksheets.Item(sheet)
            target = ws.Range(validate_address(address))
            if target.CountLarge > 1000:
                raise ValueError("Tek işlemde en fazla 1000 hücre; daha küçük aralık seç.")
            if action == "read":
                return json.dumps({"workbook":book.Name,"sheet":ws.Name,"range":address,
                                   "values":target.Value2,"formulas":target.Formula}, ensure_ascii=False, default=str)
            if action == "write":
                rows = json.loads(data_json)
                if not isinstance(rows,list) or not rows or any(not isinstance(r,list) for r in rows):
                    raise ValueError("data_json iki boyutlu JSON dizi olmalı.")
                if len(rows)!=target.Rows.Count or any(len(r)!=target.Columns.Count for r in rows):
                    raise ValueError("Veri boyutu seçilen aralıkla aynı olmalı; işlem yapılmadı.")
                for row in rows:
                    for value in row:
                        if not isinstance(value,(str,int,float,bool,type(None))):
                            raise ValueError("Hücre yalnızca metin/sayı/boş olabilir.")
                        if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")):
                            raise ValueError("Metin içinde formül çalıştırılmaz. Sayılar sayı türünde; formüller formula işlemiyle verilmeli.")
                target.Value2 = tuple(tuple(r) for r in rows)
            elif action == "formula":
                if target.CountLarge != 1:
                    raise ValueError("Formül için tek hücre seç.")
                target.Formula = validate_formula(formula)
                target.Calculate()
            elif action == "format":
                target.Font.Name = "Calibri"
                target.Font.Size = 11
                target.Columns.AutoFit()
            else:
                raise ValueError("Bilinmeyen Excel işlemi.")
            return "İşlem tamamlandı; kitap açık, diske otomatik kaydedilmedi. Değişen aralığı read ile doğrula."
    except Exception as exc:
        return "Excel işlemi tamamlanmadı: " + str(exc)
    finally:
        target = ws = book = app = None
        pythoncom.CoUninitialize()
