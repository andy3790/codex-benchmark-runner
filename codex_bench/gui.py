"""Native Tkinter desktop UI; no third-party runtime dependencies."""
from __future__ import annotations

import argparse
import copy
import json
import queue
import sys
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .config import default_config_path
from .gui_controller import BenchmarkController
from .gui_model import ConfigDocument
from .runner import TERMINAL, read_results
from .util import BenchError

STATUS = {"pending": "대기", "running": "생성 중", "grading": "채점 중", "succeeded": "정상 종료",
          "failed": "실패", "unsupported": "미지원", "timed_out": "시간 초과", "interrupted": "중단됨"}
ACTION = {"doctor": "환경 진단", "dry-run": "dry-run", "mock": "모의 실행", "run": "실제 실행"}


class BenchmarkApp:
    def __init__(self, root: tk.Tk, config_path: Path | None = None):
        self.root = root
        self.controller = BenchmarkController()
        self.document = None
        self.variables = {}
        self.models = []
        self.rows = {}
        self.edit_widgets = []
        self.current_output = None
        self.closing = False
        self.active_action = None
        self.root.title("Codex Benchmark Runner")
        self.root.geometry("1280x850")
        self.root.minsize(1060, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self.poll_id = self.root.after(100, self._poll)
        self.root.bind("<Destroy>", self._cancel_poll, add=True)
        if config_path and config_path.is_file():
            self.load(config_path)
        else:
            self.status.set("설정 파일을 열어 시작하세요")

    def _style(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.root.configure(background="#f3f5f8")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name, root=self.root).configure(family="Segoe UI", size=10)
        self.style.configure("TFrame", background="#f3f5f8")
        self.style.configure("TLabel", background="#f3f5f8", foreground="#182c45")
        self.style.configure("TLabelframe", background="#f3f5f8")
        self.style.configure("TLabelframe.Label", background="#f3f5f8", foreground="#30465f")
        self.style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        self.style.configure("Muted.TLabel", foreground="#52687d")
        self.style.configure("TButton", padding=(10, 7))
        self.style.configure("Primary.TButton", background="#176b62", foreground="white")
        self.style.map("Primary.TButton", background=[("active", "#11564f"), ("disabled", "#879b97")])
        self.style.configure("Treeview", rowheight=28, background="white", fieldbackground="white")
        self.style.configure("Treeview.Heading", padding=(5, 6))
        self.style.configure("TNotebook", background="#f3f5f8")

    def _button(self, parent, text, command, **kwargs):
        button = ttk.Button(parent, text=text, command=command, **kwargs)
        self.edit_widgets.append(button)
        return button

    def _build(self):
        shell = ttk.Frame(self.root, padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)
        ttk.Label(shell, text="Codex Benchmark Runner", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(shell, text="모델과 추론 강도를 설정하고, 실행 기록과 독립 채점 결과를 비교하세요.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 14))
        files = ttk.Frame(shell)
        files.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.path_label = tk.StringVar(value="설정 파일을 선택하세요")
        ttk.Label(files, textvariable=self.path_label, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        self._button(files, "설정 열기…", self.choose_config).pack(side="left", padx=4)
        self._button(files, "설정·프롬프트 저장", self.save).pack(side="left")
        panes = ttk.Panedwindow(shell, orient="horizontal")
        panes.grid(row=3, column=0, sticky="nsew")
        left = ttk.Notebook(panes)
        panes.add(left, weight=4)
        right = ttk.Frame(panes, padding=(16, 0, 0, 0))
        panes.add(right, weight=6)
        tabs = [ttk.Frame(left) for _ in range(4)]
        for tab, label in zip(tabs, ("모델·실행", "파일·채점", "시간·제한", "프롬프트")):
            left.add(tab, text=label)
        model_tab, paths_tab, limits_tab = [self._scrolling_form(tab) for tab in tabs[:3]]
        prompt_tab = ttk.Frame(tabs[3], padding=12)
        prompt_tab.pack(fill="both", expand=True)
        self._models_tab(model_tab)
        self._paths_tab(paths_tab)
        self._limits_tab(limits_tab)
        self.prompt = ScrolledText(prompt_tab, wrap="word", undo=True, font=("Segoe UI", 10), relief="flat", padx=10, pady=10)
        self.prompt.pack(fill="both", expand=True)
        self.edit_widgets.append(self.prompt)
        self._results_panel(right)
        actions = ttk.Frame(shell)
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 7))
        self._button(actions, "환경 진단", lambda: self.start("doctor")).pack(side="left", padx=(0, 6))
        self._button(actions, "dry-run", lambda: self.start("dry-run")).pack(side="left", padx=(0, 6))
        self._button(actions, "모의 실행 · 토큰 0", lambda: self.start("mock"), style="Primary.TButton").pack(side="left", padx=(0, 6))
        self._button(actions, "실제 실행 / 재개", lambda: self.start("run")).pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(actions, text="중단", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.report_button = ttk.Button(actions, text="HTML 보고서 열기", command=self.open_report)
        self.report_button.pack(side="right")
        self.status = tk.StringVar(value="준비됨")
        self.status_label = ttk.Label(shell, textvariable=self.status, wraplength=970)
        self.status_label.grid(row=5, column=0, sticky="w")
        ttk.Label(shell, text="모의 실행은 합성 데이터입니다. 실제 실행 버튼은 Codex 사용량을 발생시킵니다.", style="Muted.TLabel").grid(row=6, column=0, sticky="w", pady=(4, 0))

    def _scrolling_form(self, parent):
        canvas = tk.Canvas(parent, background="#f3f5f8", highlightthickness=0, width=450)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=12)
        window = canvas.create_window(0, 0, anchor="nw", window=inner)
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        return inner

    def _models_tab(self, parent):
        ttk.Label(parent, text="모델별 추론 강도", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
        self.model_tree = ttk.Treeview(parent, columns=("model", "efforts"), show="headings", height=6, selectmode="browse")
        self.model_tree.heading("model", text="모델")
        self.model_tree.heading("efforts", text="추론 강도")
        self.model_tree.column("model", width=170, minwidth=100)
        self.model_tree.column("efforts", width=190, minwidth=100)
        self.model_tree.pack(fill="both", expand=True)
        self.model_tree.bind("<Double-1>", lambda event: self.edit_model())
        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=8)
        for label, command in [("추가", lambda: self.edit_model(new=True)), ("수정", self.edit_model), ("삭제", self.remove_model)]:
            self._button(buttons, label, command).pack(side="left", padx=(0, 5))
        self._field(parent, "반복 횟수", "repetitions")
        self._field(parent, "실행 순서", "mode", choices=("sequential", "parallel"))
        self._field(parent, "최대 동시 실행", "max_workers")
        self._field(parent, "격리 정책", "isolation", choices=("restricted", "workspace-write"))
        ttk.Label(parent, text="추론 강도는 쉼표로 구분합니다.\n예: low, medium, high, xhigh\n지원 여부는 실제 CLI 응답에 따라 기록합니다.", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        self.plan_label = tk.StringVar(value="조합 0개")
        ttk.Label(parent, textvariable=self.plan_label).pack(anchor="w", pady=(8, 0))

    def _field(self, parent, label, key, choices=None, browse=None, readonly=False):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label, width=16).pack(side="left")
        variable = tk.StringVar()
        self.variables[key] = variable
        if choices:
            widget = ttk.Combobox(row, textvariable=variable, values=choices, state="readonly", width=16)
        else:
            widget = ttk.Entry(row, textvariable=variable, state="readonly" if readonly else "normal", width=20)
        widget._restore_state = "readonly" if choices or readonly else "normal"
        widget.pack(side="left", fill="x", expand=True)
        self.edit_widgets.append(widget)
        if browse:
            self._button(row, "…", lambda: self.browse(key, browse)).pack(side="left", padx=(5, 0))
        if key == "repetitions":
            variable.trace_add("write", lambda *_: self.update_plan())

    def _paths_tab(self, parent):
        for label, key, kind in [("프롬프트 파일", "prompt_file", "file"), ("시작 파일 폴더", "starter_dir", "dir"),
                                  ("결과 저장 폴더", "output_dir", "dir"), ("작업 폴더 (선택)", "scratch_dir", "dir"),
                                  ("채점기 폴더", "grader.plugin_dir", "dir"), ("Codex 실행 파일", "codex_executable", "file")]:
            self._field(parent, label, key, browse=kind, readonly=key == "prompt_file")
        self._field(parent, "Docker 이미지", "docker_image")
        ttk.Label(parent, text="상대 경로의 기준은 설정 파일 폴더입니다.\n작업 폴더를 비우면 시스템 기본 경로를 사용합니다.\n프롬프트 내용은 프롬프트 탭에서 편집할 수 있습니다.", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))

    def _limits_tab(self, parent):
        for label, key in [("실험 최대 횟수", "max_experiments"), ("실행당 제한 (초)", "run_timeout_seconds"),
                            ("전체 제한 (초)", "total_timeout_seconds"), ("테스트 제한 (초)", "grader.test_timeout_seconds"),
                            ("채점 전체 (초)", "grader.timeout_seconds"), ("출력 한도 (bytes)", "max_output_bytes"),
                            ("사본 한도 (bytes)", "max_project_bytes")]:
            self._field(parent, label, key)
        ttk.Label(parent, text="전체 제한은 이번 실행의 벽시계 시간입니다.\n미완료 작업은 같은 설정으로 재개할 수 있습니다.", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))

    def _results_panel(self, parent):
        heading = ttk.Frame(parent)
        heading.pack(fill="x")
        ttk.Label(heading, text="진행 상황과 실행 결과", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(heading, text="저장 결과 새로고침", command=self.refresh_results).pack(side="right")
        self.counter = tk.StringVar(value="완료 0 / 0  ·  실패 0  ·  실행 중 0")
        ttk.Label(parent, textvariable=self.counter).pack(anchor="w", pady=(12, 6))
        self.progress_bar = ttk.Progressbar(parent, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 12))
        panes = ttk.Panedwindow(parent, orient="vertical")
        panes.pack(fill="both", expand=True)
        table = ttk.Frame(panes)
        panes.add(table, weight=3)
        columns = ("model", "effort", "repeat", "status", "score", "tokens", "seconds")
        self.result_tree = ttk.Treeview(table, columns=columns, show="headings", height=9, selectmode="browse")
        for key, label, width in zip(columns, ("모델", "추론", "반복", "상태", "점수", "총 토큰", "시간(초)"), (140, 65, 44, 80, 50, 70, 70)):
            self.result_tree.heading(key, text=label)
            self.result_tree.column(key, width=width, minwidth=width, stretch=key == "model", anchor="w" if key in {"model", "effort", "status"} else "e")
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.result_tree.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.result_tree.xview)
        self.result_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.result_tree.tag_configure("failure", foreground="#a52b37")
        self.result_tree.tag_configure("success", foreground="#176b62")
        self.result_tree.bind("<<TreeviewSelect>>", self.show_detail)
        details = ttk.Notebook(panes)
        panes.add(details, weight=2)
        self.log_text = ScrolledText(details, height=9, wrap="word", font=("Consolas", 9), state="disabled", relief="flat")
        self.detail_text = ScrolledText(details, height=9, wrap="word", font=("Consolas", 9), state="disabled", relief="flat")
        details.add(self.log_text, text="실행 로그")
        details.add(self.detail_text, text="선택한 실험 상세")

    def _form(self):
        return {**{key: variable.get() for key, variable in self.variables.items()}, "models": copy.deepcopy(self.models)}

    def _prompt(self):
        return self.prompt.get("1.0", "end-1c")

    def load(self, path):
        try:
            document = ConfigDocument.open(Path(path))
            self.document = document
            self.path_label.set(str(document.path))
            self.models = copy.deepcopy(document.original_form["models"])
            for key, variable in self.variables.items():
                variable.set(document.original_form[key])
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", document.original_prompt)
            self.update_model_tree()
            cfg = self.preview()
            self.current_output = cfg.output
            self.controller.last_report = cfg.output / "report.html" if (cfg.output / "report.html").exists() else None
            self.render_rows(read_results(cfg))
            self.status.set("준비됨 · 모의 실행과 dry-run은 실제 모델을 호출하지 않습니다")
        except (BenchError, OSError, ValueError, TypeError) as exc:
            self._error(exc)

    def choose_config(self):
        if self.document and (self._form() != self.document.original_form or self._prompt() != self.document.original_prompt):
            answer = messagebox.askyesnocancel("편집 내용 저장", "열려 있는 설정의 변경 내용을 저장할까요?", parent=self.root)
            if answer is None or answer and not self.save():
                return
        name = filedialog.askopenfilename(parent=self.root, title="벤치마크 설정 열기", filetypes=[("JSON 설정", "*.json")])
        if name:
            self.load(Path(name))

    def browse(self, key, kind):
        if self.document is None:
            return
        if kind == "dir":
            name = filedialog.askdirectory(parent=self.root, initialdir=str(self.document.path.parent), mustexist=False)
        else:
            name = filedialog.askopenfilename(parent=self.root, initialdir=str(self.document.path.parent))
        if name:
            try:
                if key == "prompt_file":
                    text = Path(name).read_text(encoding="utf-8-sig")
                    self.prompt.delete("1.0", "end")
                    self.prompt.insert("1.0", text)
                self.variables[key].set(name)
            except (OSError, ValueError) as exc:
                self._error(exc)

    def preview(self):
        if self.document is None:
            raise BenchError("먼저 설정 파일을 여세요")
        return self.document.build(self._form(), self._prompt())

    def save(self):
        try:
            if self.document is None:
                raise BenchError("먼저 설정 파일을 여세요")
            cfg = self.document.save(self._form(), self._prompt())
            for key, variable in self.variables.items():
                variable.set(self.document.original_form[key])
            self.models = copy.deepcopy(self.document.original_form["models"])
            self.update_model_tree()
            self.current_output = cfg.output
            self.status.set("설정과 프롬프트를 저장했습니다")
            return True
        except (BenchError, OSError, ValueError, TypeError) as exc:
            self._error(exc)
            return False

    def update_model_tree(self):
        self.model_tree.delete(*self.model_tree.get_children())
        for index, model in enumerate(self.models):
            self.model_tree.insert("", "end", iid=str(index), values=(model["name"], ", ".join(model["reasoning_efforts"])))
        self.update_plan()

    def update_plan(self):
        if not hasattr(self, "plan_label"):
            return
        try:
            count = sum(len(model["reasoning_efforts"]) for model in self.models) * int(self.variables["repetitions"].get())
            self.plan_label.set(f"전체 실행 예정: {count}회")
        except (KeyError, ValueError):
            self.plan_label.set("반복 횟수를 확인하세요")

    def edit_model(self, new=False):
        if self.controller.busy:
            return
        selected = self.model_tree.selection()
        if not new and not selected:
            return
        index = len(self.models) if new else int(selected[0])
        current = {"name": "", "reasoning_efforts": ["low", "medium", "high"]} if new else self.models[index]
        dialog = tk.Toplevel(self.root)
        dialog.title("모델 추가" if new else "모델 수정")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        name = tk.StringVar(value=current["name"])
        efforts = tk.StringVar(value=", ".join(current["reasoning_efforts"]))
        for label, variable in [("모델 이름", name), ("추론 강도 (쉼표 구분)", efforts)]:
            ttk.Label(frame, text=label).pack(anchor="w", pady=(0, 4))
            ttk.Entry(frame, textvariable=variable, width=45).pack(fill="x", pady=(0, 12))

        def apply():
            value = {"name": name.get().strip(), "reasoning_efforts": [e.strip() for e in efforts.get().split(",") if e.strip()]}
            if not value["name"] or not value["reasoning_efforts"]:
                messagebox.showerror("입력 확인", "모델 이름과 추론 강도를 입력하세요", parent=dialog)
                return
            if new:
                self.models.append(value)
            else:
                self.models[index] = value
            self.update_model_tree()
            dialog.destroy()

        ttk.Button(frame, text="적용", command=apply).pack(side="right")
        ttk.Button(frame, text="취소", command=dialog.destroy).pack(side="right", padx=6)
        dialog.bind("<Return>", lambda event: apply())
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.grab_set()

    def remove_model(self):
        selected = self.model_tree.selection()
        if selected:
            del self.models[int(selected[0])]
            self.update_model_tree()

    def start(self, action):
        if self.controller.busy:
            return
        try:
            cfg = self.preview()
            if action == "run":
                if not self.save():
                    return
                cfg = self.preview()
            self.active_action = action
            if action == "mock":
                self.current_output = None
            self._set_busy(True)
            self.render_rows([{**job.as_dict(), "status": "pending", "usage": {}} for job in cfg.jobs])
            self._append_log(f"\n── {ACTION[action]} · {len(cfg.jobs)}회 · {cfg.workers}개 프로세스 ──")
            self.status.set(f"{ACTION[action]} 진행 중")
            self.controller.start(action, cfg)
        except (BenchError, OSError, ValueError, TypeError) as exc:
            self._set_busy(False)
            self._error(exc)

    def stop(self):
        self.controller.stop()
        self.stop_button.configure(state="disabled")
        self.status.set("중단 요청됨 · 프로세스를 종료하고 이미 완료된 결과를 보존합니다")

    def _set_busy(self, busy):
        for widget in self.edit_widgets:
            widget.configure(state="disabled" if busy else getattr(widget, "_restore_state", "normal"))
        self.stop_button.configure(state="normal" if busy else "disabled")

    def render_rows(self, rows):
        selected = self.result_tree.selection()
        self.rows = {row["id"]: row for row in rows}
        self.result_tree.delete(*self.result_tree.get_children())
        for row in rows:
            grade, usage = row.get("grade") or {}, row.get("usage") or {}
            score = grade.get("score")
            seconds = row.get("generation_seconds")
            values = (row["model"], row["effort"], row["repeat"], STATUS.get(row["status"], row["status"]),
                      "—" if score is None else f"{score:g}", "—" if usage.get("total_tokens") is None else f"{usage['total_tokens']:,}",
                      "—" if seconds is None else f"{seconds:.2f}")
            tag = "success" if row["status"] == "succeeded" else "failure" if row["status"] in TERMINAL else ""
            self.result_tree.insert("", "end", iid=row["id"], values=values, tags=(tag,))
        if selected and selected[0] in self.rows:
            self.result_tree.selection_set(selected[0])
        complete = sum(row["status"] in TERMINAL for row in rows)
        failed = sum(row["status"] in TERMINAL - {"succeeded"} for row in rows)
        running = [row for row in rows if row["status"] in {"running", "grading"}]
        self.counter.set(f"완료 {complete} / {len(rows)}  ·  실패 {failed}  ·  실행 중 {len(running)}")
        self.progress_bar.configure(maximum=max(1, len(rows)), value=complete)
        if running and not self.controller.cancel_event.is_set():
            self.status.set("진행 중: " + ", ".join(f"{r['model']} / {r['effort']}" for r in running))

    def show_detail(self, event=None):
        selected = self.result_tree.selection()
        if selected and selected[0] in self.rows:
            self.detail_text.configure(state="normal")
            self.detail_text.delete("1.0", "end")
            self.detail_text.insert("1.0", json.dumps(self.rows[selected[0]], ensure_ascii=False, indent=2))
            self.detail_text.configure(state="disabled")

    def refresh_results(self):
        try:
            if self.current_output and (self.current_output / "manifest.json").is_file():
                self._read_output(self.current_output)
            else:
                cfg = self.preview()
                self.current_output = cfg.output
                self.render_rows(read_results(cfg))
                if (cfg.output / "report.html").is_file():
                    self.controller.last_report = cfg.output / "report.html"
        except (BenchError, OSError, ValueError, TypeError) as exc:
            self._error(exc)

    def _read_output(self, output):
        manifest = output / "manifest.json"
        if not manifest.is_file():
            return
        data = json.loads(manifest.read_text(encoding="utf-8"))
        rows = []
        for job in data["jobs"]:
            path = output / "runs" / job["id"] / "result.json"
            rows.append(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {**job, "status": "pending", "usage": {}})
        self.render_rows(rows)

    def open_report(self):
        path = self.controller.last_report
        if path is None or not path.is_file():
            self._error("아직 생성된 보고서가 없습니다. 실행 완료 후 다시 여세요.")
            return
        try:
            if not webbrowser.open(path.resolve().as_uri()):
                raise BenchError(f"기본 브라우저를 열지 못했습니다. 파일 위치: {path}")
        except (BenchError, OSError) as exc:
            self._error(exc)

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 2500:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _error(self, error):
        self.status.set(str(error))
        self._append_log("오류: " + str(error))
        messagebox.showerror("확인 필요", str(error), parent=self.root)

    def _poll(self):
        for _ in range(80):
            try:
                kind, value = self.controller.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(value)
            elif kind == "progress":
                self.render_rows(value)
            elif kind == "output":
                self.current_output = Path(value)
            elif kind == "error":
                self.status.set(value)
            elif kind == "finished":
                self._set_busy(False)
                message = "중단됨 · 같은 설정으로 미완료 작업을 재개할 수 있습니다" if value["exit_code"] == 130 else f"{ACTION[value['action']]} 종료 · 코드 {value['exit_code']}"
                self.status.set(message)
                if value["report"]:
                    self._append_log(f"보고서: {value['report']}")
                if self.closing:
                    if self._finish_close():
                        return
        if self.controller.busy and self.active_action in {"mock", "run"} and self.current_output:
            try:
                self._read_output(self.current_output)
            except (OSError, ValueError):
                pass  # An atomic writer may briefly own the Windows file handle.
        self.poll_id = self.root.after(200, self._poll)

    def _cancel_poll(self, event):
        if event.widget is self.root and self.poll_id is not None:
            try:
                self.root.after_cancel(self.poll_id)
            except tk.TclError:
                pass
            self.poll_id = None

    def close(self):
        if self.controller.busy:
            self.closing = True
            self.stop()
            self.status.set("종료 준비 중 · 결과를 저장한 뒤 창을 닫습니다")
            return
        self._finish_close()

    def _finish_close(self):
        self.closing = False
        if self.document and (self._form() != self.document.original_form or self._prompt() != self.document.original_prompt):
            answer = messagebox.askyesnocancel("편집 내용 저장", "변경한 설정과 프롬프트를 저장할까요?", parent=self.root)
            if answer is None or answer and not self.save():
                return False
        self.root.destroy()
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex Benchmark Runner desktop GUI")
    parser.add_argument("--config", type=Path, help="Settings file (default: config.local.json if present, otherwise config.json)")
    args = parser.parse_args(argv)
    if args.config is None:
        args.config = default_config_path()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"GUI를 시작할 수 없습니다. 데스크톱 환경과 Python의 Tcl/Tk 설치를 확인하세요: {exc}", file=sys.stderr)
        return 2
    app = BenchmarkApp(root, args.config)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if app.controller.busy:
            app.controller.stop()
            app.controller.thread.join()
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
