"""PASM Studio 桌面版（路线 A：Qt 原生 GUI，进程内直调引擎）。

功能：选性格出生 → 实时画布看它探索 → 右侧内心仪表盘 → 底部聊天（Narrator）
      → 暂停/加速/睡觉/存档/读档（用户目录 ~/.pasmstudio/ 存宠物，不丢）。

运行：pip install PySide6  然后  python desktop/pasm_desktop.py（需 Python 3.10+ / Windows 10+）
注意：需在有显示器的环境运行（本文件仅作工程脚手架，含打包说明见 desktop/README.md）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qt_compat import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                       QPushButton, QTextBrowser, QVBoxLayout, QWidget,
                       Qt, QTimer, QColor, QFont, QIcon, QPainter)

from pasm.agent import PASMAgent
from pasm.config import PASMConfig
from pasm.envs import GridWorld
from pasm.narrator import Narrator
from pasm.training import pretrain_vae, pretrain_world_model

from appinfo import APP_HOMEPAGE, APP_NAME, APP_VERSION
from updater import check_async

def _data_dir() -> str:
    r"""用户数据目录（qclaw 式惯例）：
    1) 环境变量 PASM_STUDIO_DIR 优先（可迁移/便携）
    2) 便携模式：exe 旁存在 data\ 目录时用 data\（随包带走）
    3) 标准模式：%APPDATA%\PASMStudio（Windows 惯例，卸载不误删用户宠物）
    4) 开发模式：仓库内 .pasmstudio_dev（不污染用户目录）
    """
    env = os.environ.get("PASM_STUDIO_DIR")
    if env:
        return env
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        portable = os.path.join(exe_dir, "data")
        if os.path.isdir(portable):
            return portable
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PASMStudio")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".pasmstudio_dev")


DATA_DIR = _data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
SAVE_PATH = os.path.join(DATA_DIR, "pet.pt")

PERSONALITIES = {
    "冒险型": [0.8, -0.4, 0.2],
    "平衡型": [0.0, 0.0, 0.0],
    "谨慎型": [-0.4, 0.8, 0.1],
}
COLORS = {"冒险型": "#dc2626", "平衡型": "#2563eb", "谨慎型": "#0f9d58"}
CELL = 40


class WorldCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.env = None
        self.color = "#2563eb"
        self.setFixedSize(400, 400)

    def set_env(self, env, color):
        self.env, self.color = env, color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#fbfcfe"))
        if self.env is None:
            p.drawText(self.rect(), Qt.AlignCenter, "还没出生…点「出生」领养一只")
            return
        n = self.env.size
        step = 400 // n
        for i in range(n + 1):
            p.setPen(QColor("#e2e8f0"))
            p.drawLine(i * step, 0, i * step, 400)
            p.drawLine(0, i * step, 400, i * step)
        for (x, y) in self.env.obstacles:
            p.fillRect(x * step + 2, y * step + 2, step - 4, step - 4, QColor("#9aa5b1"))
        for (x, y) in self.env.energy_positions:
            p.setBrush(QColor("#f5b300"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(x * step + step / 2 - 9, y * step + step / 2 - 9, 18, 18)
        ax, ay = self.env.agent_pos
        p.setBrush(QColor(self.color))
        p.setPen(QColor("#1e3a8a"))
        p.drawEllipse(ax * step + step / 2 - 11, ay * step + step / 2 - 11, 22, 22)


def bar_pct(v, lo=-1, hi=1):
    return int(max(0.0, min(1.0, (v - lo) / (hi - lo))) * 100)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} · 养一只会成长的 AI")
        self.setWindowIcon(self._load_icon())
        self.resize(1020, 560)
        self.agent = None
        self.env = None
        self.persona = "平衡型"
        self.running = False
        self.speed = 1
        self.narrator = Narrator()
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        # 启动后台升级检查（不阻塞 UI）
        check_async(self._on_update_result)

    # ---------- UI ----------
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        left = QVBoxLayout()
        row0 = QHBoxLayout()
        row0.addWidget(QLabel("先天性格："))
        for tag, seed in PERSONALITIES.items():
            b = QPushButton(tag)
            b.clicked.connect(lambda _=False, t=tag: self.pick(t))
            b.setStyleSheet("font-size:12px")
            row0.addWidget(b)
        row0.addStretch(1)
        left.addLayout(row0)
        self.canvas = WorldCanvas()
        left.addWidget(self.canvas)
        btns = QHBoxLayout()
        for name, cb in [("出生", self.spawn), ("⏸/▶", self.toggle), ("⚡x3", self.faster),
                         ("😴", self.sleep), ("💾", self.save), ("📂", self.load)]:
            b = QPushButton(name)
            b.clicked.connect(cb)
            btns.addWidget(b)
        left.addLayout(btns)
        self.info = QLabel("尚未出生")
        self.info.setStyleSheet("color:#334155;font-size:13px")
        left.addWidget(self.info)
        root.addLayout(left, 3)
        right = QVBoxLayout()
        self.dash = QLabel()
        self.dash.setStyleSheet("font-size:13px;color:#22303f;line-height:1.6")
        right.addWidget(self.dash)
        self.chat = QTextBrowser()
        self.chat.setMaximumHeight(200)
        right.addWidget(self.chat)
        root.addLayout(right, 2)

    def pick(self, tag):
        self.persona = tag

    @staticmethod
    def _load_icon() -> QIcon:
        if getattr(sys, "frozen", False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(base, "assets", "icon.ico"),
                  os.path.join(base, "icon.ico")):
            if os.path.exists(p):
                return QIcon(p)
        return QIcon()

    def _on_update_result(self, latest):
        """后台升级检查回调（线程里调用，UI 操作用 QTimer.singleShot 回主线程）。"""
        if not latest:
            return
        msg = (f"发现新版本 v{latest.get('version')}\n"
               f"{latest.get('notes', '')}\n\n"
               f"是否打开下载页？")
        def _ask():
            btn = QPushButton("打开下载页")
            self.chat.append(
                f"🆕 新版 v{latest.get('version')} 可用（{latest.get('notes', '').strip()}）。\n"
                f"   下载：{latest.get('download_url', APP_HOMEPAGE)}")
        QTimer.singleShot(0, _ask)

    # ---------- 引擎驱动 ----------
    def spawn(self):
        cfg = PASMConfig(seed=5, plan_samples=16, plan_iters=1, consolidate_every=6)
        self.agent = PASMAgent(cfg, personality_seed=PERSONALITIES[self.persona])
        self.env = GridWorld(seed=5)
        self.chat.append(f"🌱 你领养了一只【{self.persona}】AI，它从婴儿期开始探索…")
        QTimer.singleShot(0, self._init_engine)

    def _init_engine(self):
        self.agent.reset_episode()
        self.info.setText("初始化（预训练感知器）…")
        QApplication.processEvents()
        try:
            pretrain_vae(self.env, self.agent, num_steps=200, epochs=6, verbose=False)
            pretrain_world_model(self.env, self.agent, num_steps=400, epochs=8,
                                 batch_size=128, verbose=False)
        except Exception as e:  # noqa: BLE001
            self.chat.append(f"⚠ 初始化失败：{e}")
        self.info.setText("出生完成！点 ⏸/▶ 开始（当前暂停）")
        self.render()

    def toggle(self):
        self.running = not self.running
        if self.running:
            self.timer.start(200 // self.speed)
        else:
            self.timer.stop()

    def faster(self):
        self.speed = min(3, self.speed + 1)
        if self.running:
            self.timer.setInterval(200 // self.speed)

    def sleep(self):
        if not self.agent:
            return
        self.agent.consolidate()
        self.chat.append("😴 睡觉巩固完成，记忆更牢了。")

    def save(self):
        if self.agent:
            self.agent.save(SAVE_PATH)
            self.chat.append(f"💾 已存档到 {SAVE_PATH}（宠物不丢）")

    def load(self):
        if os.path.exists(SAVE_PATH):
            cfg = PASMConfig(seed=5, plan_samples=16, plan_iters=1)
            self.agent = PASMAgent(cfg)
            self.agent.load(SAVE_PATH)
            self.env = GridWorld(seed=5)
            self.chat.append("📂 读档成功——你的 AI 还记得上次的事。")
            self.render()

    def tick(self):
        if not (self.agent and self.env):
            return
        for _ in range(self.speed):
            if not self.env.steps:
                self.env.reset()
            obs = self.env._get_obs()
            a, rep = self.agent.act(obs)
            nxt, r, done, info = self.env.step(a)
            self.agent.learn(obs, a, nxt, r, rep)
            if done:
                self.env.reset()
                self.agent.reset_episode()
        self.render()

    def render(self):
        if self.agent is None:
            return
        if self.env is not None:
            self.canvas.set_env(self.env, COLORS[self.persona])
        e = self.agent.snapshot()["emotion"]
        m = self.agent.snapshot()["memory"]
        d = self.agent.snapshot()["development"]
        self.info.setText(
            f"步 {self.env.steps} · 能量 ✦{self.env.total_energy_collected} · 碰撞 {self.env.collision_count}")
        self.dash.setText(
            f"😊 愉悦 {e['valence']:+.2f}　⚡ 唤醒 {e['arousal']:.2f}\n"
            f"🎯 掌控 {e['dominance']:.2f}　💉 多巴胺 {e['dopamine']:+.2f}\n"
            f"🧘 血清素 {e['serotonin']:+.3f}\n"
            f"🧠 情景记忆 {m['episodic']} · 习惯 {m['habits']}\n"
            f"🌱 发育阶段：{d['stage']}（可塑性 {d['plasticity']:.2f}）\n\n"
            f"[bar] 心情：{'好' if e['valence'] > 0.15 else '平' if e['valence'] > -0.15 else '低落'}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
