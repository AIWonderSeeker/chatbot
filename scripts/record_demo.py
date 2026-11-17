"""Record a ~2-3 minute demo video of the live chatbot for presentations.

Drives a headless browser with Playwright: title and architecture cards, then real
questions typed into the running app (real model answers, tool trail visible), with
on-screen captions, then a guardrails card. Exports an H.264 MP4.

    .venv/bin/python scripts/record_demo.py --url http://<demo-host>:8000 --out data/demo/chatbot-demo.mp4

Needs: pip install playwright imageio-ffmpeg && playwright install chromium (dev-only; not in
pyproject). The app URL is passed in so no host or IP is stored in the repo. Uses synthetic data
only; never point this at an environment with real PHI.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

W, H = 1280, 720

QUESTIONS: list[tuple[str, str]] = [
    (
        "Who is provider 1234567893, and what is their specialty?",
        "Claude picks the provider-lookup tool. The trail under the answer shows exactly which lookup ran.",
    ),
    (
        "Compare how many claims provider 1234567893 had in 2024 versus 2025.",
        "It calls the claim-summary tool once per year, then compares the two results.",
    ),
    (
        "What share of claims were denied in the first quarter of 2025?",
        "SQL does the counting on the database; Claude turns the totals into a rate.",
    ),
    (
        "How many patients did provider 1003000035 see in 2024 and 2025?",
        "Privacy rule: patient counts under 11 are suppressed, and Claude says so instead of guessing.",
    ),
    (
        "What's the weather in Boston?",
        "Out of scope: no tool fits, so it declines rather than inventing an answer.",
    ),
]

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1280px;height:720px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
 background:#0f1720;color:#e8eef5;display:flex;flex-direction:column;justify-content:center;padding:72px 96px}
.kicker{font-size:18px;letter-spacing:.14em;text-transform:uppercase;color:#7cc4b0;margin-bottom:18px}
h1{font-size:54px;line-height:1.1;font-weight:600;margin-bottom:22px}
h2{font-size:38px;font-weight:600;margin-bottom:28px}
p.lead{font-size:24px;line-height:1.5;color:#b7c3cf;max-width:980px}
.note{margin-top:34px;font-size:17px;color:#8a98a6}
.flow{display:flex;align-items:center;gap:14px;margin:8px 0 34px}
.box{border:1.5px solid #3a4b5c;border-radius:12px;padding:16px 18px;min-width:150px;text-align:center;background:#16212c}
.box b{display:block;font-size:20px;margin-bottom:6px}.box span{font-size:15px;color:#9fb0c0}
.box.code{border-color:#7f77dd;background:#1d1b3a}.box.aws{border-color:#1d9e75;background:#0f2a23}
.arrow{font-size:28px;color:#6b7c8c}
.below{display:flex;gap:14px;margin-left:330px}
ul{list-style:none;display:grid;grid-template-columns:1fr 1fr;gap:16px 40px}
li{font-size:21px;line-height:1.45;color:#cdd7e1;padding-left:30px;position:relative}
li:before{content:'';position:absolute;left:0;top:10px;width:12px;height:12px;border-radius:3px;background:#1d9e75}
ol{counter-reset:s;list-style:none}
ol li{counter-increment:s;padding-left:46px;margin-bottom:14px}
ol li:before{content:counter(s);width:30px;height:30px;top:0;border-radius:50%;background:#7f77dd;color:#fff;
 font-size:16px;display:flex;align-items:center;justify-content:center}
"""


def card(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


TITLE = card(
    "<div class='kicker'>Demo</div>"
    "<h1>Ask healthcare claims data<br>in plain English</h1>"
    "<p class='lead'>A chat assistant that answers questions about providers and claims using "
    "Claude on Amazon Bedrock, a controlled set of data tools, and a PostgreSQL database on AWS.</p>"
    "<p class='note'>All data in this demo is synthetic. No real patients or providers.</p>"
)

ARCH = card(
    "<div class='kicker'>How it fits together</div><h2>Architecture</h2>"
    "<div class='flow'>"
    "<div class='box'><b>Browser</b><span>Chat page</span></div><div class='arrow'>&rarr;</div>"
    "<div class='box code'><b>FastAPI</b><span>Agent loop</span></div><div class='arrow'>&rarr;</div>"
    "<div class='box code'><b>MCP server</b><span>3 safe tools</span></div><div class='arrow'>&rarr;</div>"
    "<div class='box aws'><b>RDS PostgreSQL</b><span>Providers, claims</span></div>"
    "</div>"
    "<div class='below'><div class='box aws'><b>Claude Sonnet 5</b><span>Amazon Bedrock</span></div>"
    "<div class='box aws'><b>Secrets Manager</b><span>DB password</span></div></div>"
    "<p class='note'>The API never touches the database. Only the MCP server does, and only "
    "through pre-written, read-only SQL.</p>"
)

FLOW = card(
    "<div class='kicker'>One question, step by step</div><h2>What happens when you ask</h2><ol>"
    "<li>The chat page sends your question to the API.</li>"
    "<li>Claude reads it and picks a tool, e.g. <i>claim summary for NPI X, 2025</i>.</li>"
    "<li>The API checks the tool is allowed and forwards the call to the MCP server.</li>"
    "<li>The MCP server validates inputs and runs one fixed, parameterized SQL query.</li>"
    "<li>Results are minimized (small counts hidden) and returned to Claude.</li>"
    "<li>Claude writes the answer; the page shows which lookups were used.</li></ol>"
)

GUARDRAILS = card(
    "<div class='kicker'>Built for sensitive data</div><h2>Guardrails</h2><ul>"
    "<li>The model never writes SQL: only reviewed templates run</li>"
    "<li>Allowlisted tools; every input validated</li>"
    "<li>Read-only, TLS-verified database sessions with time limits</li>"
    "<li>Patient counts under 11 are suppressed</li>"
    "<li>No questions, answers, or data in logs</li>"
    "<li>Secret scanning on every commit, plus CI on GitHub</li></ul>"
)

CLOSING = card(
    "<div class='kicker'>Where it stands</div><h2>Next steps</h2><ul>"
    "<li>Real schema mapping and a dedicated read-only DB login</li>"
    "<li>More question types (each is a new reviewed tool)</li>"
    "<li>Production hosting: ECS Fargate, HTTPS, company sign-in</li>"
    "<li>Terraform for that design is already written</li></ul>"
    "<p class='note'>Demo resources are stopped between sessions to keep cost near zero.</p>"
)

CAPTION_JS = """
(text) => {
  let el = document.getElementById('demo-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'demo-caption';
    el.style.cssText = 'position:fixed;left:50%;bottom:118px;transform:translateX(-50%);z-index:99999;' +
      'max-width:1080px;padding:12px 22px;border-radius:12px;background:rgba(15,23,32,.92);color:#fff;' +
      'font:500 19px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.25);' +
      'text-align:center;transition:opacity .25s';
    document.body.appendChild(el);
  }
  el.style.opacity = text ? '1' : '0';
  if (text) el.textContent = text;
}
"""


def record(url: str, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=tmp,
            record_video_size={"width": W, "height": H},
            color_scheme="light",
        )
        page = ctx.new_page()

        for html, seconds in ((TITLE, 5), (ARCH, 10), (FLOW, 12)):
            page.set_content(html)
            page.wait_for_timeout(seconds * 1000)

        page.goto(url, wait_until="networkidle")
        page.evaluate(CAPTION_JS, "Live app running on AWS. Real Claude answers, real database queries.")
        page.wait_for_timeout(4000)

        done = "#log .msg-assistant:not(.pending), #log .msg-error"
        for question, caption in QUESTIONS:
            page.click("#new-chat")
            page.evaluate(CAPTION_JS, caption)
            page.wait_for_timeout(1500)
            page.click("#input")
            page.keyboard.type(question, delay=35)
            page.wait_for_timeout(500)
            before = page.locator(done).count()
            page.keyboard.press("Enter")
            # Poll from Python: the app's CSP (script-src 'self') blocks string-eval waits.
            for _ in range(240):
                if page.locator(done).count() > before:
                    break
                page.wait_for_timeout(500)
            else:
                raise SystemExit(f"no answer within 120 s for: {question}")
            page.wait_for_timeout(7000)

        page.evaluate(CAPTION_JS, "")
        # Leave the app's origin: its CSP (style-src 'self') would block the cards' inline styles.
        page.goto("about:blank")
        for html, seconds in ((GUARDRAILS, 10), (CLOSING, 7)):
            page.set_content(html)
            page.wait_for_timeout(seconds * 1000)

        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path()) if video else None
        if raw is None or not raw.exists():
            raise SystemExit("no video was recorded")
        _to_mp4(raw, out)


def _to_mp4(src: Path, out: Path) -> None:
    import imageio_ffmpeg

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
        "-r", "30", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    if subprocess.run(cmd, check=False).returncode != 0:  # noqa: S603
        fallback = out.with_suffix(".webm")
        shutil.copy(src, fallback)
        raise SystemExit(f"MP4 encoding failed; raw recording saved to {fallback}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", required=True, help="base URL of the running chatbot, e.g. http://host:8000")
    parser.add_argument("--out", type=Path, default=Path("data/demo/chatbot-demo.mp4"))
    args = parser.parse_args()
    if not args.url.startswith(("http://", "https://")):
        parser.error("--url must start with http:// or https://")
    record(args.url.rstrip("/"), args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
