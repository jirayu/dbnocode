"""
GUI Initializer Builder v2.1
Produces single distributable EXE for end users
"""
import tkinter as tk
from tkinter import filedialog, messagebox
import re, zipfile, io, base64, subprocess, sys, tempfile
from pathlib import Path

LICENSE_TEXT = """DSL No‑Code TUI Platform © 2026
Terms: No reverse engineering; use at your own risk."""
PROJECT_ZIP_B64 = ""  # B64_PLACEHOLDER

class LicenseScreen(tk.Toplevel):
    def __init__(self, parent, on_agree):
        super().__init__(parent)
        self.title("License Agreement")
        self.geometry("500x280")
        tk.Label(self, text="License Agreement", font=("Segoe UI",12,"bold")).pack(pady=10)
        txt = tk.Text(self, wrap="word", height=12)
        txt.insert("1.0", LICENSE_TEXT.strip())
        txt.config(state="disabled")
        txt.pack(padx=20, fill="both", expand=True)
        f = tk.Frame(self); f.pack(pady=15)
        tk.Button(f, text="I Agree", command=lambda: [self.destroy(), on_agree()], bg="#2ecc71", fg="white").pack(side="left", padx=5)
        tk.Button(f, text="Cancel", command=self.quit).pack(side="right", padx=5)

class MainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DSL Project Initializer")
        self.root.geometry("520x220")
        tk.Label(root, text="Create New DSL Project", font=("Segoe UI",14,"bold")).pack(pady=15)
        tk.Label(root, text="Project Folder:").pack(anchor="w", padx=30)
        self.path = tk.StringVar(value=str(Path.home() / "DSL_Projects" / "MyApp"))
        tk.Entry(root, textvariable=self.path, width=55).pack(padx=30, pady=5, side="left")
        tk.Button(root, text="Browse...", command=self._browse).pack(padx=5)
        tk.Button(root, text="Create Project", command=self._create, bg="#2ecc71", fg="white", font=("Segoe UI",11,"bold"), height=2).pack(pady=20)

    def _browse(self):
        f = filedialog.asksaveasfilename(initialfile="MyDSLApp")
        if f: self.path.set(f)

    def _create(self):
        target = Path(self.path.get())
        if target.exists() and not messagebox.askyesno("Overwrite?", "Replace existing folder?"):
            return
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(PROJECT_ZIP_B64))) as zf:
            for member in zf.namelist():
                member_path = (target / member).resolve()
                if not str(member_path).startswith(str(target.resolve())):
                    raise ValueError(f"Zip entry would extract outside target: {member}")
            zf.extractall(target)
        messagebox.showinfo("Done!", f"Created at:\n{target}\nRun dsl_tui_app.exe scripts/02_delivery_note.dsl")
        self.root.destroy()

def run_gui():
    root = tk.Tk(); root.withdraw()
    LicenseScreen(root, lambda: MainApp(root) or root.deiconify())
    root.mainloop()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "BUILD":
        print("Building project archive...")
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = Path(tmpdir) / "proj"
            from init_app import create_project
            create_project(proj)
            import sqlite3
            conn = sqlite3.connect(proj / "sample_data.db")
            conn.executescript("""
                CREATE TABLE customers (code TEXT PRIMARY KEY, name TEXT);
                INSERT INTO customers VALUES ('C001','Acme Supplies'),('C002','Beta Logistics');
                CREATE TABLE items (code TEXT PRIMARY KEY, name TEXT, price REAL);
                INSERT INTO items VALUES ('P001','Bolt M10',1.25),('P002','Nut M10',0.45);
            """)
            conn.commit(); conn.close()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, 9) as zf:
                for f in proj.rglob("*"):
                    if f.is_file(): zf.write(f, f.relative_to(proj))
            b64 = base64.b64encode(buf.getvalue()).decode()
        src = re.sub(r'^PROJECT_ZIP_B64 = ""  # B64_PLACEHOLDER$', f"PROJECT_ZIP_B64 = '''{b64}'''", Path(__file__).read_text(encoding="utf-8"), flags=re.MULTILINE)
        Path("final_gui.py").write_text(src, encoding="utf-8")
        subprocess.run([sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "-n", "DSL_Project_Init", "final_gui.py"], check=True)
        print("Output: dist/DSL_Project_Init.exe")
    else:
        run_gui()