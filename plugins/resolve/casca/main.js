"use strict";

const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const ID = "com.ciclomedia.delapraca";
const PORTA = 7823;
const ORIGEM = `http://127.0.0.1:${PORTA}`;
const PAINEL = `${ORIGEM}/painel?nle=resolve`;
const PASTAS_VALIDAS = new Set(["De Lá Pra Cá", "Inserts"]);
const WIN = process.platform === "win32";

function pastaDados() {
  if (WIN) return process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "DeLaPraCa") : null;
  return process.env.HOME ? path.join(process.env.HOME, "Library", "Application Support", "DeLaPraCa") : null;
}

function dadosDesviados() {
  const d = pastaDados();
  if (!d) return null;
  const padrao = WIN
    ? (process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "DeLaPraCa") : null)
    : (process.env.HOME ? path.join(process.env.HOME, "Library", "Application Support", "DeLaPraCa") : null);
  return d !== padrao ? d : null;
}

function pastaLogs() {
  const desviada = dadosDesviados();
  if (desviada) return path.join(desviada, "Logs");
  if (WIN) return pastaDados() ? path.join(pastaDados(), "Logs") : null;
  return process.env.HOME ? path.join(process.env.HOME, "Library", "Logs", "DeLaPraCa") : null;
}

function pastaCache() {
  const d = pastaDados();
  if (!d) return null;
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(d, "configuracoes.json"), "utf8"));
    const c = cfg && cfg.cache_dir;
    const local = typeof c === "string" && !/^[\\/]{2}/.test(c)
      && (WIN ? /^[A-Za-z]:[\\/]/.test(c) : c.startsWith("/"));
    if (local && fs.existsSync(path.parse(c).root)) return path.normalize(c);
  } catch (_) {  }
  const desviada = dadosDesviados();
  if (desviada) return path.join(desviada, "Cache");
  if (WIN) return path.join(d, "Cache");
  return process.env.HOME ? path.join(process.env.HOME, "Library", "Caches", "DeLaPraCa") : null;
}

const LOG_TETO = 4 * 1024 * 1024;
const LOG_GUARDADOS = 4;
function log(texto) {
  try {
    const d = pastaLogs();
    if (!d) return;
    fs.mkdirSync(d, { recursive: true });
    const alvo = path.join(d, "casca-resolve.log");
    try {
      if (fs.statSync(alvo).size > LOG_TETO) {
        for (let i = LOG_GUARDADOS - 1; i >= 1; i--) {
          try { fs.renameSync(`${alvo}.${i}`, `${alvo}.${i + 1}`); } catch (_) {  }
        }
        fs.renameSync(alvo, `${alvo}.1`);
      }
    } catch (_) {  }
    fs.appendFileSync(alvo, `${new Date().toISOString()} ${texto}\n`);
  } catch (_) {  }
}

function token() {
  try {
    const t = fs.readFileSync(path.join(pastaDados(), "token"), "utf8").trim();
    return /^[0-9a-f]+$/i.test(t) ? t : "";
  } catch (_) { return ""; }
}

function estado() {
  return new Promise((resolve) => {
    const tk = token();
    if (!tk) { resolve(null); return; }
    const req = http.get({ host: "127.0.0.1", port: PORTA, path: "/receber/estado", timeout: 3000,
                           headers: { "X-DeLaPraCa-Token": tk } }, (res) => {
      let corpo = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { corpo += c; if (corpo.length > 65536) req.destroy(); });
      res.on("end", () => {
        try { const j = JSON.parse(corpo); resolve(res.statusCode === 200 && j.versao ? j : null); }
        catch (_) { resolve(null); }
      });
    });
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve(null));
  });
}

function caminhoDoApp() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(pastaDados(), "aplicativo.json"), "utf8"));
    const exe = cfg && cfg.executavel;
    if (typeof exe !== "string" || !path.isAbsolute(exe) || !fs.existsSync(exe)) return null;
    return /^delapraca(\.exe)?$/i.test(path.basename(exe)) ? exe : null;
  } catch (_) { return null; }
}

function abrirApp() {
  const exe = caminhoDoApp();
  if (!exe) return { ok: false, erro: "não sei onde o aplicativo está nesta máquina: abra o De Lá Pra Cá pelo Finder/menu Iniciar" };
  const filho = spawn(exe, [], { detached: true, stdio: "ignore", windowsHide: false });
  filho.on("error", (e) => log(`abrir o app falhou: ${e.message}`));
  filho.unref();
  log(`abrindo o app: ${exe}`);
  return { ok: true };
}

const espera = (ms) => new Promise((r) => setTimeout(r, ms));

async function esperarServico(aoFaltar) {
  let avisou = false;
  for (;;) {
    if (await estado()) return;
    if (!avisou) { avisou = true; log("motor fora do ar — esperando o aplicativo"); aoFaltar(); }
    await espera(1500);
  }
}

let resolveObj = null;
function resolveApi() {
  if (resolveObj) return resolveObj;
  const WI = require("./WorkflowIntegration.node");
  if (!WI.Initialize(ID)) throw new Error("o Resolve recusou a conexão do plugin");
  resolveObj = WI.GetResolve();
  if (!resolveObj) throw new Error("o Resolve não respondeu");
  return resolveObj;
}

function drtConfiavel(caminho) {
  const base = pastaCache();
  if (typeof caminho !== "string" || !base) return false;
  const pasta = path.join(base, "recebidos");
  const alvo = path.resolve(caminho);
  const mesmo = (a, b) => (WIN ? a.toLowerCase() === b.toLowerCase() : a === b);
  if (!mesmo(path.dirname(alvo), pasta)) return false;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.drt$/.test(path.basename(alvo));
}

function pastaNoPool(mp, nome, mae) {
  const raiz = mae || mp.GetRootFolder();
  for (const f of raiz.GetSubFolderList() || []) {
    if (f.GetName() === nome) return f;
  }
  return mp.AddSubFolder(raiz, nome);
}

function midiasDoDrt(drt, chave = "midias") {
  const lista = drt.replace(/\.drt$/i, ".midias.json");
  let dados;
  try { dados = JSON.parse(fs.readFileSync(lista, "utf8")); } catch (_) { return []; }
  const fora = [];
  for (const p of (dados && Array.isArray(dados[chave]) ? dados[chave] : []).slice(0, 5000)) {
    if (typeof p !== "string" || !path.isAbsolute(p) || p.includes("\0")) continue;
    try { if (fs.statSync(p).isFile()) fora.push(p); } catch (_) {  }
  }
  return fora;
}

function caminhosNoPool(mp) {
  const vistos = new Set();
  const norma = (p) => (WIN ? p.toLowerCase() : p);
  const andar = (pasta, fundo) => {
    if (!pasta || fundo > 30) return;
    for (const c of pasta.GetClipList() || []) {
      try { const p = c.GetClipProperty("File Path"); if (p) vistos.add(norma(p)); } catch (_) {  }
    }
    for (const f of pasta.GetSubFolderList() || []) andar(f, fundo + 1);
  };
  andar(mp.GetRootFolder(), 0);
  return { tem: (p) => vistos.has(norma(p)) };
}

function pastaDaMidia(mp, nome) {
  const casa = pastaNoPool(mp, "De Lá Pra Cá");
  const midia = casa && pastaNoPool(mp, "Mídia", casa);
  const titulo = (typeof nome === "string" && nome.trim() ? nome.trim() : "timeline").slice(0, 120);
  return midia && pastaNoPool(mp, titulo, midia);
}

function moverDepois(mp, drt, nome, pastaDaImportacao) {
  const depois = midiasDoDrt(drt, "depois");
  if (!depois.length || !pastaDaImportacao) return 0;
  const norma = (p) => (WIN ? p.toLowerCase() : p);
  const querer = new Set(depois.map(norma));
  const destino = pastaDaMidia(mp, nome);
  if (!destino) return 0;
  const casa = pastaNoPool(mp, "De Lá Pra Cá");
  const midia = casa && pastaNoPool(mp, "Mídia", casa);
  const achados = [];
  const andar = (pasta, fundo) => {
    if (!pasta || fundo > 30) return;
    const id = (f) => { try { return f.GetUniqueId(); } catch (_) { return null; } };
    if (midia && id(pasta) && id(pasta) === id(midia)) return;
    if (fundo === 1 && pasta.GetName() === "Mídia") return;
    for (const c of pasta.GetClipList() || []) {
      try { const p = c.GetClipProperty("File Path"); if (p && querer.has(norma(p))) achados.push(c); } catch (_) {  }
    }
    for (const f of pasta.GetSubFolderList() || []) andar(f, fundo + 1);
  };
  andar(pastaDaImportacao, 0);
  if (!achados.length) return 0;
  let ok = false;
  try { ok = mp.MoveClips(achados, destino); } catch (e) { log(`MoveClips: ${e.message}`); }
  log(`áudio de 1–2 canais: ${achados.length} itens do DRT ${ok ? "movidos" : "NÃO movidos"} para a pasta da mídia`);
  return ok ? achados.length : 0;
}

function trazerMidias(pedido) {
  const { drt, nome } = pedido || {};
  if (!drtConfiavel(drt)) return { ok: false, erro: "arquivo fora da pasta do De Lá Pra Cá — recusado" };
  const midias = midiasDoDrt(drt);
  if (!midias.length) return { ok: true, importadas: 0, ja_estavam: 0 };
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const mp = projeto.GetMediaPool();
  const noPool = caminhosNoPool(mp);
  const faltam = midias.filter((p) => !noPool.tem(p));
  if (!faltam.length) return { ok: true, importadas: 0, ja_estavam: midias.length };
  const alvo = pastaDaMidia(mp, nome);
  if (alvo) mp.SetCurrentFolder(alvo);
  const t0 = Date.now();
  let itens = [];
  try { itens = mp.ImportMedia(faltam) || []; } catch (e) { log(`ImportMedia: ${e.message}`); }
  log(`mídia: ${itens.length} de ${faltam.length} importados (${midias.length - faltam.length} já estavam) em ${Date.now() - t0} ms`);
  return { ok: true, importadas: itens.length, ja_estavam: midias.length - faltam.length,
           faltaram: Math.max(0, faltam.length - itens.length) };
}

function nomeLivre(projeto, tl, nome) {
  const usados = new Set();
  try {
    for (const t of timelines(projeto)) {
      if (t && t !== tl) usados.add(String(t.GetName() || "").toLowerCase());
    }
  } catch (_) {  }
  for (let i = 1; i < 1000; i++) {
    const tenta = i === 1 ? nome : `${nome} ${i}`;
    if (!usados.has(tenta.toLowerCase())) return i;
  }
  return 1;
}

function nomear(projeto, tl, nome) {
  if (typeof nome !== "string" || !nome) return;
  for (let i = nomeLivre(projeto, tl, nome); i < 1000; i++) {
    try { if (tl.SetName(i === 1 ? nome : `${nome} ${i}`)) return; } catch (_) { return; }
  }
}

function timelines(projeto) {
  const fora = [];
  const n = projeto.GetTimelineCount() || 0;
  for (let i = 1; i <= n; i++) fora.push(projeto.GetTimelineByIndex(i));
  return fora;
}

function importarDrt(projeto, mp, drt) {
  const chave = (t) => { try { return t.GetUniqueId() || t.GetName(); } catch (_) { return t.GetName(); } };
  const antes = new Set(timelines(projeto).map(chave));
  const nova = () => timelines(projeto).find((t) => !antes.has(chave(t))) || null;
  for (const [rotulo, chamar] of [["sem opções", () => mp.ImportTimelineFromFile(drt)],
                                  ["com {}", () => mp.ImportTimelineFromFile(drt, {})]]) {
    let tl = null;
    try { tl = chamar(); } catch (e) { log(`ImportTimelineFromFile ${rotulo}: ${e.message}`); }
    if (tl && typeof tl.GetName === "function") { log(`importou (${rotulo}), devolveu a Timeline`); return tl; }
    const achada = nova();
    if (achada) { log(`importou (${rotulo}), achada pela diferença`); return achada; }
  }
  return null;
}

function agulhaNoInicio(tl) {
  try {
    const tc = tl.GetStartTimecode();
    if (tc) tl.SetCurrentTimecode(tc);
  } catch (e) { log(`agulha no início: ${e.message}`); }
}

function importar(pedido) {
  const { drt, pasta, nome } = pedido || {};
  if (!drtConfiavel(drt)) {
    return { ok: false, erro: "o serviço respondeu um arquivo fora da pasta do De Lá Pra Cá — recusado" };
  }
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const mp = projeto.GetMediaPool();
  const alvo = pastaNoPool(mp, PASTAS_VALIDAS.has(pasta) ? pasta : "De Lá Pra Cá");
  if (alvo) mp.SetCurrentFolder(alvo);
  const t0 = Date.now();
  const tl = importarDrt(projeto, mp, drt);
  if (!tl) return { ok: false, erro: "o Resolve não importou a timeline" };
  nomear(projeto, tl, typeof nome === "string" ? nome.slice(0, 200) : "");
  try { projeto.SetCurrentTimeline(tl); } catch (_) {  }
  agulhaNoInicio(tl);
  const final = tl.GetName();
  log(`importado ${path.basename(drt)} → "${final}" em ${Date.now() - t0} ms`);
  let movidas = 0;
  try { movidas = moverDepois(mp, drt, nome, alvo); } catch (e) { log(`moverDepois: ${e.message}`); }
  let uid = "";
  try { uid = tl.GetUniqueId() || ""; } catch (_) {  }
  return { ok: true, nome: final, uid, movidas };
}

function uuid4() {
  const b = require("crypto").randomBytes(16);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = b.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function acharTimeline(projeto, uid, nome) {
  const todas = [];
  for (const t of timelines(projeto)) {
    try { todas.push({ t, uid: t.GetUniqueId() || "", nome: t.GetName() || "" }); } catch (_) {  }
  }
  const par = todas.find((x) => uid && nome && x.uid === uid && x.nome === nome);
  if (par) return par.t;
  const pelo_nome = todas.filter((x) => nome && x.nome === nome);
  if (pelo_nome.length === 1) return pelo_nome[0].t;
  const pelo_id = todas.filter((x) => uid && x.uid === uid);
  if (pelo_id.length > 1) log(`acharTimeline: ${pelo_id.length} timelines com o id ${uid}: ${pelo_id.map((x) => x.nome).join(" | ")}`);
  return pelo_id.length === 1 ? pelo_id[0].t : null;
}

function contarItens(tl) {
  const fora = {};
  for (const [tipo, letra] of [["video", "V"], ["audio", "A"]]) {
    const n = tl.GetTrackCount(tipo) || 0;
    for (let i = 1; i <= n; i++) {
      const c = (tl.GetItemListInTrack(tipo, i) || []).length;
      if (c) fora[`${letra}${i}`] = c;
    }
  }
  return fora;
}

function listarTimelines() {
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  let atual = "";
  try { const t = projeto.GetCurrentTimeline(); if (t) atual = t.GetName() || ""; } catch (_) {  }
  const lista = [];
  for (const t of timelines(projeto)) {
    try {
      const nome = t.GetName() || "";
      lista.push({ uid: t.GetUniqueId() || "", nome, atual: !!atual && nome === atual });
    } catch (_) {  }
  }
  const vistos = {};
  for (const x of lista) if (x.uid) vistos[x.uid] = (vistos[x.uid] || []).concat([x.nome]);
  for (const [uid, nomes] of Object.entries(vistos)) {
    if (nomes.length > 1) log(`timelines com o MESMO id ${uid} pela ponte: ${nomes.join(" | ")}`);
  }
  return { ok: true, timelines: lista };
}

function exportarTrabalho(pedido) {
  const { uid, nome } = pedido || {};
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const tl = acharTimeline(projeto, uid, nome);
  if (!tl) return { ok: false, erro: `a timeline "${nome || ""}" não está neste projeto — nada foi alterado` };
  let aberta = null;
  try { aberta = projeto.GetCurrentTimeline(); } catch (_) {  }
  projeto.SetCurrentTimeline(tl);
  try {
    const tipo = typeof r.EXPORT_DRT === "number" ? r.EXPORT_DRT : 1;
    const destino = path.join(pastaCache(), "recebidos", `${uuid4()}.drt`);
    fs.mkdirSync(path.dirname(destino), { recursive: true });
    let ok = false;
    try { ok = tl.Export(destino, tipo); } catch (e) { log(`Export: ${e.message}`); }
    if (!ok || !fs.existsSync(destino)) return { ok: false, erro: "o Resolve não exportou a timeline de trabalho" };
    let inicio = null;
    try { const q = tl.GetStartFrame(); if (typeof q === "number") inicio = q; } catch (_) {  }
    log(`exportou a timeline de trabalho "${tl.GetName()}" → ${path.basename(destino)}`);
    return { ok: true, drt: destino, contagem: contarItens(tl), inicio, nome: tl.GetName() };
  } finally {
    try {
      if (aberta && aberta.GetName() !== tl.GetName()) projeto.SetCurrentTimeline(aberta);
    } catch (_) {  }
  }
}

function mesmaContagem(a, b) {
  const ka = Object.keys(a || {}).filter((k) => a[k]).sort();
  const kb = Object.keys(b || {}).filter((k) => b[k]).sort();
  return ka.length === kb.length && ka.every((k, i) => k === kb[i] && Number(a[k]) === Number(b[k]));
}

function itemDaTimelineNoPool(mp, nome) {
  let achado = null;
  const andar = (pasta, fundo) => {
    if (!pasta || fundo > 30 || achado) return;
    for (const c of pasta.GetClipList() || []) {
      try { if (c.GetClipProperty("Type") === "Timeline" && c.GetName() === nome) { achado = c; return; } }
      catch (_) {  }
    }
    for (const f of pasta.GetSubFolderList() || []) andar(f, fundo + 1);
  };
  andar(mp.GetRootFolder(), 0);
  return achado;
}

function itensDoPoolPorArquivo(mp) {
  const fora = new Map();
  const norma = (p) => (WIN ? p.toLowerCase() : p);
  const andar = (pasta, fundo) => {
    if (!pasta || fundo > 30) return;
    for (const c of pasta.GetClipList() || []) {
      try { const p = c.GetClipProperty("File Path"); if (p && !fora.has(norma(p))) fora.set(norma(p), c); } catch (_) {  }
    }
    for (const f of pasta.GetSubFolderList() || []) andar(f, fundo + 1);
  };
  andar(mp.GetRootFolder(), 0);
  return { de: (p) => fora.get(norma(p)) || null };
}

function carimbo() {
  const d = new Date();
  const z = (n) => String(n).padStart(2, "0");
  return `${z(d.getDate())}-${z(d.getMonth() + 1)} ${z(d.getHours())}h${z(d.getMinutes())}`;
}

function avisador(event, rotulo) {
  const t0 = Date.now();
  return async (texto) => {
    log(`${rotulo} +${Date.now() - t0} ms · ${texto}`);
    try { event.sender.send("dlpc:andamento", { texto }); } catch (_) {  }
    await new Promise((ok) => setImmediate(ok));
  };
}

async function aplicarAtualizacao(pedido, avisar = async () => {}) {
  const { drt, esperado, antiga, inserir, marcas, marcar, colorir, nome } = pedido || {};
  if (!drtConfiavel(drt)) return { ok: false, erro: "arquivo fora da pasta do De Lá Pra Cá — recusado" };
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const mp = projeto.GetMediaPool();
  const velha = acharTimeline(projeto, (antiga || {}).uid, (antiga || {}).nome);
  if (!velha) return { ok: false, erro: "a timeline de trabalho sumiu do projeto — nada foi alterado" };
  const casa = pastaNoPool(mp, "De Lá Pra Cá");
  if (casa) mp.SetCurrentFolder(casa);

  await avisar("o Resolve está importando a timeline reescrita (a parte mais longa)");
  const nova = importarDrt(projeto, mp, drt);
  if (!nova) return { ok: false, erro: "o Resolve não importou a timeline atualizada — nada foi alterado" };
  await avisar("conferindo a timeline importada, trilha por trilha");
  const contada = contarItens(nova);
  if (!mesmaContagem(contada, esperado)) {
    try { mp.DeleteTimelines([nova]); } catch (e) { log(`DeleteTimelines: ${e.message}`); }
    log(`atualizar: contagem ${JSON.stringify(contada)} ≠ prevista ${JSON.stringify(esperado)}`);
    return { ok: false, erro: "a timeline atualizada não entrou como devia; ela foi descartada e a sua "
                              + "timeline de trabalho ficou como estava" };
  }

  projeto.SetCurrentTimeline(nova);
  const pedidos = Array.isArray(inserir) ? inserir.length : 0;
  if (pedidos) await avisar(`procurando no pool a mídia de ${pedidos} plano(s) novo(s)`);
  const pool = itensDoPoolPorArquivo(mp);
  const infos = [], origem = [], faltaram = [];
  for (const x of Array.isArray(inserir) ? inserir.slice(0, 5000) : []) {
    const item = typeof x.arquivo === "string" ? pool.de(x.arquivo) : null;
    if (!item) { faltaram.push(x.nome || x.arquivo); continue; }
    const tipo = x.tipo === "A" ? "audio" : "video";
    while ((nova.GetTrackCount(tipo) || 0) < Number(x.trilha)) {
      try { if (!nova.AddTrack(tipo)) break; } catch (_) { break; }
    }
    infos.push({ mediaPoolItem: item, startFrame: Number(x.inicio_fonte), endFrame: Number(x.fim_fonte),
                 mediaType: x.tipo === "A" ? 2 : 1, trackIndex: Number(x.trilha), recordFrame: Number(x.quadro) });
    origem.push(x);
  }
  let inseridos = [];
  if (infos.length) {
    await avisar(`inserindo ${infos.length} plano(s) novo(s)`);
    try { inseridos = mp.AppendToTimeline(infos) || []; } catch (e) { log(`AppendToTimeline: ${e.message}`); }
  }
  if (faltaram.length || inseridos.length < infos.length) {
    try { mp.DeleteTimelines([nova]); } catch (e) { log(`DeleteTimelines: ${e.message}`); }
    try { projeto.SetCurrentTimeline(velha); } catch (_) {  }
    log(`atualizar: ${inseridos.length} de ${infos.length} inseridos, ${faltaram.length} sem mídia no pool — desfeito`);
    return { ok: false, erro: `${infos.length - inseridos.length + faltaram.length} plano(s) novo(s) não entraram `
      + `(${faltaram.slice(0, 5).join(", ") || "o Resolve recusou"}); a atualização foi desfeita e a sua `
      + "timeline de trabalho ficou como estava" };
  }

  const conferir = [];
  const daTrilha = new Map();
  origem.forEach((x, i) => {
    const it = inseridos[i], pedido = Number(x.fim_fonte) - Number(x.inicio_fonte);
    const tipo = x.tipo === "A" ? "audio" : "video", chave = `${tipo}${x.trilha}`;
    let veio = null;
    try {
      if (!daTrilha.has(chave)) daTrilha.set(chave, nova.GetItemListInTrack(tipo, Number(x.trilha)) || []);
      const achado = daTrilha.get(chave).find((t) => Number(t.GetStart()) === Number(x.quadro));
      const fonte = achado || it;
      veio = fonte ? Number(fonte.GetDuration()) : null;
    } catch (_) {  }
    if (veio !== pedido) {
      conferir.push(veio === null || Number.isNaN(veio)
        ? `${x.nome || "plano novo"} (${x.tipo}${x.trilha}): não achei o plano na trilha depois de inserir — conferir`
        : `${x.nome || "plano novo"} (${x.tipo}${x.trilha}): entrou com ${veio} quadro(s), o Avid tem ${pedido} — conferir`);
    }
  });
  if (conferir.length) log(`atualizar: ${conferir.length} plano(s) com outro tamanho: ${conferir.slice(0, 5).join(" | ")}`);

  await avisar("guardando a timeline anterior em De Lá Pra Cá › Backup");
  const nomeVelho = velha.GetName();
  const nomeBackup = `${nomeVelho} (backup ${carimbo()})`;
  nomear(projeto, velha, nomeBackup);
  const backupNome = velha.GetName();
  const pastaBackup = casa && pastaNoPool(mp, "Backup", casa);
  const itemVelho = itemDaTimelineNoPool(mp, backupNome);
  let naPasta = false;
  if (pastaBackup && itemVelho) {
    try { naPasta = mp.MoveClips([itemVelho], pastaBackup); } catch (e) { log(`MoveClips backup: ${e.message}`); }
  }
  nomear(projeto, nova, typeof nome === "string" && nome.trim() ? nome.trim().slice(0, 200) : nomeVelho);
  projeto.SetCurrentTimeline(nova);

  let nMarcas = 0, nCores = 0;
  const sinais = (marcar || colorir) && Array.isArray(marcas) ? marcas.slice(0, 5000) : [];
  let ultimo = 0;
  const itensDaTrilha = new Map();
  for (const [i, m] of sinais.entries()) {
    if (i === 0 || Date.now() - ultimo > 500) {
      await avisar(`aplicando os Sinais: ${i + 1} de ${sinais.length}`);
      ultimo = Date.now();
    }
    if (marcar) {
      try { if (nova.AddMarker(Number(m.quadro), m.cor_marcador, m.nome, "", Math.max(1, Number(m.dur) || 1), m.palavra || "DLPC")) nMarcas++; }
      catch (_) {  }
    }
    if (colorir) {
      try {
        const t = Number(m.trilha);
        if (!itensDaTrilha.has(t)) itensDaTrilha.set(t, nova.GetItemListInTrack("video", t) || []);
        const alvo = itensDaTrilha.get(t).find((it) => Number(it.GetStart()) === Number(m.quadro_abs));
        if (alvo && alvo.SetClipColor(m.cor_clipe)) nCores++;
      } catch (_) {  }
    }
  }
  let uid = "";
  try { uid = nova.GetUniqueId() || ""; } catch (_) {  }
  log(`atualizado: "${nova.GetName()}" (backup "${backupNome}"), ${inseridos.length} inseridos, ${faltaram.length} sem mídia no pool`);
  return { ok: true, nome: nova.GetName(), uid, backup: backupNome, backup_na_pasta: !!naPasta,
           inseridos: inseridos.length, pedidos: infos.length, faltaram, conferir, marcadores: nMarcas, cores: nCores };
}

function doPainel(event) {
  try { return new URL(event.sender.getURL()).origin === ORIGEM; } catch (_) { return false; }
}

ipcMain.handle("dlpc:importar", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return importar(pedido); } catch (e) {
    log(`importar falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

ipcMain.handle("dlpc:midias", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return trazerMidias(pedido); } catch (e) {
    log(`midias falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

ipcMain.handle("dlpc:timelines", async (event) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return listarTimelines(); } catch (e) {
    log(`timelines falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

ipcMain.handle("dlpc:exportarTrabalho", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return exportarTrabalho(pedido); } catch (e) {
    log(`exportarTrabalho falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

ipcMain.handle("dlpc:atualizar", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return await aplicarAtualizacao(pedido, avisador(event, "atualizar")); } catch (e) {
    log(`atualizar falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

ipcMain.handle("dlpc:religar", async (event) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  return (await estado()) ? { ok: true } : { ok: false, erro: "abra o aplicativo De Lá Pra Cá" };
});

function daTelaDeEspera(event) {
  try {
    const u = new URL(event.sender.getURL());
    return u.protocol === "file:" && path.resolve(decodeURIComponent(u.pathname.replace(/^\/([A-Za-z]:)/, "$1")))
      === path.resolve(path.join(__dirname, "carregando.html"));
  } catch (_) { return false; }
}
const esperando = new WeakSet();
ipcMain.handle("dlpc:esperarMotor", async (event) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  const w = BrowserWindow.fromWebContents(event.sender);
  if (!w || esperando.has(w)) return { ok: true };
  if (await estado()) return { ok: true };
  esperando.add(w);
  log("motor caiu com o painel aberto — de volta à tela de espera");
  w.loadFile(path.join(__dirname, "carregando.html"), { query: { estado: "fora", app: caminhoDoApp() ? "1" : "" } });
  esperarServico(() => {})
    .then(() => { esperando.delete(w); if (!w.isDestroyed()) { log("motor no ar"); w.loadURL(PAINEL); } });
  return { ok: true };
});

ipcMain.handle("dlpc:abrirApp", async (event) => {
  if (!doPainel(event) && !daTelaDeEspera(event)) return { ok: false, erro: "pedido recusado" };
  try { return abrirApp(); } catch (e) { return { ok: false, erro: (e && e.message) || String(e) }; }
});

ipcMain.handle("dlpc:projeto", async (event) => {
  if (!doPainel(event)) return {};
  const r = resolveApi();
  const p = r.GetProjectManager().GetCurrentProject();
  return { versao: r.GetVersionString(), projeto: p ? p.GetName() : "" };
});

const FUSION_TIPOS = new Set(["PolylineMask", "Blur", "CornerPositioner"]);
const FUSION_ENTRADAS = new Set(["XBlurSize", "YBlurSize", "LockXY"]);
const FUSION_LIGACOES = new Set(["Input", "EffectMask"]);
const FUSION_CANTOS = ["TopLeft", "TopRight", "BottomLeft", "BottomRight"];
const FUSION_SAIDA = { PolylineMask: "Mask", Blur: "Output", CornerPositioner: "Output", Loader: "Output" };
const FUSION_IDS = /^(MediaIn1|MediaOut1|(Polygon|Blur|CornerPositioner)\d{1,3})$/;

function num(v) {
  const n = Number(v);
  if (typeof v !== "number" || !Number.isFinite(n)) throw new Error("número inválido no plano do Fusion");
  const t = n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  return t === "-0" || t === "" ? "0" : t;
}

function entradasFusion(no) {
  if (no.tipo === "PolylineMask") {
    const pts = (no.pontos || []).map((p) => (p.linear
      ? `{ Linear = true, X = ${num(p.x)}, Y = ${num(p.y)} }`
      : `{ X = ${num(p.x)}, Y = ${num(p.y)}, LX = ${num(p.lx)}, LY = ${num(p.ly)}, RX = ${num(p.rx)}, RY = ${num(p.ry)} }`));
    if (!pts.length) throw new Error("Polygon sem pontos");
    return (no.inverter ? ["Invert = Input { Value = 1, }"] : []).concat([
      `MaskWidth = Input { Value = ${Math.trunc(Number(num(no.largura)))}, }`,
      `MaskHeight = Input { Value = ${Math.trunc(Number(num(no.altura)))}, }`,
      "UseFrameFormatSettings = Input { Value = 0, }",
      `SoftEdge = Input { Value = ${num(no.suave)}, }`,
      `Polyline = Input { Value = Polyline { Closed = true, Points = { ${pts.join(", ")} } }, }`]);
  }
  if (no.tipo === "CornerPositioner") {
    return FUSION_CANTOS.map((k) => {
      const c = (no.cantos || {})[k];
      if (!Array.isArray(c) || c.length !== 2) throw new Error(`CornerPositioner sem ${k}`);
      return `${k} = Input { Value = { ${num(c[0])}, ${num(c[1])} }, }`;
    });
  }
  if (no.tipo === "Blur") {
    return Object.entries(no.entradas || {}).map(([k, v]) => {
      if (!FUSION_ENTRADAS.has(k)) throw new Error(`entrada ${k} recusada`);
      return `${k} = Input { Value = ${num(v)}, }`;
    });
  }
  throw new Error("nó inválido no plano");
}

function comporFusion(texto, plano) {
  const nos = (plano && plano.nos) || [];
  const ligacoes = (plano && plano.ligacoes) || [];
  for (const no of nos) {
    if (!no || !FUSION_TIPOS.has(no.tipo) || !FUSION_IDS.test(no.id || "")) throw new Error("nó inválido no plano");
  }
  const tipos = { MediaIn1: "Loader" };
  for (const no of nos) tipos[no.id] = no.tipo;
  const ligar = {};
  for (const l of ligacoes) {
    if (!Array.isArray(l) || l.length !== 3 || !FUSION_IDS.test(l[0]) || !FUSION_IDS.test(l[2])
        || !FUSION_LIGACOES.has(l[1]) || !tipos[l[2]] || (l[0] !== "MediaOut1" && !tipos[l[0]])) {
      throw new Error("ligação inválida no plano");
    }
    (ligar[l[0]] = ligar[l[0]] || []).push(`${l[1]} = Input { SourceOp = "${l[2]}", Source = "${FUSION_SAIDA[tipos[l[2]]]}", }`);
  }
  const defs = nos.map((no, k) => `${no.id} = ${no.tipo} { Inputs = { ${entradasFusion(no).concat(ligar[no.id] || []).join(", ")}, }, ` +
    `ViewInfo = OperatorInfo { Pos = { ${110 * (k + 1)}, 115 } }, },`);
  let iOut = texto.indexOf("MediaOut1 = Saver {");
  const iIn = texto.indexOf("MediaIn1 = Loader {");
  if (iOut < 0 || iIn < 0) throw new Error("composição exportada sem MediaIn1/MediaOut1");
  const fimOut = texto.indexOf("ViewInfo", iOut);
  let trecho = texto.slice(iOut, fimOut);
  for (const linha of ligar.MediaOut1 || []) {
    const origem = linha.split('"')[1];
    const novo = trecho.replace('SourceOp = "MediaIn1"', `SourceOp = "${origem}"`);
    if (novo === trecho) throw new Error("MediaOut1 sem a entrada do MediaIn1");
    trecho = novo;
  }
  texto = texto.slice(0, iOut) + trecho + texto.slice(fimOut);
  if (ligar.MediaIn1) {
    let j = texto.indexOf("Inputs = {", iIn);
    if (j < 0 || j > texto.indexOf("MediaOut1 = Saver {")) throw new Error("MediaIn1 sem Inputs");
    j += "Inputs = {".length;
    texto = texto.slice(0, j) + ligar.MediaIn1.map((x) => `\n\t\t\t\t${x},`).join("") + texto.slice(j);
  }
  iOut = texto.indexOf("MediaOut1 = Saver {");
  return texto.slice(0, iOut) + defs.join("\n\t\t") + "\n\t\t" + texto.slice(iOut);
}

const FUSION_MODELO = 'Composition { Tools = { MediaIn1 = Loader { Inputs = { }, ViewInfo = OperatorInfo { }, }, ' +
  'MediaOut1 = Saver { Inputs = { Input = Input { SourceOp = "MediaIn1", Source = "Output", }, }, ViewInfo = OperatorInfo { }, }, }, }';

function arquivoComp() {
  return path.join(require("os").tmpdir(), `dlpc_${uuid4()}.comp`);
}

function compMontada(item, indice, plano) {
  const arq = arquivoComp();
  try {
    if (!item.ExportFusionComp(arq, indice)) return false;
    const t = fs.readFileSync(arq, "utf8");
    return t.includes("MEDIA_PATH") && ((plano && plano.nos) || []).every((no) => t.includes(`${no.id} = ${no.tipo} {`));
  } catch (_) {
    return false;
  } finally {
    try { fs.unlinkSync(arq); } catch (_) {  }
  }
}

function executarPlano(item, plano) {
  let direto;
  try { direto = comporFusion(FUSION_MODELO, plano); } catch (e) { return { ok: false, erro: (e && e.message) || String(e) }; }
  if (!(item.GetFusionCompNameList() || []).length) {
    const arq = arquivoComp();
    try {
      fs.writeFileSync(arq, direto, "utf8");
      item.ImportFusionComp(arq);
    } finally {
      try { fs.unlinkSync(arq); } catch (_) {  }
    }
    const nomes = item.GetFusionCompNameList() || [];
    if (nomes.length && compMontada(item, nomes.length, plano)) return { ok: true, comp: nomes[nomes.length - 1] };
    if (nomes.length && !item.DeleteFusionCompByName(nomes[nomes.length - 1])) {
      return { ok: false, comp: nomes[nomes.length - 1], erro: "o Resolve não montou a composição — refazer à mão no Fusion" };
    }
  }
  item.AddFusionComp();
  const nomes = item.GetFusionCompNameList() || [];
  if (!nomes.length) return { ok: false, erro: "AddFusionComp falhou" };
  const nome = nomes[nomes.length - 1];
  item.LoadFusionCompByName(nome);
  const arq = arquivoComp();
  try {
    if (!item.ExportFusionComp(arq, nomes.length)) return { ok: false, comp: nome, erro: "ExportFusionComp falhou" };
    let texto;
    try { texto = comporFusion(fs.readFileSync(arq, "utf8"), plano); } catch (e) {
      return { ok: false, comp: nome, erro: (e && e.message) || String(e) };
    }
    fs.writeFileSync(arq, texto, "utf8");
    item.ImportFusionComp(arq);
  } finally {
    try { fs.unlinkSync(arq); } catch (_) {  }
  }
  if (!compMontada(item, nomes.length, plano)) {
    try { item.DeleteFusionCompByName(nome); } catch (_) {  }
    return { ok: false, comp: nome, erro: "o Resolve não montou a composição — refazer à mão no Fusion" };
  }
  return { ok: true, comp: nome };
}

function aplicarFusion(pedido) {
  const { nome, uid, clipes } = pedido || {};
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const tl = acharTimeline(projeto, uid, nome);
  if (!tl) return { ok: false, erro: `a timeline "${nome || ""}" não está neste projeto` };
  const inicio = tl.GetStartFrame();
  const fora = [];
  for (const c of (Array.isArray(clipes) ? clipes : []).slice(0, 2000)) {
    const base = { trilha: c && c.trilha, quadro: c && c.quadro, arquivo: c && c.arquivo };
    let alvo = null;
    try {
      for (const it of tl.GetItemListInTrack("video", Number(c.trilha)) || []) {
        if (it.GetStart() - inicio === Number(c.quadro) && it.GetName() === c.arquivo) { alvo = it; break; }
      }
    } catch (_) {  }
    if (!alvo) { fora.push(Object.assign(base, { ok: false, erro: "clipe não encontrado na timeline" })); continue; }
    try {
      fora.push(Object.assign(base, executarPlano(alvo, c.plano)));
    } catch (e) {
      fora.push(Object.assign(base, { ok: false, erro: (e && e.message) || String(e) }));
    }
  }
  const ok = fora.filter((x) => x.ok).length;
  log(`fusion: ${ok} de ${fora.length} composições criadas em "${tl.GetName()}"` +
      fora.filter((x) => !x.ok).map((x) => ` · ${x.arquivo}@${x.quadro}: ${x.erro}`).join(""));
  return { ok: true, criadas: ok, clipes: fora };
}

ipcMain.handle("dlpc:fusion", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return aplicarFusion(pedido); } catch (e) {
    log(`fusion falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

function aplicarCompostos(pedido) {
  const { nome, uid, grupos } = pedido || {};
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const tl = acharTimeline(projeto, uid, nome);
  if (!tl) return { ok: false, erro: `a timeline "${nome || ""}" não está neste projeto` };
  if (typeof tl.CreateCompoundClip !== "function") {
    return { ok: false, erro: "este Resolve não cria Compound Clip pela API — os nests ficaram em trilhas" };
  }
  const inicio = tl.GetStartFrame();
  const fora = [];
  for (const g of (Array.isArray(grupos) ? grupos : []).slice(0, 500)) {
    const clipes = Array.isArray(g && g.clipes) ? g.clipes.slice(0, 200) : [];
    const base = { nome: String((g && g.nome) || "Compound Clip"), clipes: clipes.length };
    const itens = clipes.map((c) => {
      try {
        for (const it of tl.GetItemListInTrack("video", Number(c.trilha)) || []) {
          if (it.GetStart() - inicio === Number(c.quadro) && it.GetName() === c.arquivo) return it;
        }
      } catch (_) {  }
      return null;
    });
    const faltam = itens.filter((x) => !x).length;
    if (faltam || !itens.length) {
      fora.push(Object.assign(base, { ok: false, erro: `${faltam} de ${itens.length} clipes não encontrados — o nest ficou em trilhas` }));
      continue;
    }
    try {
      const cc = tl.CreateCompoundClip(itens, { name: base.nome });
      fora.push(Object.assign(base, cc ? { ok: true } : { ok: false, erro: "o Resolve não criou o Compound Clip — o nest ficou em trilhas" }));
    } catch (e) {
      fora.push(Object.assign(base, { ok: false, erro: (e && e.message) || String(e) }));
    }
  }
  const ok = fora.filter((x) => x.ok).length;
  log(`compostos: ${ok} de ${fora.length} Compound Clips em "${tl.GetName()}"` +
      fora.filter((x) => !x.ok).map((x) => ` · ${x.nome}: ${x.erro}`).join(""));
  agulhaNoInicio(tl);
  return { ok: true, criados: ok, grupos: fora };
}

ipcMain.handle("dlpc:compostos", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return aplicarCompostos(pedido); } catch (e) {
    log(`compostos falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

let conferindo = null;


function imagemConfiavel(caminho) {
  const base = pastaCache();
  if (typeof caminho !== "string" || !base) return false;
  const pasta = path.join(base, "Conferências");
  const alvo = path.resolve(caminho);
  const interno = path.dirname(alvo);
  const mesmo = (a, b) => (WIN ? a.toLowerCase() === b.toLowerCase() : a.normalize("NFC") === b.normalize("NFC"));
  const conferencia = path.basename(path.dirname(interno));
  return mesmo(path.dirname(path.dirname(interno)), pasta)
    && path.basename(interno) === "_interno"
    && conferencia !== "" && conferencia !== "." && conferencia !== ".."
    && /^\d{1,9}\.jpg$/.test(path.basename(alvo));
}

function conferirInicio(pedido) {
  const { nome, uid, trilhaReferencia } = pedido || {};
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  if (!projeto) return { ok: false, erro: "nenhum projeto aberto no Resolve" };
  const tl = acharTimeline(projeto, uid, nome);
  if (!tl) return { ok: false, erro: `a timeline "${nome || ""}" não está neste projeto` };
  if (conferindo) conferirDevolver();
  projeto.SetCurrentTimeline(tl);
  let pagina = "edit";
  try { pagina = r.GetCurrentPage() || "edit"; } catch (_) {  }
  const n = tl.GetTrackCount("video");
  const trilhas = [];
  for (let t = 1; t <= n; t++) trilhas.push(!!tl.GetIsTrackEnabled("video", t));
  conferindo = { tl, pagina, trilhas };
  const ref = Number(trilhaReferencia);
  if (ref >= 1 && ref <= n) tl.SetTrackEnable("video", ref, false);
  r.OpenPage("color");
  log(`conferência: começou em "${tl.GetName()}" (referência na V${ref || "?"})`);
  return { ok: true, nome: tl.GetName() };
}

async function conferirQuadros(pedido) {
  if (!conferindo) return { ok: false, erro: "a conferência não foi iniciada" };
  const r = resolveApi();
  const projeto = r.GetProjectManager().GetCurrentProject();
  const { tl } = conferindo;
  const fora = [];
  for (const p of (Array.isArray(pedido && pedido.pontos) ? pedido.pontos : []).slice(0, 50)) {
    if (!p || typeof p.tc !== "string" || !/^\d\d:\d\d:\d\d[:;]\d\d$/.test(p.tc) || !imagemConfiavel(p.imagem)) {
      fora.push({ quadro: p && p.quadro, ok: false, erro: "ponto recusado" });
      continue;
    }
    let chegou = false;
    for (let i = 0; i < 5 && !chegou; i++) {
      try { tl.SetCurrentTimecode(p.tc); } catch (_) {  }
      await espera(i ? 250 : 60);
      try { chegou = tl.GetCurrentTimecode() === p.tc; } catch (_) { chegou = false; }
    }
    let ok = false;
    if (chegou) {
      try { ok = !!projeto.ExportCurrentFrameAsStill(path.resolve(p.imagem)); } catch (e) { log(`still: ${e.message}`); }
    }
    fora.push({ quadro: p.quadro, ok, erro: ok ? "" : (chegou ? "o Resolve não exportou o quadro" : "a agulha não chegou ao ponto") });
    if (chegou && conferindo) conferindo.ultimoTc = p.tc;
  }
  return { ok: true, quadros: fora };
}

function conferirDevolver() {
  if (!conferindo) return;
  const { tl, pagina, trilhas, ultimoTc } = conferindo;
  conferindo = null;
  try {
    trilhas.forEach((ligada, i) => {
      try { if (!!tl.GetIsTrackEnabled("video", i + 1) !== ligada) tl.SetTrackEnable("video", i + 1, ligada); } catch (_) {  }
    });
  } catch (_) {  }
  try { resolveApi().OpenPage(pagina || "edit"); } catch (_) {  }
  agulhaNoInicio(tl);
  setTimeout(() => {
    try { if (ultimoTc) tl.SetCurrentTimecode(ultimoTc); } catch (_) {  }
    setTimeout(() => agulhaNoInicio(tl), 150);
  }, 400);
}

function conferirFim(pedido) {
  const tl = conferindo ? conferindo.tl : null;
  const { marcas, cor, dado } = pedido || {};
  let postos = 0;
  try {
    if (tl && typeof dado === "string" && /^[a-z-]{1,40}$/.test(dado)) {
      for (let i = 0; i < 5000; i++) {
        let apagou = false;
        try { apagou = !!tl.DeleteMarkerByCustomData(dado); } catch (_) { apagou = false; }
        if (!apagou) break;
      }
      for (const m of (Array.isArray(marcas) ? marcas : []).slice(0, 5000)) {
        const q = Number(m && m.quadro);
        if (!Number.isInteger(q) || q < 0) continue;
        try {
          if (tl.AddMarker(q, String(cor || "Purple"), String(m.nome || "DLPC").slice(0, 120),
                           String(m.nota || "").slice(0, 500), 1, dado)) postos++;
        } catch (_) {  }
      }
    }
  } finally {
    conferirDevolver();
  }
  log(`conferência: ${postos} marcadores; trilhas, página e agulha devolvidas`);
  return { ok: true, marcadores: postos };
}

ipcMain.handle("dlpc:conferirInicio", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return conferirInicio(pedido); } catch (e) {
    log(`conferirInicio falhou: ${e && e.stack || e}`);
    try { conferirDevolver(); } catch (_) {  }
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});
ipcMain.handle("dlpc:conferirQuadros", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return await conferirQuadros(pedido); } catch (e) {
    log(`conferirQuadros falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});
ipcMain.handle("dlpc:conferirFim", async (event, pedido) => {
  if (!doPainel(event)) return { ok: false, erro: "pedido recusado" };
  try { return conferirFim(pedido); } catch (e) {
    log(`conferirFim falhou: ${e && e.stack || e}`);
    return { ok: false, erro: (e && e.message) || String(e) };
  }
});

function janela(opcoes) {
  const w = new BrowserWindow({
    useContentSize: true, backgroundColor: "#2b2b2b", title: "De Lá Pra Cá", ...opcoes,
    webPreferences: { preload: path.join(__dirname, "preload.js"),
                      contextIsolation: true, nodeIntegration: false },
  });
  w.setMenu(null);
  w.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(`${ORIGEM}/`)) e.preventDefault();
  });
  w.webContents.on("new-window", (e) => e.preventDefault());
  if (typeof w.webContents.setWindowOpenHandler === "function") {
    w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  }
  return w;
}

let revisao = null;
ipcMain.handle("dlpc:revisao", async (event) => {
  if (!doPainel(event)) return { janela: false };
  if (revisao && !revisao.isDestroyed()) { revisao.focus(); return { janela: true }; }
  revisao = janela({ width: 1100, height: 760, minWidth: 700, minHeight: 420,
                     title: "De Lá Pra Cá — revisão das mídias" });
  revisao.on("closed", () => { revisao = null; });
  revisao.loadURL(`${ORIGEM}/painel?nle=resolve&vista=revisao`);
  return { janela: true };
});

ipcMain.handle("dlpc:fechar", async (event) => {
  if (!doPainel(event)) return false;
  const w = BrowserWindow.fromWebContents(event.sender);
  if (w && w === revisao) { w.close(); return true; }
  return false;
});

function abrir() {
  const w = janela({ width: 440, height: 780, minWidth: 360, minHeight: 480 });
  w.on("closed", () => app.quit());

  w.loadFile(path.join(__dirname, "carregando.html"));
  esperarServico(() => w.loadFile(path.join(__dirname, "carregando.html"),
                                  { query: { estado: "fora", app: caminhoDoApp() ? "1" : "" } }))
    .then(() => { log("motor no ar"); w.loadURL(PAINEL); });
}

log(`casca aberta · electron ${process.versions.electron} · chrome ${process.versions.chrome}`);
app.on("ready", abrir);
app.on("window-all-closed", () => app.quit());
process.on("uncaughtException", (e) => log(`ERRO ${e && e.stack || e}`));

module.exports = { drtConfiavel, imagemConfiavel, pastaCache, pastaLogs };
