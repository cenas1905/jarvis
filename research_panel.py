"""Text research entry point that works even when Gemini Live has no quota."""
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

from actions.research import research, _load_store, _LOCK


def open_panel(parent=None):
    window = tk.Toplevel(parent) if parent else tk.Tk()
    window.title("JARVIS · DeepSeek Araştırma")
    window.geometry("1000x760")
    window.configure(bg="#101923")
    fg, bg = "#e1edf5", "#101923"
    tk.Label(window, text="Ne araştırmamı istersin?", bg=bg, fg=fg,
             font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=22, pady=(18, 8))
    tk.Label(window, text="DeepSeek ile araştırma • Gemini bağlantısı gerektirmez", bg=bg, fg="#8cbed0").pack(anchor="w", padx=22)
    query = tk.Text(window, height=4, wrap="word", font=("Segoe UI", 12), bg="#1d2b39", fg=fg, insertbackground=fg)
    query.pack(fill="x", padx=22, pady=12)
    query.insert("1.0", "Yapabileceğim 5 kullanışlı websitesi fikri bul. Hedef kitle, ihtiyaç, ilk sürüm özellikleri, gelir modeli varsayımı, zorluk ve riskleri karşılaştır.")
    excel = tk.BooleanVar(value=True)
    tk.Checkbutton(window, text="Bittiğinde Excel'de göster ve .xlsx kaydet", variable=excel,
        bg=bg, fg=fg, selectcolor="#1d2b39", activebackground=bg, activeforeground=fg).pack(anchor="w", padx=22)
    status = tk.StringVar(value="Konuyu yazıp başlat. Araştırma bu pencereden bağımsız sürer.")
    state = {"id": "", "poll": None}

    def show(text):
        output.delete("1.0", "end")
        output.insert("1.0", text)

    def poll():
        with _LOCK:
            job = next((dict(j) for j in _load_store()["jobs"] if j.get("id") == state["id"]), None)
        if not job:
            start.configure(state="normal")
            status.set("Görev kaydı bulunamadı.")
            return
        status.set(job.get("phase", job.get("status", "")))
        if job.get("status") == "running":
            # Also detect a job interrupted by closing a previous JARVIS process.
            research("research_status", interaction_id=state["id"])
            state["poll"] = window.after(1500, poll)
        else:
            with _LOCK:
                final_job = next((dict(j) for j in _load_store()["jobs"] if j.get("id") == state["id"]), None)
            if final_job and final_job.get("status") == "completed" and final_job.get("report_path"):
                try:
                    from pathlib import Path
                    detail = Path(final_job["report_path"]).read_text(encoding="utf-8")
                    show(detail + ("\n\nExcel dosyası: " + final_job["excel_path"] if final_job.get("excel_path") else ""))
                except OSError:
                    show(research("research_status", interaction_id=state["id"]))
            else:
                show(research("research_status", interaction_id=state["id"]))
            start.configure(state="normal")

    def launch():
        text = query.get("1.0", "end").strip()
        if not text:
            status.set("Önce araştırılacak konuyu yaz.")
            return
        message = research("deep_research", query=text, export_excel=excel.get())
        show(message)
        import re
        match = re.search(r"deepseek-[a-f0-9]{16}", message)
        if match:
            state["id"] = match.group()
            start.configure(state="disabled")
            poll()
        else:
            status.set(message)

    start = tk.Button(window, text="Araştırmayı başlat", command=launch, bg="#1ab7ab", fg="#08212a", font=("Segoe UI", 11, "bold"), padx=16, pady=8)
    start.pack(anchor="w", padx=22, pady=(8, 2))
    tk.Label(window, textvariable=status, bg=bg, fg="#8cbed0", wraplength=940, justify="left").pack(anchor="w", padx=22, pady=6)
    output = ScrolledText(window, wrap="word", font=("Segoe UI", 11), bg="#172330", fg=fg)
    output.pack(fill="both", expand=True, padx=22, pady=8)
    if parent is None:
        window.mainloop()
    return window


if __name__ == "__main__":
    open_panel()
