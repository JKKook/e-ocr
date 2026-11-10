// Electron 셸 — ① FastAPI 를 빈 포트·새 토큰으로 자식 프로세스로 띄우고 ② Next 정적 export(out/) 를 로컬 HTTP 로 서빙해 창에 싣는다.
// 포트·토큰은 URL hash(#api=…&t=…)로 화면에 넘긴다 — hash 는 서버 요청에 실리지 않는다.
// 개발: ELECTRON_DEV=1 이면 next dev(:3000) 를 싣고, API 는 AIOCR_API(기본 :8765, 토큰 없음)를 쓴다.
// 캡처: AIOCR_CAPTURE=<폴더> 면 창을 숨긴 채 화면별 PNG 를 찍고 끝낸다(AIOCR_CAPTURE_ASK=질문 · AIOCR_CAPTURE_FILE=업로드할 파일).
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");      // ~/projects/ai-ocr
const OUT = path.resolve(__dirname, "..", "out");
const CAPTURE = process.env.AIOCR_CAPTURE;
if (CAPTURE) app.setPath("userData", path.join(CAPTURE, ".userdata"));  // 캡처의 대화·설정이 사용자 앱 저장소에 섞이지 않게
let api = null;

const freePort = () => new Promise((ok) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); }); });
const up = (base, token) => new Promise((ok) => http.get(`${base}/health`, { headers: { "x-aiocr-token": token } }, (r) => { r.resume(); ok(r.statusCode === 200); }).on("error", () => ok(false)));

async function startApi() {
  if (process.env.ELECTRON_DEV) return { base: process.env.AIOCR_API || "http://127.0.0.1:8765", token: "" };
  const port = await freePort();
  const token = crypto.randomBytes(24).toString("hex");
  const uv = ["/opt/homebrew/bin/uv", path.join(process.env.HOME, ".local/bin/uv")].find(fs.existsSync) || "uv";
  api = spawn(uv, ["run", "uvicorn", "aiocr.api:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: ROOT, stdio: "inherit",
    env: { ...process.env, AIOCR_TOKEN: token, PATH: `/opt/homebrew/bin:/usr/local/bin:${process.env.PATH}` },
  });
  return { base: `http://127.0.0.1:${port}`, token };
}

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2", ".ico": "image/x-icon", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };

function serveOut() {
  return new Promise((ok) => {
    const srv = http.createServer((req, res) => {
      let f = path.join(OUT, decodeURIComponent(new URL(req.url, "http://x").pathname));
      if (!f.startsWith(OUT)) { res.writeHead(403).end(); return; }
      if (fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, "index.html");
      else if (!fs.existsSync(f) && fs.existsSync(f + ".html")) f += ".html";
      if (!fs.existsSync(f)) { res.writeHead(404).end(); return; }
      res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" });
      fs.createReadStream(f).pipe(res);
    });
    srv.listen(0, "127.0.0.1", () => ok(`http://127.0.0.1:${srv.address().port}`));
  });
}

async function capture(win, cfg) {
  fs.mkdirSync(CAPTURE, { recursive: true });
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const js = (code) => win.webContents.executeJavaScript(code);
  const shot = async (name) => { await wait(800); fs.writeFileSync(path.join(CAPTURE, `${name}.png`), (await win.webContents.capturePage()).toPNG()); };
  const until = async (cond, sec) => { for (let i = 0; i < sec; i++) { await wait(1000); if (await js(cond)) return true; } return false; };
  const click = (sel, text) => js(`[...document.querySelectorAll(${JSON.stringify(sel)})].find(b => b.textContent.trim() === ${JSON.stringify(text)})?.click()`);

  for (let i = 0; i < 180 && !(await up(cfg.base, cfg.token)); i++) await wait(1000);
  await until(`!!document.querySelector('.home')`, 60);
  await wait(1500);
  await shot("home");
  await js(`document.querySelector('.bar2 .ibtn[aria-label="검색할 문서 고르기"]')?.click()`);
  await wait(600);
  await shot("picker");
  await js(`document.querySelector('.modal .ibtn.x')?.click()`);
  const q = process.env.AIOCR_CAPTURE_ASK;  // 링크를 넣으면 '링크 붙여 넣고 바로 질문' 흐름까지 본다
  if (q) {
    await js(`(() => { const el = document.querySelector('.composer2 textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, ${JSON.stringify(q)});
      el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await wait(300);
    await js(`document.querySelector('.composer2 .send')?.click()`);
    await until(`!!document.querySelector('.msg.bot [role=status]')`, 20);
    await wait(3000);
    await shot("ingesting");
    await until(`!!document.querySelector('.fb') || (!!document.querySelector('.msg.bot .answer') && !document.querySelector('.caret') && !document.querySelector('.msg.bot [role=status]'))`, 400);
    await js(`(document.querySelector('.sources .src.cited') || document.querySelector('.sources .src'))?.click()`);
    await until(`!!document.querySelector('.srcpanel .pagetext, .srcpanel img')`, 20);
    await wait(1500);
    await shot("answer");
  }
  const rd = process.env.AIOCR_CAPTURE_READER;  // "문서 제목|질문" — 읽기 화면 흐름
  if (rd) {
    const [title, rq] = rd.split("|");
    await js(`[...document.querySelectorAll('.side .navitem')][0]?.click()`);
    await wait(1200);
    await js(`[...document.querySelectorAll('.doclist .doc')].find(r => r.textContent.includes(${JSON.stringify(title)}))?.querySelector('.btn')?.click()`);
    await until(`!!document.querySelector('.rpage .textLayer span')`, 40);
    await wait(2000);
    await shot("reader");
    await js(`(() => { const el = document.querySelector('.rchat textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, ${JSON.stringify(rq)});
      el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await wait(300);
    await js(`document.querySelector('.rchat .send')?.click()`);
    await until(`!!document.querySelector('.rchat .fb')`, 240);
    await js(`document.querySelector('.rchat .cite')?.click()`);
    await wait(2500);
    await shot("reader-answer");
    await js(`(() => { const sp = [...(document.querySelector('.rpage.hot .textLayer') || document.querySelector('.rpage .textLayer')).querySelectorAll('span:not(.markedContent)')].filter(x => x.textContent.trim().length > 3);
      if (sp.length < 10) return;
      const r = document.createRange(); r.setStartBefore(sp[6]); r.setEndAfter(sp[9]);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      document.querySelector('.rpages').dispatchEvent(new MouseEvent('mouseup', { bubbles: true })); })()`);
    await wait(500);
    await shot("reader-select");
    await wait(600);
    await js(`document.querySelector('.askbubble')?.click()`);
    await wait(600);
    await shot("reader-quote");
  }
  const rep = process.env.AIOCR_CAPTURE_REPORT;  // "문서 제목" — 읽기 화면 보고서 탭(저장본) → 키워드 쪽 이동
  if (rep) {
    await js(`[...document.querySelectorAll('.side .navitem')][0]?.click()`);
    await wait(1200);
    await js(`[...document.querySelectorAll('.doclist .doc')].find(r => r.textContent.includes(${JSON.stringify(rep)}))?.querySelectorAll('.btn')[0]?.click()`);
    await until(`!!document.querySelector('.rpage')`, 40);
    await js(`[...document.querySelectorAll('.rtool .seg button')].find(b => b.textContent === '보고서')?.click()`);
    await until(`!!document.querySelector('.report .barrow') || !!document.querySelector('.rp-empty')`, 30);
    if (await js(`!!document.querySelector('.rp-empty')`)) {
      await js(`document.querySelector('.rp-empty .btn.primary')?.click()`);
      await until(`!!document.querySelector('.report .rp-sec .gist') && !document.querySelector('.rp-toc a.wait')`, 300);
    }
    await wait(1500);
    await shot("report-top");
    await js(`document.querySelector('#rp-topics')?.scrollIntoView()`);
    await wait(800);
    await shot("report-topics");
    await js(`document.querySelector('#rp-map')?.scrollIntoView()`);
    await wait(800);
    await shot("report-map");
    // 벡터 지도 호버(연관 청크) · 휠 확대
    await js(`(() => { const c = [...document.querySelectorAll('#rp-map svg circle[style*="pointer"]')][20]; c?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); })()`);
    await until(`document.querySelectorAll('.nnitem').length > 0`, 15);
    await wait(800);
    await shot("map-hover");
    await js(`(() => { const sv = document.querySelector('#rp-map svg'); const b = sv.getBoundingClientRect();
      const c = [...sv.querySelectorAll('circle[style*="pointer"]')][20].getBoundingClientRect();  // 색 점이 모인 곳을 커서로
      for (let i = 0; i < 2; i++) sv.dispatchEvent(new WheelEvent('wheel', { deltaY: -300, clientX: c.left + c.width / 2, clientY: c.top + c.height / 2, bubbles: true, cancelable: true })); })()`);
    await wait(800);
    await js(`(() => { const c = [...document.querySelectorAll('#rp-map svg circle[style*="pointer"]')][20]; c?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); })()`);
    await wait(800);
    await shot("map-zoom");
    await js(`document.querySelector('#rp-structure')?.scrollIntoView()`);
    await wait(800);
    await shot("report-structure");
    await js(`document.querySelector('#rp-keywords .pg')?.click()`);
    await until(`!!document.querySelector('.textLayer span.hl')`, 20);
    await wait(1500);
    await shot("report-jump");
  }
  const vid = process.env.AIOCR_CAPTURE_VIDEO;  // "문서 제목|구간 라벨" — 영상 대본 정책 표시
  if (vid) {
    const [title, at] = vid.split("|");
    await js(`[...document.querySelectorAll('.side .navitem')][0]?.click()`);
    await wait(1200);
    await js(`[...document.querySelectorAll('.doclist .doc')].find(r => r.textContent.includes(${JSON.stringify(title)}))?.querySelectorAll('.btn')[0]?.click()`);
    await until(`!!document.querySelector('.tline')`, 40);
    await js(`[...document.querySelectorAll('.toc button')].find(b => b.textContent.startsWith(${JSON.stringify(at)}))?.click()`);
    await wait(2000);
    await shot("video-lyrics");
  }
  const cmp = process.env.AIOCR_CAPTURE_COMPARE;  // "제목1|제목2" — 문서함 체크 → 비교 보고서 생성
  if (cmp) {
    await js(`[...document.querySelectorAll('.side .navitem')][0]?.click()`);
    await wait(1200);
    for (const t of cmp.split("|")) await js(`[...document.querySelectorAll('.doclist .doc')].find(r => r.textContent.includes(${JSON.stringify(t)}))?.querySelector('input[type=checkbox]')?.click()`);
    await wait(300);
    await js(`[...document.querySelectorAll('.btn.primary')].find(b => b.textContent.includes('비교 보고서'))?.click()`);
    await until(`!!document.querySelector('.report') || !!document.querySelector('.rp-empty')`, 20);
    await js(`document.querySelector('.rp-empty .btn.primary')?.click()`);
    await until(`!!document.querySelector('#rp-summary table') && !document.querySelector('.rp-toc a.wait')`, 400);
    await wait(1500);
    await shot("compare-top");
    await js(`document.querySelector('#rp-contrast')?.scrollIntoView()`);
    await wait(800);
    await shot("compare-contrast");
    await js(`document.querySelector('#rp-map')?.scrollIntoView()`);
    await wait(800);
    await shot("compare-map");
  }
  const only = process.env.AIOCR_CAPTURE_ATTACH_ONLY;  // 링크만 보내면 뷰어가 열리고 요약이 시작되는지
  if (only) {
    await click(".newchat", "새 대화");
    await until(`!!document.querySelector('.home')`, 10);
    await js(`(() => { const el = document.querySelector('.composer2 textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(el, ${JSON.stringify(only)});
      el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await wait(300);
    await js(`document.querySelector('.composer2 .send')?.click()`);
    await until(`!!document.querySelector('.reader .rpage')`, 120);
    await until(`!!document.querySelector('.rchat .fb')`, 240);
    await wait(1500);
    await shot("attach-open");
  }
  await js(`[...document.querySelectorAll('.side .navitem')][0]?.click()`);
  await wait(1500);
  await shot("docs");
  await js(`(() => { const b = document.querySelector('.toggle input'); if (!b.checked) b.click(); })()`);
  for (const [name, label] of [["trace", "질의 trace"], ["gate", "품질 게이트"], ["perf", "성능 보고서"]]) { await click(".side button", label); await wait(1500); await shot(name); }
  app.quit();
}

app.setName("e-ocr");  // 메뉴·정보 창에 뜨는 앱 이름
const ICON = path.join(__dirname, "icon.png");  // 문서 + 스캔선 (앱 색 토큰)

app.whenReady().then(async () => {
  if (process.platform === "darwin") app.dock?.setIcon(ICON);
  const cfg = await startApi();
  const page = process.env.ELECTRON_DEV ? "http://localhost:3000" : await serveOut();
  const win = new BrowserWindow({
    width: 1320, height: 860, minWidth: 900, minHeight: 600, show: !CAPTURE, title: "e-ocr", icon: ICON,
    titleBarStyle: "hiddenInset", trafficLightPosition: { x: 14, y: 14 }, backgroundColor: "#F5F7F5",
    webPreferences: { contextIsolation: true, sandbox: true },
  });
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  await win.loadURL(`${page}/#${new URLSearchParams({ api: cfg.base, t: cfg.token })}`);
  if (CAPTURE) capture(win, cfg);
});

app.on("window-all-closed", () => app.quit());
app.on("quit", () => api?.kill());
