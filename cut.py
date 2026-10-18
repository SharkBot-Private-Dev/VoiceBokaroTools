from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pydub import AudioSegment
from pydub.silence import detect_nonsilent

def is_pyinstaller():
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

def get_ffmpeg_path():
    if not is_pyinstaller():
        return r"C:\ffmpeg\bin\ffmpeg.exe"

    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    ext = '.exe' if sys.platform.startswith('win') else ''
    return os.path.join(base_path, f'ffmpeg{ext}')

APP_TITLE = "Voice Segment Cutter"
DEFAULT_FFMPEG = get_ffmpeg_path()
DEFAULT_INPUT_NAME = "example.wav"


def configure_ffmpeg() -> None:
    """Configure pydub to use FFmpeg when it is available."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg and Path(DEFAULT_FFMPEG).exists():
        ffmpeg = DEFAULT_FFMPEG
    if ffmpeg:
        AudioSegment.converter = ffmpeg


class VoiceSegmentCutter:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x780")
        self.root.minsize(850, 620)

        self.audio: AudioSegment | None = None
        self.audio_path: Path | None = None
        self.segments: list[list[int]] = []  # [start_ms, end_ms]
        self.play_process: subprocess.Popen | None = None
        self.play_temp: str | None = None

        self.threshold_var = tk.StringVar(value="-40")
        self.min_silence_var = tk.StringVar(value="180")
        self.padding_var = tk.StringVar(value="40")
        self.start_var = tk.StringVar(value="0.000")
        self.end_var = tk.StringVar(value="0.000")
        self.status_var = tk.StringVar(value="音声ファイルを開いてください。")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        initial = Path.cwd() / DEFAULT_INPUT_NAME
        if initial.exists():
            self.load_audio(initial)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Button(top, text="音声ファイルを開く", command=self.choose_audio).pack(side="left")
        self.path_label = ttk.Label(top, text="未選択", width=80)
        self.path_label.pack(side="left", padx=10, fill="x", expand=True)

        settings = ttk.LabelFrame(self.root, text="無音検出設定", padding=8)
        settings.pack(fill="x", padx=10, pady=5)
        ttk.Label(settings, text="無音しきい値 (dBFS)").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.threshold_var, width=9).grid(row=0, column=1, padx=(5, 16))
        ttk.Label(settings, text="無音の最小長さ (ms)").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.min_silence_var, width=9).grid(row=0, column=3, padx=(5, 16))
        ttk.Label(settings, text="前後の余白 (ms)").grid(row=0, column=4, sticky="w")
        ttk.Entry(settings, textvariable=self.padding_var, width=9).grid(row=0, column=5, padx=(5, 16))
        ttk.Button(settings, text="音声区間を再検出", command=self.detect_segments).grid(row=0, column=6, padx=5)
        ttk.Label(
            settings,
            text="しきい値を -35 などにすると小さな音を無視しやすくなります。検出結果は必ず確認してください。",
            wraplength=980,
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(7, 0))

        wave_frame = ttk.LabelFrame(self.root, text="波形（クリックすると、その位置を含む区間を選択）", padding=5)
        wave_frame.pack(fill="x", padx=10, pady=5)
        self.canvas = tk.Canvas(wave_frame, height=170, background="#171a21", highlightthickness=0)
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Button-1>", self.on_wave_click)
        self.canvas.bind("<Configure>", lambda _event: self.draw_waveform())

        body = ttk.Frame(self.root, padding=(10, 4))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="検出された区間").pack(anchor="w")
        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(list_frame, height=12, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select_segment)

        controls = ttk.LabelFrame(body, text="選択区間の編集", padding=10)
        controls.pack(side="left", fill="y", padx=(12, 0))
        ttk.Label(controls, text="開始 (秒)").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.start_var, width=13).grid(row=0, column=1, pady=4)
        ttk.Label(controls, text="終了 (秒)").grid(row=1, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.end_var, width=13).grid(row=1, column=1, pady=4)
        ttk.Button(controls, text="変更を適用", command=self.apply_edit).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 3))
        ttk.Button(controls, text="この区間を再生", command=self.play_selected).grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(controls, text="区間を追加", command=self.add_segment).grid(row=4, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(controls, text="選択区間を削除", command=self.delete_selected).grid(row=5, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Separator(controls).grid(row=6, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Button(controls, text="選択区間を MP3 保存", command=self.export_selected).grid(row=7, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Button(controls, text="全区間を MP3 保存", command=self.export_all).grid(row=8, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Label(controls, text="全区間保存時は 001.mp3、002.mp3…\nの連番で出力します。", justify="left").grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))

        status = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=5)
        status.pack(fill="x", side="bottom")

    def choose_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="音声ファイルを選択",
            filetypes=[("音声ファイル", "*.wav *.mp3 *.flac *.ogg *.m4a"), ("すべてのファイル", "*.*")]
        )
        if path:
            self.load_audio(Path(path))

    def load_audio(self, path: Path) -> None:
        try:
            configure_ffmpeg()
            audio = AudioSegment.from_file(str(path))
        except Exception as exc:
            messagebox.showerror("読み込みエラー", f"音声を開けませんでした。\n\n{exc}")
            return
        self.audio = audio
        self.audio_path = path
        self.path_label.configure(text=f"{path}  ({len(audio) / 1000:.2f} 秒)")
        self.segments = []
        self.listbox.delete(0, tk.END)
        self.draw_waveform()
        self.status_var.set("音声を読み込みました。「音声区間を再検出」を押してください。")
        self.detect_segments()

    def detect_segments(self) -> None:
        if self.audio is None:
            messagebox.showinfo("音声未選択", "先に音声ファイルを開いてください。")
            return
        try:
            threshold = float(self.threshold_var.get())
            min_silence = int(self.min_silence_var.get())
            padding = int(self.padding_var.get())
            if min_silence < 1 or padding < 0:
                raise ValueError("無音の最小長さは1以上、余白は0以上にしてください。")
        except ValueError as exc:
            messagebox.showerror("設定エラー", f"検出設定を確認してください。\n{exc}")
            return

        mono = self.audio.set_channels(1)
        found = detect_nonsilent(
            mono,
            min_silence_len=min_silence,
            silence_thresh=threshold,
            seek_step=5,
        )
        duration = len(self.audio)
        padded: list[list[int]] = []
        for start, end in found:
            start = max(0, start - padding)
            end = min(duration, end + padding)
            if padded and start <= padded[-1][1]:
                padded[-1][1] = max(padded[-1][1], end)
            else:
                padded.append([start, end])

        self.segments = padded
        self.refresh_list()
        self.status_var.set(f"{len(self.segments)} 区間を検出しました。短すぎる区間や複数音がつながった区間を確認してください。")
        self.draw_waveform()

    def refresh_list(self, select_index: int | None = None) -> None:
        self.listbox.delete(0, tk.END)
        for i, (start, end) in enumerate(self.segments, 1):
            self.listbox.insert(tk.END, f"{i:03d}   {start / 1000:.3f} – {end / 1000:.3f} 秒   ({(end-start)/1000:.3f} 秒)")
        if self.segments:
            idx = 0 if select_index is None else max(0, min(select_index, len(self.segments) - 1))
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            self.on_select_segment()
        else:
            self.start_var.set("0.000")
            self.end_var.set("0.000")
        self.draw_waveform()

    def selected_index(self) -> int | None:
        selected = self.listbox.curselection()
        return int(selected[0]) if selected else None

    def on_select_segment(self, _event=None) -> None:
        idx = self.selected_index()
        if idx is None or idx >= len(self.segments):
            return
        start, end = self.segments[idx]
        self.start_var.set(f"{start / 1000:.3f}")
        self.end_var.set(f"{end / 1000:.3f}")
        self.draw_waveform()

    def apply_edit(self) -> None:
        idx = self.selected_index()
        if self.audio is None or idx is None:
            return
        try:
            start = round(float(self.start_var.get()) * 1000)
            end = round(float(self.end_var.get()) * 1000)
            if start < 0 or end > len(self.audio) or start >= end:
                raise ValueError("開始・終了位置が不正です。")
        except ValueError as exc:
            messagebox.showerror("入力エラー", str(exc) or "秒数を数値で入力してください。")
            return
        self.segments[idx] = [start, end]
        self.segments.sort(key=lambda pair: pair[0])
        new_idx = self.segments.index([start, end])
        self.refresh_list(new_idx)
        self.status_var.set("区間を更新しました。")

    def add_segment(self) -> None:
        if self.audio is None:
            return
        try:
            start = round(float(self.start_var.get()) * 1000)
            end = round(float(self.end_var.get()) * 1000)
            if start < 0 or end > len(self.audio) or start >= end:
                raise ValueError("開始・終了位置が不正です。")
        except ValueError as exc:
            messagebox.showerror("入力エラー", str(exc) or "秒数を数値で入力してください。")
            return
        self.segments.append([start, end])
        self.segments.sort(key=lambda pair: pair[0])
        self.refresh_list(self.segments.index([start, end]))
        self.status_var.set("区間を追加しました。")

    def delete_selected(self) -> None:
        idx = self.selected_index()
        if idx is None:
            return
        del self.segments[idx]
        self.refresh_list(min(idx, len(self.segments) - 1) if self.segments else None)
        self.status_var.set("選択区間を削除しました。")

    def play_selected(self) -> None:
        idx = self.selected_index()
        if self.audio is None or idx is None:
            return
        ffplay = shutil.which("ffplay")
        if not ffplay:
            candidate = Path(DEFAULT_FFMPEG).with_name("ffplay.exe")
            if candidate.exists():
                ffplay = str(candidate)
        if not ffplay:
            messagebox.showerror(
                "ffplay が見つかりません",
                "再生には FFmpeg の ffplay.exe が必要です。\n"
                "C:\\ffmpeg\\bin\\ffplay.exe が存在するか確認してください。"
            )
            return
        start, end = self.segments[idx]
        segment = self.audio[start:end]
        fd, temp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        segment.export(temp_path, format="wav")
        try:
            if self.play_process and self.play_process.poll() is None:
                self.play_process.terminate()
            self.play_process = subprocess.Popen(
                [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", temp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.play_temp = temp_path
            self.root.after(300, self._cleanup_play_temp)
            self.status_var.set(f"{start / 1000:.3f}～{end / 1000:.3f} 秒を再生しています。")
        except Exception as exc:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            messagebox.showerror("再生エラー", str(exc))

    def _cleanup_play_temp(self) -> None:
        if self.play_process is None or self.play_process.poll() is not None:
            if self.play_temp and os.path.exists(self.play_temp):
                try:
                    os.remove(self.play_temp)
                except OSError:
                    pass
            self.play_temp = None
            return
        self.root.after(500, self._cleanup_play_temp)

    def export_selected(self) -> None:
        idx = self.selected_index()
        if self.audio is None or idx is None:
            messagebox.showinfo("区間未選択", "保存する区間を選択してください。")
            return
        path = filedialog.asksaveasfilename(
            title="MP3 の保存先",
            defaultextension=".mp3",
            initialfile=f"{idx + 1:03d}.mp3",
            filetypes=[("MP3 ファイル", "*.mp3")]
        )
        if path:
            self._export_segment(self.segments[idx], Path(path))

    def export_all(self) -> None:
        if self.audio is None or not self.segments:
            messagebox.showinfo("区間なし", "保存する区間がありません。")
            return
        folder = filedialog.askdirectory(title="MP3 の保存先フォルダーを選択")
        if not folder:
            return
        out_dir = Path(folder)
        try:
            for i, segment in enumerate(self.segments, 1):
                self._export_segment(segment, out_dir / f"{i:03d}.mp3", show_error=False)
        except Exception as exc:
            messagebox.showerror("保存エラー", f"保存中にエラーが発生しました。\n{exc}")
            return
        messagebox.showinfo("保存完了", f"{len(self.segments)} 個の MP3 を保存しました。\n{out_dir}")
        self.status_var.set(f"{len(self.segments)} 個の MP3 を保存しました。")

    def _export_segment(self, times: list[int], path: Path, show_error: bool = True) -> None:
        if self.audio is None:
            return
        try:
            configure_ffmpeg()
            start, end = times
            self.audio[start:end].export(str(path), format="mp3", bitrate="192k")
            self.status_var.set(f"保存しました: {path}")
        except Exception as exc:
            if show_error:
                messagebox.showerror("保存エラー", f"MP3 を保存できませんでした。\n{exc}")
            else:
                raise

    def draw_waveform(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_line(0, height // 2, width, height // 2, fill="#4c5363")
        if self.audio is None or len(self.audio) == 0:
            canvas.create_text(width // 2, height // 2, text="音声を開くと波形が表示されます", fill="#d9deea")
            return

        mono = self.audio.set_channels(1)
        samples = mono.get_array_of_samples()
        total = len(samples)
        if total == 0:
            return
        samples_per_pixel = max(1, math.ceil(total / width))
        max_amp = float(1 << (8 * mono.sample_width - 1))
        center = height // 2
        amplitude_scale = max(1, height * 0.43)
        # Draw a compact peak envelope, not every individual sample.
        for x in range(width):
            a = x * samples_per_pixel
            if a >= total:
                break
            b = min(total, a + samples_per_pixel)
            peak = max(abs(int(v)) for v in samples[a:b])
            y = int(min(center - 1, (peak / max_amp) * amplitude_scale))
            if y > 0:
                canvas.create_line(x, center - y, x, center + y, fill="#7cc7ff")

        duration = len(self.audio)
        for i, (start, end) in enumerate(self.segments):
            x1 = int(start / duration * width)
            x2 = int(end / duration * width)
            canvas.create_rectangle(x1, 2, max(x1 + 1, x2), height - 2, outline="#ffca70", fill="", width=2)
            canvas.create_text((x1 + x2) // 2, 12, text=str(i + 1), fill="#ffca70")
        idx = self.selected_index()
        if idx is not None and idx < len(self.segments):
            start, end = self.segments[idx]
            x1 = int(start / duration * width)
            x2 = int(end / duration * width)
            canvas.create_rectangle(x1, 2, max(x1 + 1, x2), height - 2, outline="#ff6b6b", width=3)

    def on_wave_click(self, event) -> None:
        if self.audio is None or not self.segments:
            return
        width = max(self.canvas.winfo_width(), 1)
        target_ms = int(event.x / width * len(self.audio))
        for i, (start, end) in enumerate(self.segments):
            if start <= target_ms <= end:
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(i)
                self.listbox.activate(i)
                self.on_select_segment()
                return

    def _on_close(self) -> None:
        if self.play_process and self.play_process.poll() is None:
            self.play_process.terminate()
        if self.play_temp and os.path.exists(self.play_temp):
            try:
                os.remove(self.play_temp)
            except OSError:
                pass
        self.root.destroy()


def main() -> None:
    configure_ffmpeg()
    root = tk.Tk()
    VoiceSegmentCutter(root)
    root.mainloop()


if __name__ == "__main__":
    main()
