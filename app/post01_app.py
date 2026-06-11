#!/opt/homebrew/bin/python3
"""
POST-01 — Drag & Drop App
Menu bar watcher + drag and drop window for TFCPOST01.

Drop a brief PDF or JSON onto the window or into the watch folder.
POST-01 does the rest.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import rumps
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ── Paths ──────────────────────────────────────────────────────────────────────
APP_DIR    = Path(__file__).parent
PROJECT    = APP_DIR.parent
SRC        = PROJECT / "src"
SETTINGS_P = PROJECT / "config" / "settings.json"
sys.path.insert(0, str(SRC))

def load_settings() -> dict:
    if SETTINGS_P.exists():
        with open(SETTINGS_P) as f:
            return json.load(f)
    return {}

SETTINGS = load_settings()
QNAP     = Path(SETTINGS.get("qnap_base", "/Volumes/TFC"))
WORK_IN_PROGRESS = QNAP / "1) WORK IN PROGRESS"
TEMPLATE_NAME    = "-!!_2025 NEW_TEMPLATE_YYMMDD_CLIENT_PROJECT"
WATCH_FOLDER     = QNAP / "POST01_Input"   # drop briefs here for auto-run
OUTPUT_FOLDER    = QNAP / "POST01_Output"
PYTHON           = sys.executable


# ── Brief extraction ───────────────────────────────────────────────────────────

def extract_brief_from_pdf(pdf_path: Path) -> dict:
    """Use Claude API to extract structured brief from a PDF."""
    import anthropic, base64
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    with open(pdf_path, "rb") as f:
        pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")

    schema_path = PROJECT / "config" / "briefs" / "brief_schema.json"
    with open(schema_path) as f:
        schema = f.read()

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}
                },
                {
                    "type": "text",
                    "text": f"""Extract this production brief into a POST-01 JSON brief.
Follow this schema exactly:
{schema}

Rules:
- brief_id: derive from date + client + project (e.g. 260609_PORSCHE_CLASSICS_SHANGHAI)
- style_preset: infer from content (default: topgear)
- Extract all beats, deliverables, client info
- created_at: today {datetime.now().isoformat()}
- outcome_tag: "pending"
- project_folder: "{WORK_IN_PROGRESS}/[brief_id]"
- Return only valid JSON, no commentary."""
                }
            ]
        }]
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def load_brief(path: Path) -> dict:
    if path.suffix.lower() == ".pdf":
        return extract_brief_from_pdf(path)
    elif path.suffix.lower() == ".json":
        with open(path) as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported brief format: {path.suffix}")


# ── Project setup ──────────────────────────────────────────────────────────────

def setup_project_folder(brief: dict) -> Path:
    brief_id = brief.get("brief_id", "UNKNOWN")
    dest = WORK_IN_PROGRESS / brief_id

    if dest.exists():
        return dest  # already set up

    template = WORK_IN_PROGRESS / TEMPLATE_NAME
    if not template.exists():
        # Fallback: create from POST-01 structure
        from qnap_folders import create_project_folders
        return create_project_folders(brief)

    shutil.copytree(str(template), str(dest))
    return dest


def move_media(brief: dict, project_root: Path) -> bool:
    """Move media from Downloads or specified path to project media folder."""
    brief_id = brief.get("brief_id", "")
    assets = brief.get("assets", {})
    media_source = assets.get("media_source")

    # Try to find media in Downloads if not specified
    if not media_source:
        client_name = brief.get("client", {}).get("name", "").replace(" ", "-").upper()
        project_title = brief.get("project_title", "").replace(" ", "-").upper()
        downloads = Path.home() / "Downloads"
        candidates = [
            downloads / brief_id,
            downloads / client_name,
            downloads / project_title,
        ]
        for c in candidates:
            if c.exists():
                media_source = str(c)
                break

    if not media_source or not Path(media_source).exists():
        return False

    # Find the media folder in the project
    media_dest = None
    for candidate in ["3. MEDIA/Client Provided", "3. MEDIA", "MEDIA"]:
        p = project_root / candidate
        if p.exists():
            media_dest = p
            break

    if not media_dest:
        media_dest = project_root / "3. MEDIA" / "Client Provided"
        media_dest.mkdir(parents=True, exist_ok=True)

    dest_path = media_dest / Path(media_source).name
    shutil.move(media_source, str(dest_path))
    # Update brief with actual paths
    brief.setdefault("assets", {})["footage_folders"] = [str(dest_path)]
    return True


def save_brief_json(brief: dict, project_root: Path) -> Path:
    briefs_dir = project_root / "1. DOCUMENTS"
    briefs_dir.mkdir(exist_ok=True)
    brief_path = briefs_dir / f"{brief.get('brief_id', 'brief')}.json"
    with open(brief_path, "w") as f:
        json.dump(brief, f, indent=2)
    # Also save to POST-01 briefs folder
    local_path = PROJECT / "config" / "briefs" / f"{brief.get('brief_id', 'brief')}.json"
    with open(local_path, "w") as f:
        json.dump(brief, f, indent=2)
    return local_path


# ── Pipeline runner ────────────────────────────────────────────────────────────

def run_pipeline(brief_path: Path, status_callback=None) -> bool:
    def status(msg):
        print(msg)
        if status_callback:
            status_callback(msg)

    output_dir = str(OUTPUT_FOLDER)

    cmd = [
        PYTHON, str(PROJECT / "post01.py"),
        "--brief", str(brief_path),
        "--output-dir", output_dir,
        "--skip-fcpxml"  # remove once timecodes validated
    ]

    status("Running POST-01 pipeline...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        status("Pipeline complete.")
        return True
    else:
        status(f"Pipeline error: {result.stderr[-200:]}")
        return False


def notify(title: str, message: str):
    """macOS notification."""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script])


# ── Full job ───────────────────────────────────────────────────────────────────

def run_full_job(brief_file: Path, status_callback=None):
    def status(msg):
        print(f"[POST-01] {msg}")
        if status_callback:
            status_callback(msg)

    try:
        status(f"Loading brief: {brief_file.name}")
        brief = load_brief(brief_file)
        brief_id = brief.get("brief_id", brief_file.stem)
        status(f"Brief ID: {brief_id}")

        status("Setting up project folder...")
        project_root = setup_project_folder(brief)
        status(f"Project: {project_root.name}")

        status("Moving media...")
        moved = move_media(brief, project_root)
        if moved:
            status("Media moved to project folder")
        else:
            status("Media not found in Downloads — add manually to 3. MEDIA")

        status("Saving brief JSON...")
        brief_path = save_brief_json(brief, project_root)

        status("Running POST-01 pipeline...")
        ok = run_pipeline(brief_path, status_callback)

        if ok:
            notify("POST-01", f"{brief_id} ready — check POST01_Output")
            status("Done. Check POST01_Output on the QNAP.")
        else:
            notify("POST-01 Error", f"{brief_id} — pipeline failed, check logs")

    except Exception as e:
        status(f"Error: {e}")
        notify("POST-01 Error", str(e)[:100])


# ── Watch folder handler ───────────────────────────────────────────────────────

class BriefDropHandler(FileSystemEventHandler):
    def __init__(self, status_callback=None):
        self.status_callback = status_callback
        self._seen = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() in (".pdf", ".json") and path not in self._seen:
            self._seen.add(path)
            time.sleep(1)  # wait for file to finish copying
            threading.Thread(
                target=run_full_job,
                args=(path, self.status_callback),
                daemon=True
            ).start()


# ── Menu bar app ───────────────────────────────────────────────────────────────

class POST01App(rumps.App):
    def __init__(self):
        super().__init__(
            "POST-01",
            icon=None,
            title="▶ POST-01",
            quit_button="Quit POST-01"
        )

        self.status_item = rumps.MenuItem("● Ready", callback=None)
        self.watch_item  = rumps.MenuItem("Watch folder: OFF", callback=self.toggle_watch)
        self.drop_item   = rumps.MenuItem("Open Drop Window", callback=self.open_drop_window)
        self.open_output  = rumps.MenuItem("Open Output Folder", callback=self.open_outputs)
        self.open_wip     = rumps.MenuItem("Open Work in Progress", callback=self.open_wip_folder)
        self.search_item  = rumps.MenuItem("Visual Search...", callback=self.open_search_window)

        self.menu = [
            self.status_item,
            None,
            self.watch_item,
            self.drop_item,
            self.search_item,
            None,
            self.open_output,
            self.open_wip,
        ]

        self.observer = None
        self._ensure_watch_folder()

    def _ensure_watch_folder(self):
        WATCH_FOLDER.mkdir(parents=True, exist_ok=True)

    def set_status(self, msg: str):
        rumps.App.title.fset(self, f"▶ {msg[:30]}")
        self.status_item.title = f"● {msg}"

    def toggle_watch(self, sender):
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer = None
            self.watch_item.title = "Watch folder: OFF"
            self.set_status("Ready")
        else:
            self._start_watching()
            self.watch_item.title = f"Watch folder: ON  ({WATCH_FOLDER.name})"
            self.set_status("Watching...")

    def _start_watching(self):
        handler = BriefDropHandler(status_callback=self.set_status)
        self.observer = Observer()
        self.observer.schedule(handler, str(WATCH_FOLDER), recursive=False)
        self.observer.start()

    def open_drop_window(self, _):
        threading.Thread(target=self._show_drop_window, daemon=True).start()

    def _show_drop_window(self):
        import tkinter as tk
        from tkinter import filedialog, scrolledtext

        root = tk.Tk()
        root.title("POST-01 — Drop Brief")
        root.geometry("480x420")
        root.configure(bg="#1A1A1A")
        root.resizable(False, False)

        # Header
        tk.Label(root, text="POST-01", font=("Helvetica", 20, "bold"),
                 fg="#E8500A", bg="#1A1A1A").pack(pady=(20, 2))
        tk.Label(root, text="Drop a brief PDF or JSON to start",
                 font=("Helvetica", 11), fg="#888888", bg="#1A1A1A").pack()

        # Drop zone
        drop_frame = tk.Frame(root, bg="#2A2A2A", relief="flat",
                              width=420, height=140)
        drop_frame.pack(pady=16, padx=30)
        drop_frame.pack_propagate(False)

        drop_label = tk.Label(drop_frame,
                              text="⬇  Drop brief here\nor click to choose",
                              font=("Helvetica", 13), fg="#666666", bg="#2A2A2A",
                              cursor="hand2")
        drop_label.place(relx=0.5, rely=0.5, anchor="center")

        # Log area
        log = scrolledtext.ScrolledText(root, height=8, font=("Courier", 9),
                                        bg="#111111", fg="#AAAAAA",
                                        relief="flat", state="disabled")
        log.pack(padx=30, fill="x")

        def append_log(msg):
            log.configure(state="normal")
            log.insert("end", f"{msg}\n")
            log.see("end")
            log.configure(state="disabled")
            root.update_idletasks()

        def process_file(path_str):
            path = Path(path_str)
            if path.suffix.lower() not in (".pdf", ".json"):
                append_log(f"Unsupported file: {path.suffix}")
                return
            drop_label.configure(text="⏳ Running...", fg="#E8500A")
            threading.Thread(
                target=lambda: run_full_job(path, append_log),
                daemon=True
            ).start()

        def on_click(event=None):
            path = filedialog.askopenfilename(
                title="Choose Brief",
                filetypes=[("Brief files", "*.pdf *.json"), ("PDF", "*.pdf"), ("JSON", "*.json")]
            )
            if path:
                process_file(path)

        drop_label.bind("<Button-1>", on_click)
        drop_frame.bind("<Button-1>", on_click)

        # Native drag and drop via tkinterdnd if available, else show instruction
        try:
            root.tk.call("package", "require", "tkdnd")
            drop_frame.drop_target_register("DND_Files")
            drop_frame.dnd_bind("<<Drop>>", lambda e: process_file(e.data.strip("{}")))
            drop_label.configure(text="⬇  Drop brief here\nor click to choose")
        except Exception:
            drop_label.configure(
                text="📂  Click to choose brief\n(PDF or JSON)",
                fg="#888888"
            )

        root.mainloop()

    def open_search_window(self, _):
        threading.Thread(target=self._show_search_window, daemon=True).start()

    def _show_search_window(self):
        import tkinter as tk
        from tkinter import filedialog, scrolledtext

        root = tk.Tk()
        root.title("POST-01 — Visual Search")
        root.geometry("520x480")
        root.configure(bg="#1A1A1A")
        root.resizable(False, False)

        tk.Label(root, text="VISUAL SEARCH", font=("Helvetica", 16, "bold"),
                 fg="#E8500A", bg="#1A1A1A").pack(pady=(20, 4))
        tk.Label(root, text="Find any car, driver, or moment across your footage",
                 font=("Helvetica", 10), fg="#888888", bg="#1A1A1A").pack()

        # Text search
        tk.Label(root, text="Search query:", font=("Helvetica", 10, "bold"),
                 fg="#CCCCCC", bg="#1A1A1A", anchor="w").pack(fill="x", padx=30, pady=(16, 2))
        query_var = tk.StringVar()
        query_entry = tk.Entry(root, textvariable=query_var, font=("Helvetica", 12),
                               bg="#2A2A2A", fg="white", relief="flat",
                               insertbackground="white")
        query_entry.pack(fill="x", padx=30, ipady=6)
        query_entry.insert(0, "e.g. Porsche #36, driver removing helmet")

        # Reference image
        ref_frame = tk.Frame(root, bg="#1A1A1A")
        ref_frame.pack(fill="x", padx=30, pady=(10, 0))
        tk.Label(ref_frame, text="Or drop a reference screenshot:",
                 font=("Helvetica", 10, "bold"), fg="#CCCCCC", bg="#1A1A1A").pack(anchor="w")
        ref_path_var = tk.StringVar(value="No image selected")
        ref_label = tk.Label(ref_frame, textvariable=ref_path_var,
                             font=("Helvetica", 9), fg="#666666", bg="#2A2A2A",
                             cursor="hand2", pady=8)
        ref_label.pack(fill="x", pady=4)

        ref_image_path = [None]
        def choose_ref(event=None):
            p = filedialog.askopenfilename(
                title="Reference Screenshot",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.tiff")]
            )
            if p:
                ref_image_path[0] = p
                ref_path_var.set(Path(p).name)
                ref_label.configure(fg="#E8500A")
        ref_label.bind("<Button-1>", choose_ref)

        # Footage path
        tk.Label(root, text="Footage folder:", font=("Helvetica", 10, "bold"),
                 fg="#CCCCCC", bg="#1A1A1A", anchor="w").pack(fill="x", padx=30, pady=(10, 2))
        footage_var = tk.StringVar(value=str(WORK_IN_PROGRESS))
        footage_entry = tk.Entry(root, textvariable=footage_var, font=("Helvetica", 10),
                                 bg="#2A2A2A", fg="#AAAAAA", relief="flat")
        footage_entry.pack(fill="x", padx=30, ipady=4)

        # Log
        log = scrolledtext.ScrolledText(root, height=6, font=("Courier", 8),
                                        bg="#111111", fg="#AAAAAA",
                                        relief="flat", state="disabled")
        log.pack(padx=30, fill="x", pady=(10, 0))

        def append_log(msg):
            log.configure(state="normal")
            log.insert("end", f"{msg}\n")
            log.see("end")
            log.configure(state="disabled")
            root.update_idletasks()

        def run_search():
            q = query_var.get().strip()
            if q in ("", "e.g. Porsche #36, driver removing helmet"):
                q = None
            ref = ref_image_path[0]
            footage = footage_var.get().strip()

            if not q and not ref:
                append_log("Enter a search query or select a reference image.")
                return
            if not footage:
                append_log("Enter a footage folder path.")
                return

            append_log(f"Searching: {q or Path(ref).name}")

            def _run():
                cmd = [PYTHON, str(PROJECT / "post01.py"),
                       "--output-dir", str(OUTPUT_FOLDER)]
                if q:
                    cmd += ["--search", q, "--footage", footage]
                else:
                    cmd += ["--search-ref", ref, "--footage", footage]

                result = subprocess.run(cmd, capture_output=True, text=True)
                for line in result.stdout.split("\n"):
                    if line.strip():
                        append_log(line)
                if result.returncode != 0:
                    append_log(f"Error: {result.stderr[-200:]}")
                else:
                    notify("POST-01 Search", "Visual search complete — check outputs")

            threading.Thread(target=_run, daemon=True).start()

        tk.Button(root, text="SEARCH", font=("Helvetica", 12, "bold"),
                  bg="#E8500A", fg="white", relief="flat",
                  command=run_search, pady=8, cursor="hand2").pack(
            fill="x", padx=30, pady=10
        )

        root.mainloop()

    def open_outputs(self, _):
        subprocess.run(["open", str(OUTPUT_FOLDER)])

    def open_wip_folder(self, _):
        subprocess.run(["open", str(WORK_IN_PROGRESS)])

    @rumps.timer(30)
    def check_health(self, _):
        if not QNAP.exists():
            self.set_status("⚠ QNAP offline")
        elif self.observer and self.observer.is_alive():
            self.set_status("Watching...")
        else:
            self.set_status("Ready")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Ensure watch folder exists
    WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
    POST01App().run()
