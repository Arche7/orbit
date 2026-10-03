"""Генератор картинок и видео для приветствия бота.

Делает:
  hub/assets/welcome.mp4      — короткое зацикленное видео (бот шлёт его как GIF);
  hub/assets/slide-1..4.jpg   — слайды для карусели «Как это работает».

Цены и названия берутся из hub/catalog.json, поэтому после смены цен
просто запусти скрипт ещё раз и закоммить hub/assets.

Нужно один раз на Mac:
    pip install playwright && python -m playwright install chromium
    brew install ffmpeg
Запуск из папки проекта:
    python tools/make_media.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "hub" / "assets"
W, H = 1280, 720
FPS = 30
LOOP_SECONDS = 8

FONT = "'Inter Display', 'SF Pro Display', -apple-system, 'Segoe UI', sans-serif"

# Общая сцена: орбита с ядром и тремя планетами-приложениями.
# render(t) рисует кадр; t от 0 до 1 — один полный цикл, поэтому видео зациклено без шва.
SCENE_JS = r"""
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function rand(seed) { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; }

function buildScene(svg, o) {
  const defs = el("defs", {}, svg);
  defs.innerHTML = `
    <radialGradient id="core" cx=".35" cy=".3" r=".85"><stop offset="0" stop-color="#FFF3DC"/><stop offset=".45" stop-color="#D8B27A"/><stop offset="1" stop-color="#6E5029"/></radialGradient>
    <filter id="glow" x="-1" y="-1" width="3" height="3"><feGaussianBlur stdDeviation="18"/></filter>
    <filter id="haze" x="-1" y="-1" width="3" height="3"><feGaussianBlur stdDeviation="60"/></filter>` +
    o.planets.map((p, i) => `<radialGradient id="p${i}" cx=".35" cy=".3" r=".85"><stop offset="0" stop-color="#FFFFFF"/><stop offset=".5" stop-color="${p.color}"/><stop offset="1" stop-color="${p.dark}"/></radialGradient>`).join("");
  const r = rand(7);
  const stars = el("g", {}, svg);
  const starList = [];
  for (let i = 0; i < 90; i++) {
    const s = el("circle", { cx: r() * o.w, cy: r() * o.h, r: 0.6 + r() * 1.4, fill: "#FFFFFF" }, stars);
    starList.push({ s, ph: r(), k: 1 + Math.floor(r() * 3) });
  }
  el("circle", { cx: o.cx, cy: o.cy, r: o.coreR * 1.9, fill: "#D8B27A", opacity: .18, filter: "url(#haze)" }, svg);
  const back = el("g", {}, svg);
  const ringPath = (half) => {
    const sweep = half === "back" ? 1 : 0;
    return `M ${-o.rx} 0 A ${o.rx} ${o.ry} 0 0 ${sweep} ${o.rx} 0`;
  };
  const tr = `translate(${o.cx} ${o.cy}) rotate(${o.tilt})`;
  el("path", { d: ringPath("back"), transform: tr, fill: "none", stroke: "#FFFFFF", "stroke-width": 3, opacity: .28 }, back);
  const backPlanets = el("g", {}, svg);
  el("circle", { cx: o.cx, cy: o.cy, r: o.coreR, fill: "url(#core)" }, svg);
  el("path", { d: ringPath("front"), transform: tr, fill: "none", stroke: "#0A0F1F", "stroke-width": 10, opacity: .5 }, svg);
  el("path", { d: ringPath("front"), transform: tr, fill: "none", stroke: "#FFFFFF", "stroke-width": 3, opacity: .7 }, svg);
  const frontPlanets = el("g", {}, svg);

  const nodes = o.planets.map((p, i) => {
    const g = el("g", {});
    const halo = el("circle", { r: p.r * 2.2, fill: p.color, opacity: .25, filter: "url(#glow)" }, g);
    const body = el("circle", { r: p.r, fill: `url(#p${i})` }, g);
    const label = el("text", { "text-anchor": "middle", "dominant-baseline": "central", fill: "#0A0F1F",
      "font-family": o.font, "font-weight": 700, "font-size": p.r * 0.95 }, g);
    label.textContent = p.mono;
    return { g, p };
  });
  const rad = Math.PI / 180, ct = Math.cos(o.tilt * rad), st = Math.sin(o.tilt * rad);

  return function render(t) {
    for (const x of starList) x.s.setAttribute("opacity", 0.15 + 0.45 * (0.5 + 0.5 * Math.sin(2 * Math.PI * (t * x.k + x.ph))));
    for (const { g, p } of nodes) {
      const th = 2 * Math.PI * (p.phase + t * p.turns);
      const ex = o.rx * Math.cos(th), ey = o.ry * Math.sin(th);
      const x = o.cx + ex * ct - ey * st, y = o.cy + ex * st + ey * ct;
      const depth = Math.sin(th);              // >0 — ближняя половина орбиты
      const sc = 1 + 0.16 * depth;
      g.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${sc.toFixed(3)})`);
      g.setAttribute("opacity", (0.75 + 0.25 * (depth + 1) / 2).toFixed(3));
      (depth >= 0 ? frontPlanets : backPlanets).appendChild(g);
    }
  };
}
"""

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1280px;height:720px;overflow:hidden;background:#070A14;color:#F2F4FA;font-family:FONT}
.bg{position:absolute;inset:0;background:radial-gradient(900px 520px at 70% 40%,#1A2445 0%,#0A0F1F 55%,#05070E 100%)}
.blob{position:absolute;border-radius:50%;filter:blur(90px)}
svg.scene{position:absolute;inset:0}
.brand{position:absolute;left:72px;top:56px;font-weight:700;letter-spacing:.18em;font-size:22px;opacity:.9}
.count{position:absolute;right:72px;top:58px;font-size:18px;color:#8E97B0;letter-spacing:.08em}
h1{font-weight:700;letter-spacing:-.03em;line-height:1.02}
.muted{color:#A3ACC4}
.gold{color:#E9CB98}
""".replace("FONT", FONT)


def scene_options(cx, cy, scale=1.0):
    return {
        "w": W, "h": H, "cx": cx, "cy": cy, "tilt": -14,
        "rx": 300 * scale, "ry": 84 * scale, "coreR": 104 * scale, "font": FONT,
        "planets": [
            {"mono": "M", "color": "#D8B27A", "dark": "#7A5A30", "r": 30 * scale, "phase": 0.08, "turns": 1},
            {"mono": "V", "color": "#43E08A", "dark": "#147A45", "r": 30 * scale, "phase": 0.41, "turns": 1},
            {"mono": "Δ", "color": "#9C84FF", "dark": "#4B33B5", "r": 30 * scale, "phase": 0.74, "turns": 1},
        ],
    }


def page_html(body: str, css: str = "", scene: dict | None = None, t: float = 0.0) -> str:
    script = ""
    if scene is not None:
        script = (
            "<script>" + SCENE_JS + "\nwindow.render = buildScene(document.querySelector('svg.scene'), "
            + json.dumps(scene) + f");\nrender({t});</script>"
        )
    svg = f'<svg class="scene" viewBox="0 0 {W} {H}" width="{W}" height="{H}"></svg>' if scene else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}{css}</style></head>'
            f'<body><div class="bg"></div>{svg}{body}{script}</body></html>')


def video_page(catalog: dict) -> str:
    names = " · ".join(a["name"] for a in catalog["apps"] if a["status"] == "live")
    css = """
    .word{position:absolute;left:0;right:0;top:520px;text-align:center;font-weight:700;font-size:64px;letter-spacing:.16em}
    .tag{position:absolute;left:0;right:0;top:604px;text-align:center;font-size:24px;color:#A3ACC4;letter-spacing:.02em}
    .tag b{color:#E9CB98;font-weight:600}
    """
    body = f'<div class="word">ORBIT</div><div class="tag">{names} — <b>одна подписка на всё</b></div>'
    return page_html(body, css, scene_options(W / 2, 290, 1.0))


def slide_pages(catalog: dict) -> list[str]:
    live = [a for a in catalog["apps"] if a["status"] == "live"]
    bundle = catalog["bundle"]
    separate = sum(a["price_stars"] for a in live)
    total = 4
    pages = []

    # 1 — кто мы
    css1 = """
    .txt{position:absolute;left:72px;top:170px;width:640px}
    h1{font-size:64px}
    .lead{margin-top:28px;font-size:25px;line-height:1.45}
    .chips{margin-top:34px;display:flex;gap:12px}
    .chip{padding:10px 18px;border-radius:999px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);font-size:18px}
    """
    chips = "".join(f'<span class="chip">{a["name"]}</span>' for a in live)
    pages.append(page_html(
        f'<div class="brand">ORBIT</div><div class="count">1 / {total}</div>'
        f'<div class="txt"><h1>Все приложения —<br><span class="gold">в одной орбите</span></h1>'
        f'<p class="lead muted">Один вход через Telegram, одна подписка, '
        f'никаких регистраций и паролей.</p><div class="chips">{chips}</div></div>',
        css1, scene_options(930, 380, 0.82), t=0.0))

    # 2 — приложения
    cards = ""
    for a in live:
        c = a["colors"]
        feats = "".join(f"<li>{f}</li>" for f in a["features"][:3])
        cards += (
            f'<div class="card" style="--a:{c["accent"]};--f:{c["from"]};--t:{c["to"]}">'
            f'<div class="mono">{a["monogram"]}</div><div class="name">{a["name"]}</div>'
            f'<div class="tl">{a["tagline"]}</div><ul>{feats}</ul>'
            f'<div class="price">{a["price_stars"]} ⭐ <span>в месяц</span></div></div>')
    css2 = """
    h1{position:absolute;left:72px;top:118px;font-size:52px}
    .row{position:absolute;left:72px;right:72px;top:220px;display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
    .card{position:relative;height:440px;border-radius:28px;padding:30px;background:linear-gradient(160deg,var(--f),var(--t));
      border:1px solid rgba(255,255,255,.10);overflow:hidden}
    .card:after{content:"";position:absolute;right:-60px;top:-60px;width:200px;height:200px;border-radius:50%;background:var(--a);opacity:.18;filter:blur(40px)}
    .mono{width:58px;height:58px;border-radius:50%;background:var(--a);color:#0A0F1F;display:grid;place-items:center;font-weight:700;font-size:28px}
    .name{margin-top:20px;font-weight:700;font-size:32px;letter-spacing:.04em}
    .tl{margin-top:8px;font-size:18px;color:#C9CFDF;line-height:1.35;min-height:50px}
    ul{margin-top:14px;list-style:none;font-size:16px;color:#A3ACC4;line-height:1.42}
    li+li{margin-top:4px}
    li:before{content:"— ";color:var(--a)}
    .price{position:absolute;left:30px;bottom:28px;font-weight:700;font-size:26px;color:var(--a)}
    .price span{font-weight:500;font-size:17px;color:#A3ACC4}
    """
    pages.append(page_html(
        f'<div class="brand">ORBIT</div><div class="count">2 / {total}</div>'
        f'<h1>Три приложения — <span class="gold">одна экосистема</span></h1><div class="row">{cards}</div>', css2))

    # 3 — всё включено
    css3 = """
    .wrap{position:absolute;left:72px;top:150px;width:640px}
    .eyebrow{font-size:20px;letter-spacing:.14em;text-transform:uppercase;color:#E9CB98}
    h1{margin-top:16px;font-size:64px}
    .price{margin-top:30px;display:flex;align-items:baseline;gap:18px}
    .big{font-weight:700;font-size:96px;letter-spacing:-.03em}
    .per{font-size:24px;color:#A3ACC4}
    .was{margin-top:6px;font-size:22px;color:#8E97B0}
    .was s{color:#8E97B0} .save{color:#43E08A;font-weight:600}
    .list{position:absolute;right:72px;top:180px;width:440px;display:grid;gap:16px}
    .item{padding:22px 24px;border-radius:22px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.10);font-size:21px;line-height:1.35}
    .item b{display:block;font-size:23px;margin-bottom:4px}
    .blob{left:-120px;top:260px;width:520px;height:420px;background:#D8B27A;opacity:.14}
    """
    names = ", ".join(a["name"] for a in live)
    pages.append(page_html(
        f'<div class="blob"></div><div class="brand">ORBIT</div><div class="count">3 / {total}</div>'
        f'<div class="wrap"><div class="eyebrow">Подписка</div><h1>{bundle["name"]}</h1>'
        f'<div class="price"><span class="big gold">{bundle["price_stars"]} ⭐</span><span class="per">в месяц</span></div>'
        f'<div class="was">Отдельно — <s>{separate} ⭐</s> · <span class="save">выгода {separate - bundle["price_stars"]} ⭐</span></div></div>'
        f'<div class="list"><div class="item"><b>Всё, что есть сейчас</b>{names}</div>'
        f'<div class="item"><b>Всё, что выйдет потом</b>Новые приложения — сразу и без доплат</div>'
        f'<div class="item"><b>Одна дата продления</b>Раз в 30 дней, отключить — в два касания</div></div>', css3))

    # 4 — как это работает
    css4 = """
    h1{position:absolute;left:72px;top:118px;font-size:56px}
    .steps{position:absolute;left:72px;right:72px;top:250px;display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
    .step{height:300px;border-radius:28px;padding:32px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10);position:relative}
    .n{width:56px;height:56px;border-radius:50%;border:2px solid #E9CB98;color:#E9CB98;display:grid;place-items:center;font-weight:700;font-size:24px}
    .step b{display:block;margin-top:28px;font-size:30px;letter-spacing:-.01em}
    .step p{margin-top:12px;font-size:20px;color:#A3ACC4;line-height:1.45}
    .foot{position:absolute;left:72px;bottom:56px;font-size:20px;color:#8E97B0}
    .foot b{color:#43E08A;font-weight:600}
    """
    steps = [
        ("Открой ORBIT", "Кнопка под этим сообщением или в меню слева от поля ввода"),
        ("Выбери", "Одно приложение или «" + bundle["name"] + "» — всё сразу"),
        ("Оплати в Stars", "Доступ откроется сразу во всех выбранных приложениях"),
    ]
    st = "".join(f'<div class="step"><div class="n">{i}</div><b>{a}</b><p>{b}</p></div>'
                 for i, (a, b) in enumerate(steps, 1))
    pages.append(page_html(
        f'<div class="brand">ORBIT</div><div class="count">4 / {total}</div>'
        f'<h1>Как это работает</h1><div class="steps">{st}</div>'
        f'<div class="foot"><b>Без риска:</b> отменить можно в любой момент — доступ останется до конца оплаченного месяца.</div>', css4))
    return pages


# Ждём два кадра отрисовки: иначе Chromium иногда снимает экран до того,
# как нарисовал SVG с размытием.
_TWO_FRAMES = "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


def _settle(page) -> None:
    page.evaluate(f"document.fonts.ready.then(() => {_TWO_FRAMES})")
    page.wait_for_timeout(120)


def main() -> None:
    catalog = json.loads((ROOT / "hub" / "catalog.json").read_text(encoding="utf-8"))
    ASSETS.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg") is None:
        raise SystemExit("Нужен ffmpeg: brew install ffmpeg")

    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        # Каждой картинке — своя вкладка: в переиспользованной вкладке Chromium
        # иногда не перерисовывает SVG со второго документа.
        for i, html in enumerate(slide_pages(catalog), 1):
            page = browser.new_page(viewport={"width": W, "height": H})
            page.set_content(html)
            _settle(page)
            page.screenshot(path=str(ASSETS / f"slide-{i}.jpg"), type="jpeg", quality=90)
            page.close()

        page = browser.new_page(viewport={"width": W, "height": H})
        page.set_content(video_page(catalog))
        _settle(page)
        frames = FPS * LOOP_SECONDS
        for f in range(frames):
            page.evaluate(f"render({f / frames}); {_TWO_FRAMES}")
            page.screenshot(path=f"{tmp}/f{f:04d}.png")
        browser.close()

        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{tmp}/f%04d.png",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-preset", "slow",
             "-movflags", "+faststart", "-an", str(ASSETS / "welcome.mp4")],
            check=True,
        )
    for f in sorted(ASSETS.iterdir()):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
