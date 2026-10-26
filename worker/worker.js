// 텔레그램 버튼 메뉴에 즉시 답하는 Cloudflare Worker.
// 메뉴 진행 상황은 KV에 저장하고, 고른 내용은 '대기열'에 넣은 뒤 GitHub 실행을 바로 깨운다.
// 실제 검색·추적은 GitHub(tracker.py)가 대기열을 가져가서 처리한다.

const REPO = "MoonSeongSam/price_tracker";
const PRICES = [[50000, "5만"], [100000, "10만"], [150000, "15만"], [200000, "20만"], [300000, "30만"], [500000, "50만"]];
const CATS = ["아우터", "상의", "하의", "신발", "잡화"];

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/queue") return queueEndpoint(req, env);
    if (url.pathname === "/tg" && req.method === "POST") {
      if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TG_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const u = await req.json();
      try {
        await handle(u, env);
      } catch (e) {
        const chat = (u.message || (u.callback_query || {}).message || {}).chat;
        if (chat) await tg(env, "sendMessage", { chat_id: chat.id, text: "⚠️ 메뉴 처리 오류: " + e.message });
      }
      return new Response("ok");
    }
    return new Response("ok");
  },
};

// ---------- GitHub에서 대기열 가져가기 ----------
async function queueEndpoint(req, env) {
  if (req.headers.get("X-Key") !== env.QUEUE_KEY) return new Response("forbidden", { status: 403 });
  const q = JSON.parse((await env.KV.get("queue")) || "[]");
  await env.KV.put("queue", "[]");
  return new Response(JSON.stringify(q), { headers: { "Content-Type": "application/json" } });
}

async function enqueue(env, cmd) {
  const q = JSON.parse((await env.KV.get("queue")) || "[]");
  q.push(cmd);
  await env.KV.put("queue", JSON.stringify(q));
  // GitHub 실행을 바로 깨움 (실패해도 매시간 실행 때 처리됨)
  await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/track.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + env.GH_TOKEN,
      Accept: "application/vnd.github+json",
      "User-Agent": "price-bot",
    },
    body: JSON.stringify({ ref: "main" }),
  });
}

async function repoJson(env, path, fallback) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/contents/${path}`, {
    headers: { Authorization: "Bearer " + env.GH_TOKEN, Accept: "application/vnd.github.raw+json", "User-Agent": "price-bot" },
  });
  if (!r.ok) return fallback;
  try { return JSON.parse(await r.text()); } catch { return fallback; }
}

// ---------- 텔레그램 ----------
async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

const kb = (rows) => ({ inline_keyboard: rows.map((r) => r.map(([text, data]) => ({ text, callback_data: data }))) });
const MENU_KEY = { keyboard: [[{ text: "🛍 메뉴" }]], resize_keyboard: true, is_persistent: true };

async function show(env, chat, msgId, text, rows) {
  const body = { chat_id: chat, text, parse_mode: "HTML", reply_markup: kb(rows) };
  if (msgId) {
    const r = await tg(env, "editMessageText", { ...body, message_id: msgId });
    if (r.ok) return;
  }
  await tg(env, "sendMessage", body);
}

const getConv = async (env) => JSON.parse((await env.KV.get("conv")) || "null");
const setConv = (env, c) => env.KV.put("conv", JSON.stringify(c));
const won = (n) => (n ? n.toLocaleString("ko-KR") + "원" : "상관없음");
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function parsePrice(t) {
  let m = t.match(/(\d+(?:\.\d+)?)\s*만/);
  if (m) return Math.round(parseFloat(m[1]) * 10000);
  m = t.replace(/[,원\s]/g, "").match(/^\d{4,}$/);
  return m ? parseInt(m[0], 10) : null;
}

// ---------- 메시지 처리 ----------
async function handle(u, env) {
  if (u.callback_query) {
    const cb = u.callback_query;
    if (String(cb.message.chat.id) !== String(env.CHAT_ID)) return;
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
    return onButton(env, cb.message.chat.id, cb.message.message_id, cb.data);
  }
  const msg = u.message;
  if (!msg || String(msg.chat.id) !== String(env.CHAT_ID)) return;
  const text = (msg.text || "").trim();
  const conv = await getConv(env);

  if (text.startsWith("검색 ") && text.length > 3) {
    await enqueue(env, { action: "search", q: text.slice(3).trim() });
    return tg(env, "sendMessage", { chat_id: msg.chat.id, text: "🔎 검색 중… 1~2분 안에 결과가 옵니다." });
  }
  if (conv && conv.wait) return onText(env, msg.chat.id, conv, text);
  if (text === "/start") {
    await tg(env, "sendMessage", { chat_id: msg.chat.id, text: "아래 🛍 메뉴 버튼으로 시작하세요.", reply_markup: MENU_KEY });
  }
  return mainMenu(env, msg.chat.id, null);
}

async function mainMenu(env, chat, msgId) {
  await setConv(env, null);
  return show(env, chat, msgId, "무엇을 할까요?", [
    [["👕 옷", "c"], ["📦 기타 물건", "e"]],
    [["📋 진행 중 목록 · 중단", "l"]],
  ]);
}

async function onButton(env, chat, msgId, data) {
  let conv = (await getConv(env)) || { d: {} };
  const d = conv.d || {};

  if (data === "m") return mainMenu(env, chat, msgId);
  // 예전 메시지의 버튼을 눌러 진행 상황이 없으면 처음으로
  if (/^(b:|k:|dc:|bx:|badd|h2)/.test(data) && conv.flow !== "h") return mainMenu(env, chat, msgId);
  if (/^(p:|n:|x:|go)/.test(data) && !conv.flow) return mainMenu(env, chat, msgId);
  if (data === "c") {
    return show(env, chat, msgId, "👕 옷", [
      [["🎯 트래킹 시작 (상품 하나)", "t"]],
      [["🛍 쇼핑 시작 (좋은 것 추천)", "h"]],
      [["⏹ 중단", "l"], ["← 처음", "m"]],
    ]);
  }
  if (data === "t" || data === "e") {
    conv = { flow: data, wait: "name", d: {} };
    await setConv(env, conv);
    const ex = data === "t" ? "예: 쿠어 패널드 울 블레이저" : "예: 닌텐도 스위치 2, 에어팟 프로 2";
    return show(env, chat, msgId, (data === "t" ? "🎯 사고 싶은 옷" : "📦 찾을 물건") + "을 입력해 주세요.\n" + ex, [[["취소", "m"]]]);
  }
  if (data === "h") {
    const menu = await repoJson(env, "state/menu_data.json", { brands: [] });
    conv = { flow: "h", d: { brands: [], cats: ["아우터", "상의", "하의", "신발"], all: menu.brands } };
    await setConv(env, conv);
    return brandMenu(env, chat, msgId, conv);
  }
  if (data.startsWith("b:")) {
    const v = data.slice(2);
    const all = d.all || [];
    if (v === "next") {
      if (!d.brands.length) return brandMenu(env, chat, msgId, conv, "브랜드를 하나 이상 골라 주세요.");
      return catMenu(env, chat, msgId, conv);
    }
    if (v.startsWith("t")) {
      const tier = parseInt(v.slice(1), 10);
      for (const b of all) if (b.tier === tier && !d.brands.includes(b.name)) d.brands.push(b.name);
    } else {
      const name = all[parseInt(v, 10)].name;
      d.brands = d.brands.includes(name) ? d.brands.filter((x) => x !== name) : [...d.brands, name];
    }
    await setConv(env, conv);
    return brandMenu(env, chat, msgId, conv);
  }
  if (data === "badd") {
    conv.wait = "brand";
    await setConv(env, conv);
    return show(env, chat, msgId, "추가할 브랜드 이름을 입력해 주세요.\n예: 아워레가시", [[["← 브랜드 고르기", "h2"]]]);
  }
  if (data === "h2") return brandMenu(env, chat, msgId, conv);
  if (data.startsWith("bx:")) {  // 방금 추가한 브랜드를 이번 쇼핑에도 넣기
    const name = data.slice(3);
    d.all = [...(d.all || []), { name, tier: 2 }];
    if (!d.brands.includes(name)) d.brands.push(name);
    await setConv(env, conv);
    return brandMenu(env, chat, msgId, conv);
  }
  if (data.startsWith("k:")) {
    const v = data.slice(2);
    if (v === "next") {
      if (!d.cats.length) return catMenu(env, chat, msgId, conv, "품목을 하나 이상 골라 주세요.");
      return show(env, chat, msgId, "세일은 몇 % 이상만 볼까요?\n(후기 좋은 상품은 할인율과 상관없이 같이 보여 드립니다)", [
        [["20%", "dc:20"], ["30%", "dc:30"], ["50%", "dc:50"]],
        [["세일 상관없음", "dc:0"]],
      ]);
    }
    const c = CATS[parseInt(v, 10)];
    d.cats = d.cats.includes(c) ? d.cats.filter((x) => x !== c) : [...d.cats, c];
    await setConv(env, conv);
    return catMenu(env, chat, msgId, conv);
  }
  if (data.startsWith("dc:")) {
    d.min_discount = parseInt(data.slice(3), 10);
    await setConv(env, conv);
    return priceMenu(env, chat, msgId, "한 개 가격 상한선을 골라 주세요.");
  }
  if (data.startsWith("p:")) {
    const v = data.slice(2);
    if (v === "x") {
      conv.wait = "price";
      await setConv(env, conv);
      return show(env, chat, msgId, "상한선을 입력해 주세요. 예: 12만", [[["취소", "m"]]]);
    }
    d.max_price = parseInt(v, 10) || null;
    await setConv(env, conv);
    return afterPrice(env, chat, msgId, conv);
  }
  if (data.startsWith("x:")) {  // 기타: 제외할 말 / 꼭 들어갈 말 '없음'
    if (data === "x:ex") { d.exclude = []; return askInclude(env, chat, msgId, conv); }
    if (data === "x:in") { d.include = []; return condMenu(env, chat, msgId, conv); }
  }
  if (data.startsWith("n:")) {
    d.cond = data.slice(2);
    await setConv(env, conv);
    return confirm(env, chat, msgId, conv);
  }
  if (data === "go") return start(env, chat, msgId, conv);
  if (data === "l") return listMenu(env, chat, msgId);
  if (data.startsWith("sf:")) {  // 기본 추적(스위치 등) 중단
    const menu = await repoJson(env, "state/menu_data.json", { fixed: [] });
    const name = (menu.fixed || [])[parseInt(data.slice(3), 10)];
    if (name) await enqueue(env, { action: "stop_fixed", name });
    return show(env, chat, msgId, `⏹ ${esc(name || "")} 중단 요청했습니다.`, [[["📋 목록", "l"], ["← 처음", "m"]]]);
  }
  if (data.startsWith("s:")) {
    const id = parseInt(data.slice(2), 10);
    await enqueue(env, { action: "stop", id });
    return show(env, chat, msgId, `⏹ #${id} 중단 요청했습니다.`, [[["📋 목록", "l"], ["← 처음", "m"]]]);
  }
  return mainMenu(env, chat, msgId);
}

async function onText(env, chat, conv, text) {
  const d = conv.d;
  if (conv.wait === "name") {
    d.query = text;
    conv.wait = null;
    await setConv(env, conv);
    return priceMenu(env, chat, null, `「${esc(text)}」 가격 상한선을 골라 주세요.`);
  }
  if (conv.wait === "price") {
    const p = parsePrice(text);
    if (!p) return show(env, chat, null, "숫자로 입력해 주세요. 예: 12만, 120000", [[["취소", "m"]]]);
    d.max_price = p;
    conv.wait = null;
    await setConv(env, conv);
    return afterPrice(env, chat, null, conv);
  }
  if (conv.wait === "exclude") {
    d.exclude = text.split(/[,，\s]+/).filter(Boolean);
    return askInclude(env, chat, null, conv);
  }
  if (conv.wait === "include") {
    d.include = text.split(/[,，\s]+/).filter(Boolean);
    conv.wait = null;
    await setConv(env, conv);
    return condMenu(env, chat, null, conv);
  }
  if (conv.wait === "brand") {
    await enqueue(env, { action: "add_brand", name: text });
    conv.wait = null;
    await setConv(env, conv);
    return show(env, chat, null, `「${esc(text)}」 브랜드 등록을 요청했습니다 (1~2분). 이번 쇼핑에도 넣을까요?`, [
      [["넣기", "bx:" + text.slice(0, 15)], ["← 브랜드 고르기", "h2"]],  // 버튼 데이터는 64바이트 제한
    ]);
  }
  return mainMenu(env, chat, null);
}

async function brandMenu(env, chat, msgId, conv, warn) {
  const d = conv.d;
  const all = d.all || [];
  const rows = [];
  for (let i = 0; i < all.length; i += 3) {
    rows.push(all.slice(i, i + 3).map((b, j) => [(d.brands.includes(b.name) ? "✅ " : "") + b.name, "b:" + (i + j)]));
  }
  rows.push([["1순위 전부", "b:t1"], ["2순위 전부", "b:t2"], ["3순위 전부", "b:t3"]]);
  rows.push([["+ 브랜드 추가", "badd"], ["다음 →", "b:next"]]);
  rows.push([["취소", "m"]]);
  const sel = d.brands.length ? "\n고른 브랜드: " + d.brands.join(", ") : "";
  return show(env, chat, msgId, (warn ? "⚠️ " + warn + "\n" : "") + "🛍 어떤 브랜드를 볼까요? (여러 개 선택)" + sel, rows);
}

async function catMenu(env, chat, msgId, conv, warn) {
  const d = conv.d;
  const rows = [CATS.slice(0, 3).map((c, i) => [(d.cats.includes(c) ? "✅ " : "") + c, "k:" + i]),
    CATS.slice(3).map((c, i) => [(d.cats.includes(c) ? "✅ " : "") + c, "k:" + (i + 3)]),
    [["다음 →", "k:next"]], [["취소", "m"]]];
  return show(env, chat, msgId, (warn ? "⚠️ " + warn + "\n" : "") + "어떤 품목을 볼까요? (여러 개 선택)", rows);
}

function priceMenu(env, chat, msgId, text) {
  const rows = [PRICES.slice(0, 3).map(([v, l]) => [l, "p:" + v]), PRICES.slice(3).map(([v, l]) => [l, "p:" + v]),
    [["직접 입력", "p:x"], ["상관없음", "p:0"]], [["취소", "m"]]];
  return show(env, chat, msgId, text, rows);
}

async function afterPrice(env, chat, msgId, conv) {
  if (conv.flow === "h") return confirm(env, chat, msgId, conv);
  if (conv.flow === "e") {
    conv.wait = "exclude";
    await setConv(env, conv);
    return show(env, chat, msgId, "특이사항 1/2: 결과에서 <b>뺄 말</b>을 입력해 주세요 (쉼표로 구분).\n예: 일본판, 케이스, 고장", [[["없음", "x:ex"]], [["취소", "m"]]]);
  }
  return condMenu(env, chat, msgId, conv);
}

async function askInclude(env, chat, msgId, conv) {
  conv.wait = "include";
  await setConv(env, conv);
  return show(env, chat, msgId, "특이사항 2/2: <b>꼭 들어가야 할 말</b>을 입력해 주세요 (쉼표로 구분).\n예: 미개봉, 정품", [[["없음", "x:in"]], [["취소", "m"]]]);
}

function condMenu(env, chat, msgId) {
  return show(env, chat, msgId, "새 상품과 중고 중 무엇을 볼까요?", [
    [["둘 다", "n:a"], ["새 상품만", "n:n"], ["중고만", "n:u"]], [["취소", "m"]],
  ]);
}

async function confirm(env, chat, msgId, conv) {
  const d = conv.d;
  const cond = { a: "새 상품·중고", n: "새 상품만", u: "중고만" }[d.cond || "a"];
  let lines;
  if (conv.flow === "h") {
    lines = ["🛍 <b>쇼핑 시작</b>", "브랜드: " + d.brands.join(", "), "품목: " + d.cats.join(", "),
      "세일: " + (d.min_discount ? d.min_discount + "% 이상" : "상관없음") + " + 후기 좋은 것", "상한선: " + won(d.max_price)];
  } else {
    lines = [(conv.flow === "t" ? "🎯 <b>트래킹 시작</b>" : "📦 <b>기타 물건 추적</b>"), "상품: " + esc(d.query),
      "상한선: " + won(d.max_price), "범위: " + cond];
    if (conv.flow === "e") {
      lines.push("뺄 말: " + ((d.exclude || []).join(", ") || "없음"));
      lines.push("꼭 들어갈 말: " + ((d.include || []).join(", ") || "없음"));
    }
  }
  return show(env, chat, msgId, lines.join("\n"), [[["✅ 시작", "go"], ["✏️ 처음부터", "m"]]]);
}

async function start(env, chat, msgId, conv) {
  const d = conv.d;
  let w;
  if (conv.flow === "h") {
    w = { kind: "brand", brands: d.brands, cats: d.cats, min_discount: d.min_discount || 0, max_price: d.max_price || null };
  } else if (conv.flow === "t") {
    w = { kind: "item", query: d.query, max_price: d.max_price || null, cond: d.cond || "a" };
  } else {
    w = { kind: "etc", query: d.query, max_price: d.max_price || null, cond: d.cond || "a",
      exclude: d.exclude || [], include: d.include || [] };
  }
  await enqueue(env, { action: "add", watch: w });
  await setConv(env, null);
  return show(env, chat, msgId, "⏳ 시작 요청했습니다. 1~2분 안에 지금 있는 것부터 보내 드립니다.", [[["← 처음", "m"]]]);
}

async function listMenu(env, chat, msgId) {
  const st = await repoJson(env, "state/watches.json", { list: [] });
  const menu = await repoJson(env, "state/menu_data.json", { fixed: [] });
  const icon = { item: "🎯", brand: "🛍", etc: "📦" };
  const lines = ["📋 <b>진행 중</b>"];
  const rows = [];
  (menu.fixed || []).forEach((f, i) => {
    lines.push("• " + esc(f) + " (기본)");
    rows.push([[`⏹ ${f.slice(0, 20)} 중단`, "sf:" + i]]);
  });
  for (const w of st.list || []) {
    lines.push(`• #${w.id} ${icon[w.kind] || ""} ${esc(w.title || w.query || "")}`);
    rows.push([[`⏹ #${w.id} 중단`, "s:" + w.id]]);
  }
  if (!(st.list || []).length) lines.push("버튼으로 시작한 추적은 아직 없습니다.");
  rows.push([["← 처음", "m"]]);
  return show(env, chat, msgId, lines.join("\n"), rows);
}
