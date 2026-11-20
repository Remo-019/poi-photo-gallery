# -*- coding: utf-8 -*-
"""地图 POI 照片采集 —— 图形界面。

打包命令见 build.bat。
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import poi_core

APP_NAME = "POIPhotoGallery"          # 配置目录名（%LOCALAPPDATA%\POIPhotoGallery）
DISPLAY_NAME = "POI 照片采集"
APP_VERSION = "1.1"


# ------------------------------------------------------------------ 路径与配置


def base_dir() -> str:
    """exe 所在目录（打包后）；开发时是脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def config_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(root, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


DEFAULT_OUT = os.path.join(base_dir(), "output")


def load_config() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg: dict) -> None:
    try:
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ 主窗口


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{DISPLAY_NAME} v{APP_VERSION}")
        self.geometry("940x780")
        self.minsize(860, 660)
        self.configure(bg="#F4F5F7")

        self.cfg = load_config()
        self.msg_q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.out_dir = self.cfg.get("out") or DEFAULT_OUT
        self.last_run_dir: str | None = None

        self._setup_style()
        self._build_ui()
        self._apply_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._drain)

    def _on_close(self):
        """关闭窗口时把当前参数（含密钥）存下来，下次打开自动填入。"""
        try:
            save_config(self._collect_config())
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def _quiet_save(self):
        try:
            save_config(self._collect_config())
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------- 外观

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        bg, card, line = "#F4F5F7", "#FFFFFF", "#E3E5E9"
        style.configure(".", font=("微软雅黑", 9), background=bg)
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=card, relief="flat")
        style.configure("TLabel", background=card, foreground="#23262B")
        style.configure("Hint.TLabel", background=card, foreground="#8A9099",
                        font=("微软雅黑", 8))
        style.configure("Title.TLabel", background=bg, foreground="#12151A",
                        font=("微软雅黑", 17, "bold"))
        style.configure("Sub.TLabel", background=bg, foreground="#7A8089",
                        font=("微软雅黑", 9))
        style.configure("CardTitle.TLabel", background=card, foreground="#12151A",
                        font=("微软雅黑", 10, "bold"))
        style.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=line,
                        lightcolor=line, darkcolor=line, padding=5)
        style.configure("TCombobox", fieldbackground="#FFFFFF", padding=4)
        style.configure("Accent.TButton", font=("微软雅黑", 10, "bold"),
                        background="#1F6FEB", foreground="#FFFFFF",
                        borderwidth=0, padding=(18, 9))
        style.map("Accent.TButton",
                  background=[("active", "#1A5FCC"), ("disabled", "#B9C4D4")],
                  foreground=[("disabled", "#F0F2F5")])
        style.configure("TButton", font=("微软雅黑", 9), padding=(12, 7),
                        background="#FFFFFF", bordercolor=line)
        style.map("TButton", background=[("active", "#EFF2F6")])
        style.configure("TProgressbar", troughcolor="#EDEFF3", background="#1F6FEB",
                        bordercolor="#EDEFF3", lightcolor="#1F6FEB", darkcolor="#1F6FEB")

    # ---------------------------------------------------------- 布局

    def _card(self, parent, title: str) -> ttk.Frame:
        wrap = ttk.Frame(parent, style="TFrame")
        wrap.pack(fill="x", padx=18, pady=(0, 12))
        inner = ttk.Frame(wrap, style="Card.TFrame", padding=(16, 12))
        inner.pack(fill="x")
        ttk.Label(inner, text=title, style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 10))
        return inner

    def _build_ui(self):
        head = ttk.Frame(self, style="TFrame")
        head.pack(fill="x", padx=18, pady=(16, 12))
        ttk.Label(head, text="地图 POI 照片采集", style="Title.TLabel").pack(anchor="w")
        ttk.Label(head, text="按城市 + 关键词搜出机构，自动把详情页照片按原始分辨率下载到本地",
                  style="Sub.TLabel").pack(anchor="w", pady=(3, 0))

        # ---------------- 参数
        card = self._card(self, "参数")
        card.columnconfigure(1, weight=1)
        card.columnconfigure(3, weight=1)

        ttk.Label(card, text="密钥").grid(row=1, column=0, sticky="w", pady=4)
        self.var_key = tk.StringVar()
        self.ent_key = ttk.Entry(card, textvariable=self.var_key, show="●")
        self.ent_key.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=4)
        self.ent_key.bind("<FocusOut>", lambda _e: self._quiet_save())

        ttk.Label(card, text="数据源").grid(row=1, column=2, sticky="w", pady=4)
        self.var_source = tk.StringVar(value="高德+百度")
        cb = ttk.Combobox(card, textvariable=self.var_source,
                          values=["高德+百度", "高德", "百度"],
                          state="readonly", width=12)
        cb.grid(row=1, column=3, sticky="w", padx=(8, 16), pady=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_source())

        self.var_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(card, text="显示", variable=self.var_show,
                        command=self._toggle_key).grid(row=1, column=4, sticky="w")

        ttk.Label(card, text="城市/区县").grid(row=2, column=0, sticky="w", pady=4)
        self.var_city = tk.StringVar(value="北京市")
        ttk.Entry(card, textvariable=self.var_city).grid(
            row=2, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(card, text="关键词").grid(row=2, column=2, sticky="w", pady=4)
        self.var_kw = tk.StringVar(value="医院")
        ttk.Entry(card, textvariable=self.var_kw).grid(
            row=2, column=3, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(card, text="分类码").grid(row=3, column=0, sticky="w", pady=4)
        self.var_types = tk.StringVar()
        ttk.Entry(card, textvariable=self.var_types).grid(
            row=3, column=1, sticky="ew", padx=(8, 16), pady=4)

        ttk.Label(card, text="数量上限").grid(row=3, column=2, sticky="w", pady=4)
        num = ttk.Frame(card, style="Card.TFrame")
        num.grid(row=3, column=3, sticky="w", padx=(8, 16), pady=4)
        self.var_max = tk.StringVar(value="40")
        ttk.Spinbox(num, from_=0, to=99999, textvariable=self.var_max,
                    width=7).pack(side="left")
        ttk.Label(num, text="个 POI，每个最多", style="TLabel").pack(side="left", padx=(6, 4))
        self.var_ppp = tk.StringVar(value="3")
        ttk.Spinbox(num, from_=0, to=99, textvariable=self.var_ppp,
                    width=4).pack(side="left")
        ttk.Label(num, text="张", style="TLabel").pack(side="left", padx=(3, 0))
        ttk.Label(num, text="（0 = 不限）", style="Hint.TLabel").pack(side="left", padx=(8, 0))

        ttk.Label(card, text="保存到").grid(row=4, column=0, sticky="w", pady=4)
        row4 = ttk.Frame(card, style="Card.TFrame")
        row4.grid(row=4, column=1, columnspan=4, sticky="ew", padx=(8, 0), pady=4)
        row4.columnconfigure(0, weight=1)
        self.var_out = tk.StringVar(value=self.out_dir)
        ttk.Entry(row4, textvariable=self.var_out).grid(row=0, column=0, sticky="ew")
        ttk.Button(row4, text="浏览…", command=self._choose_out).grid(
            row=0, column=1, padx=(8, 0))

        ttk.Label(card, text="高德需「Web服务」Key；百度走公开网页接口，不需要 Key。结果按来源分文件夹存放。",
                  style="Hint.TLabel").grid(row=5, column=0, columnspan=6, sticky="w",
                                            pady=(8, 0))

        # ---------------- 操作
        bar = ttk.Frame(self, style="TFrame")
        bar.pack(fill="x", padx=18, pady=(0, 10))
        self.btn_start = ttk.Button(bar, text="开始抓取", style="Accent.TButton",
                                    command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="挑最优图", command=self._pick).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="生成总览图", command=self._sheet).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="打开文件夹", command=self._open_dir).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="历史记录", command=self._history).pack(side="left", padx=(8, 0))

        self.var_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="完成后自动挑最优图 + 出总览", variable=self.var_auto).pack(
            side="right")

        # ---------------- 进度
        prog = ttk.Frame(self, style="TFrame")
        prog.pack(fill="x", padx=18, pady=(0, 8))
        self.pbar = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.pbar.pack(fill="x")
        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(prog, textvariable=self.var_status, style="Sub.TLabel").pack(
            anchor="w", pady=(4, 0))

        # ---------------- 日志
        logwrap = ttk.Frame(self, style="TFrame")
        logwrap.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.log = tk.Text(logwrap, bg="#171A1F", fg="#D6DAE1", insertbackground="#D6DAE1",
                           relief="flat", wrap="word", font=("Consolas", 9),
                           padx=12, pady=10, spacing1=1, spacing3=1)
        sb = ttk.Scrollbar(logwrap, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("ok", foreground="#6FD08C")
        self.log.tag_configure("err", foreground="#FF7B72")
        self.log.tag_configure("dim", foreground="#8B949E")
        self.log.configure(state="disabled")

        self._sync_source()

    # ---------------------------------------------------------- 交互

    def _toggle_key(self):
        self.ent_key.configure(show="" if self.var_show.get() else "●")

    def _sync_source(self):
        """密钥始终是高德 Key（百度走网页接口，不需要 AK）。"""
        self.var_key.set(self.cfg.get("amap_key", ""))

    def _apply_config(self):
        if self.cfg.get("amap_key"):
            self.var_key.set(self.cfg["amap_key"])
        src = self.cfg.get("source", "高德+百度")
        if src in ("高德", "百度", "高德+百度"):
            self.var_source.set(src)
        self.var_types.set(self.cfg.get("types", "090000"))
        self.var_city.set(self.cfg.get("city", "北京市"))
        self.var_kw.set(self.cfg.get("keywords", "医院"))
        self.var_max.set(str(self.cfg.get("max_poi", 40)))
        self.var_ppp.set(str(self.cfg.get("ppp", 3)))
        self.var_out.set(os.path.normpath(self.cfg.get("out") or DEFAULT_OUT))
        self.var_auto.set(self.cfg.get("auto", True))

    def _collect_config(self) -> dict:
        cfg = dict(self.cfg)
        cfg["amap_key"] = self.var_key.get().strip()
        cfg.update({
            "source": self.var_source.get(),
            "types": self.var_types.get().strip(),
            "city": self.var_city.get().strip(),
            "keywords": self.var_kw.get().strip(),
            "max_poi": int(self.var_max.get().strip() or 0),
            "ppp": int(self.var_ppp.get().strip() or 0),
            "out": os.path.normpath(self.var_out.get().strip() or DEFAULT_OUT),
            "auto": bool(self.var_auto.get()),
        })
        return cfg

    def _choose_out(self):
        d = filedialog.askdirectory(title="选择保存位置", initialdir=self.var_out.get() or ".")
        if d:
            self.var_out.set(os.path.join(d, "output"))

    def _history(self):
        """历史运行记录：扫描输出目录下每次抓取的文件夹。"""
        root = self.var_out.get()
        runs = poi_core.scan_history(root)

        win = tk.Toplevel(self)
        win.title("历史运行记录")
        win.geometry("900x480")
        win.minsize(760, 380)
        win.configure(bg="#F4F5F7")
        win.transient(self)

        ttk.Label(win, text=f"共 {len(runs)} 次抓取　{root}",
                  style="Sub.TLabel").pack(anchor="w", padx=14, pady=(12, 8))

        cols = ("时间", "城市 / 关键词", "高德", "百度", "机构数", "图片数")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        for c, w, anchor in zip(cols, (160, 240, 120, 120, 70, 70),
                                ("w", "w", "center", "center", "center", "center")):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor=anchor)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for r in runs:
            tree.insert("", "end", iid=r["目录"], values=(
                r["时间"], r["标签"],
                r["来源"].get("高德", "—"), r["来源"].get("百度", "—"),
                r["机构数"], r["图片数"]))

        def open_sel(_e=None):
            sel = tree.selection()
            if sel:
                try:
                    os.startfile(sel[0])  # noqa: S606
                except OSError:
                    pass

        tree.bind("<Double-1>", open_sel)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btns, text="打开所选目录", command=open_sel).pack(side="left")
        ttk.Button(btns, text="刷新", command=lambda: (win.destroy(), self._history())).pack(
            side="left", padx=(8, 0))
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right")

        if runs:
            tree.selection_set(runs[0]["目录"])

    def _open_dir(self):
        d = self.last_run_dir or self.var_out.get()
        if not os.path.isdir(d):
            messagebox.showinfo("提示", "目录还不存在，先抓一次。")
            return
        os.startfile(d)  # noqa: S606

    # ---------------------------------------------------------- 日志

    def _println(self, text: str, tag: str = ""):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    tag = ""
                    if payload.startswith("      ✓"):
                        tag = "ok"
                    elif "✗" in payload or "报错" in payload:
                        tag = "err"
                    elif payload.startswith("  "):
                        tag = "dim"
                    self._println(payload, tag)
                elif kind == "progress":
                    cur, total = payload
                    self.pbar.configure(maximum=max(total, 1), value=cur)
                    self.var_status.set(f"正在处理 {cur}/{total}")
                elif kind == "status":
                    self.var_status.set(payload)
                elif kind == "done":
                    self._finish(True, payload)
                elif kind == "error":
                    self._finish(False, payload)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _finish(self, ok: bool, payload):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.worker = None
        if ok:
            self.pbar.configure(value=self.pbar.cget("maximum"))
            self.var_status.set("完成")
            self._println("— 全部完成 —", "ok")
            if payload:
                messagebox.showinfo("完成", payload)
        else:
            self.var_status.set("出错了")
            self._println(str(payload), "err")

    # ---------------------------------------------------------- 任务

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            cfg = self._collect_config()
        except ValueError:
            messagebox.showerror("参数有误", "数量上限和每个 POI 张数必须是数字。")
            return
        if not cfg.get("amap_key") and self.var_source.get() != "百度":
            messagebox.showerror("缺少密钥", "选了高德就需要填「Web服务」Key（百度不需要）。")
            return
        if not cfg["city"] or not cfg["keywords"]:
            messagebox.showerror("参数有误", "城市和关键词不能为空。")
            return

        save_config(cfg)
        self.cfg = cfg
        self.stop_event.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.pbar.configure(value=0)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.var_status.set("开始搜索…")

        self.worker = threading.Thread(target=self._run, args=(cfg, self.var_source.get()),
                                       daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.var_status.set("正在停止…")
        self._println("已请求停止，正在收尾…", "err")

    def _log(self, text: str):
        self.msg_q.put(("log", str(text)))

    def _run(self, cfg: dict, source: str):
        try:
            stop = self.stop_event.is_set

            # 每次抓取单独一个文件夹：20261120_161230_北京市_医院
            stamp = time.strftime("%Y%m%d_%H%M%S")
            tag = poi_core.safe_name(f"{cfg['city']}_{cfg['keywords']}", 30)
            root = os.path.join(cfg["out"], f"{stamp}_{tag}")
            os.makedirs(root, exist_ok=True)
            self.last_run_dir = root

            sources = ["高德", "百度"] if source == "高德+百度" else [source]
            summary_parts = []
            self._log(f"本次输出目录：{root}")
            self._log("")

            for idx, src in enumerate(sources, 1):
                out_dir = os.path.join(root, src)
                os.makedirs(out_dir, exist_ok=True)
                self._log(f"===== [{idx}/{len(sources)}] {src} =====")
                lim = "不限" if not cfg["max_poi"] else f"{cfg['max_poi']} 个"
                per = "全部" if not cfg["ppp"] else f"{cfg['ppp']} 张"
                self._log(f"城市 {cfg['city']} | 关键词 {cfg['keywords']} | POI {lim} | 每个最多 {per}")
                self.msg_q.put(("status", f"{src}：正在搜索…"))

                if src == "高德":
                    items = poi_core.amap_search(
                        cfg["amap_key"], cfg["keywords"], cfg["city"],
                        cfg["types"] or None, cfg["max_poi"], 25, 0.4,
                        log=self._log, should_stop=stop)
                else:
                    items = poi_core.baidu_search(
                        cfg["keywords"], cfg["city"], cfg["max_poi"], 0.6,
                        log=self._log, should_stop=stop)

                if not items:
                    self._log(f"  {src} 没搜到结果，跳过")
                    self._log("")
                    continue

                self._log(f"  {src} 共 {len(items)} 个 POI，开始下载照片…")
                rows = poi_core.download_items(
                    items, out_dir, cfg["ppp"], 5000, 0.3, log=self._log,
                    on_progress=lambda c, t: self.msg_q.put(("progress", (c, t))),
                    should_stop=stop)

                got = sum(r["已下载"] for r in rows)
                summary_parts.append(f"{src}：{len(rows)} 个 POI / {got} 张图")

                if cfg.get("auto"):
                    self.msg_q.put(("status", f"{src}：正在挑最优图…"))
                    st = poi_core.pick_best(out_dir, log=self._log)
                    median = st.get("median", 0)
                    if median and median < 800:
                        self._log(f"  ! 中位宽度只有 {median}px，原图参数可能已失效，"
                                  f"这次拿到的多半是缩略图。")
                    self.msg_q.put(("status", f"{src}：正在生成总览图…"))
                    poi_core.build_sheet_paged(out_dir, per_poi=3, per_page=24, log=self._log)
                self._log("")

            if not summary_parts:
                raise RuntimeError("两个来源都没搜到结果。检查城市名（建议用标准区县名）或关键词。")

            summary = "\n".join(summary_parts) + f"\n\n保存位置：{root}"
            self.msg_q.put(("done", summary))
        except poi_core.StopRequested:
            self.msg_q.put(("error", "已停止。已下载的文件保留在输出目录。"))
        except Exception as exc:  # noqa: BLE001
            self.msg_q.put(("error", f"{exc}"))
            self._log(traceback.format_exc(), "")

    # ---------------------------------------------------------- 辅助按钮

    def _target_dirs(self) -> list[str]:
        """返回有 manifest.csv 的目录（可能是本次运行目录下的 高德/ 百度/）。"""
        root = self.last_run_dir or self.var_out.get()
        if os.path.exists(os.path.join(root, "manifest.csv")):
            return [root]
        out = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                sub = os.path.join(root, name)
                if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "manifest.csv")):
                    out.append(sub)
        return out

    def _pick(self):
        dirs = self._target_dirs()
        if not dirs:
            messagebox.showinfo("提示", "还没抓过数据。先点「开始抓取」。")
            return

        def job():
            try:
                for d in dirs:
                    poi_core.pick_best(d, log=self._log)
                self.msg_q.put(("done", f"最优图已挑好（{len(dirs)} 个来源），见各自 best/ 目录"))
            except Exception as exc:  # noqa: BLE001
                self.msg_q.put(("error", str(exc)))

        self.btn_start.configure(state="disabled")
        threading.Thread(target=job, daemon=True).start()

    def _sheet(self):
        dirs = self._target_dirs()
        if not dirs:
            messagebox.showinfo("提示", "还没抓过数据。先点「开始抓取」。")
            return

        def job():
            try:
                paths = []
                for d in dirs:
                    paths.extend(poi_core.build_sheet_paged(
                        d, per_poi=3, per_page=24, log=self._log))
                self.msg_q.put(("done", f"总览图已生成（{len(paths)} 页，每店 3 张）：\n"
                                        + "\n".join(paths)))
                os.startfile(paths[0])  # noqa: S606
            except Exception as exc:  # noqa: BLE001
                self.msg_q.put(("error", str(exc)))

        self.btn_start.configure(state="disabled")
        threading.Thread(target=job, daemon=True).start()


class _Null:
    """打包成无控制台程序后 sys.stdout 为 None，print 会炸，这里兜一层。"""

    def write(self, *_a):
        return 0

    def flush(self):
        return None


if sys.stdout is None:
    sys.stdout = _Null()
if sys.stderr is None:
    sys.stderr = _Null()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        print(f"{DISPLAY_NAME} v{APP_VERSION}")
        print("poi_core OK:", hasattr(poi_core, "amap_search"))
        try:
            from PIL import Image  # noqa: F401
            print("Pillow OK")
        except ImportError as exc:
            print("Pillow 缺失:", exc)
        print("配置目录:", os.path.dirname(config_path()))
        print("默认输出:", DEFAULT_OUT)
        return

    app = App()
    try:
        icon = resource_path("icon.ico")
        if os.path.exists(icon):
            app.iconbitmap(icon)
    except Exception:  # noqa: BLE001
        pass
    app.mainloop()


if __name__ == "__main__":
    main()
