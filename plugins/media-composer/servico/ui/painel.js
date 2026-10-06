"use strict";

const ABAS = ["deLaPraCa", "daquiPraLa", "ajustes"];
const CHAVE_ABA = "delapraca.mc.aba";
const CHAVE_PASTAS = "delapraca.mc.pastas";

const VISTA_REVISAO =
  new URLSearchParams(location.search).get("vista") === "revisao";
const VISTA_CONFIG =
  new URLSearchParams(location.search).get("vista") === "configuracoes";

const NLE = ["mc", "resolve"].includes(new URLSearchParams(location.search).get("nle"))
  ? new URLSearchParams(location.search).get("nle") : "mc";
document.body.dataset.nle = NLE;

(function detectarFlexGap() {
  const d = document.createElement("div");
  d.style.cssText = "display:flex;flex-direction:column;row-gap:1px;position:absolute;visibility:hidden";
  d.appendChild(document.createElement("div"));
  d.appendChild(document.createElement("div"));
  document.body.appendChild(d);
  const tem = d.scrollHeight === 1;
  d.parentNode.removeChild(d);
  if (!tem) document.documentElement.classList.add("sem-flex-gap");
})();

let arrastada = null;

const el = (id) => document.getElementById(id);
const nomeDe = (c) => String(c).split("/").pop();

async function api(caminho, opcoes) {
  const r = await fetch(caminho, opcoes);
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) {
    const erro = new Error(corpo.erro || `HTTP ${r.status}`);
    erro.status = r.status;
    erro.corpo = corpo;
    throw erro;
  }
  return corpo;
}

const post = (caminho, dados) => api(caminho, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(dados || {}),
});

function escapar(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function dizer(texto, ok) {
  const m = el("msg");
  m.textContent = texto || "";
  m.className = "msg" + (texto ? (ok ? " ok" : " er") : "");
}

function lerAba() {
  try {
    const salva = localStorage.getItem(CHAVE_ABA);
    if (NLE !== "mc" && salva === "daquiPraLa") return "deLaPraCa";
    if (ABAS.includes(salva) && salva !== "ajustes") return salva;
  } catch (_) {  }
  return "deLaPraCa";
}

function irPara(aba) {
  const nome = (a) => a[0].toUpperCase() + a.slice(1);
  for (const a of ABAS) {
    el("tab" + nome(a)).classList.toggle("on", a === aba);
    el("pane" + nome(a)).classList.toggle("on", a === aba);
  }
  try { localStorage.setItem(CHAVE_ABA, aba); } catch (_) {  }
  if (aba === "ajustes") carregarAjustes();
}

const PASSOS = ["passoOrigem", "passoRevisao", "passoProgresso", "passoFeito"];
function mostrarPasso(id) {
  for (const p of PASSOS) el(p).hidden = p !== id;
}

let pastas = [];

function lerPastas() {
  try { localStorage.removeItem(CHAVE_PASTAS); } catch (_) {  }
  return [];
}

function gravarPastas() {  }

function renderPastas() {
  el("listaPastas").innerHTML = pastas.map((p, i) =>
    `<div class="pasta"><span class="caminho"><bdi>${escapar(p)}</bdi></span>` +
    `<button class="x" data-i="${i}" title="remover">✕</button></div>`).join("");
  if (NLE === "resolve") reanalisarSePreciso();
}

function addPasta(caminhos) {
  const lista = (Array.isArray(caminhos) ? caminhos : [caminhos])
    .map((c) => String(c || "").trim())
    .filter(Boolean);
  if (!lista.length) return;
  let novas = 0;
  for (const v of lista) if (!pastas.includes(v)) { pastas.push(v); novas++; }
  gravarPastas(); renderPastas();
  dizer(novas ? "" : "essa pasta já está na lista", novas ? true : false);
}

async function escolherNativo(rota, aoReceber) {
  try {
    const r = await post(rota);
    if (r.aviso) dizer(r.aviso, false);
    if (Array.isArray(r.caminhos) && r.caminhos.length) aoReceber(r.caminhos);
    else if (r.caminho) aoReceber(r.caminho);
  } catch (e) { dizer(e.message, false); }
}

let modoOrigem = "mc";
let rcCarga = null;
let rcSeqEscolhida = null;

function irParaModo(modo) {
  if (!modo || modo === modoOrigem) return;
  modoOrigem = modo;
  for (const x of el("modoOrigem").children) x.classList.toggle("on", x.dataset.modo === modo);
  el("origemMC").hidden = modo !== "mc";
  el("origemAAF").hidden = modo !== "aaf";
  el("origemAtualizar").hidden = modo !== "atualizar";
  if (NLE === "resolve") pintarCarga();
  if (!rcOcupado && !atComparando) el("rcAtualizar").hidden = true;
  if (modo === "atualizar") abaAtualizar();
  el("campoPreset").hidden = modo !== "mc";
  if (modo === "mc") verificarPreset();
  if (modo === "aaf") procurarNovos(true);
}

const MIME_MC_ASSETS = "text/x.avid.mc-api-asset-list+json";
const MIME_PLUGIN_ASSETS = "text/x.avid.panel-sdk-plugin-asset-list+json";
const PREFIXO_AVID = "text/x.avid.";

let profundidadeArrasto = 0;

let tiposNoEnter = [];

let itensNoEnter = [];

function itensDoArrasto(dt) {
  const itens = dt && dt.items ? Array.from(dt.items) : [];
  return itens.map((i) => ({ kind: i.kind, type: i.type }));
}

const ZONA = "[data-solta]";

function zonasQueAceitam(especie) {
  if (!especie) return [];
  const todas = Array.from(document.querySelectorAll(ZONA)).filter((z) => z.offsetParent);
  if (especie === "caminho") return todas.filter((z) => z.dataset.solta !== "exportTimeline");
  return todas.filter((z) => z.dataset.solta === "timeline" || z.dataset.solta === "exportTimeline");
}

function rotuloDaZona(zona, especie) {
  if (zona.dataset.solta === "midia") return "solte para adicionar as pastas";
  if (zona.dataset.solta === "exportTimeline") return "solte para exportar esta timeline";
  if (zona.dataset.solta === "referencia") return "solte para usar como vídeo de referência";
  return especie === "timeline" ? "solte para usar esta timeline"
    : NLE === "resolve" ? "solte para carregar a timeline" : "solte um AAF para importar";
}

function realcar(especie, alvo) {
  const aceitantes = new Set(zonasQueAceitam(especie));
  const sob = alvo && alvo.closest ? alvo.closest(ZONA) : null;
  for (const z of document.querySelectorAll(ZONA)) {
    const aceita = aceitantes.has(z);
    z.classList.toggle("pode-soltar", aceita);
    const sobOCursor = aceita && z === sob;
    if (sobOCursor) z.dataset.rotulo = rotuloDaZona(z, especie);
    z.classList.toggle("solta-aqui", !!sobOCursor);
  }
}

function limparRealce() {
  for (const z of document.querySelectorAll(ZONA)) {
    z.classList.remove("solta-aqui", "pode-soltar");
  }
}

function dizerNoArrasto(texto, ok) {
  dizer(texto, ok);
  try { el("msg").scrollIntoView({ block: "nearest" }); } catch (_) {  }
}

function efeitoCompativel(dt) {
  const permitido = (dt && dt.effectAllowed) || "all";
  if (permitido === "none") return null;
  if (permitido === "all" || permitido === "uninitialized") return "copy";
  const p = permitido.toLowerCase();
  if (p.includes("copy")) return "copy";
  if (p.includes("link")) return "link";
  if (p.includes("move")) return "move";
  return "copy";
}

function tiposDoArrasto(dt) {
  return Array.from(dt && dt.types ? dt.types : []);
}

function temNossoMime(dt) {
  const t = tiposDoArrasto(dt);
  return t.includes(MIME_MC_ASSETS) || t.includes(MIME_PLUGIN_ASSETS);
}

function capturarArrasto(dt) {
  const linhas = [];
  const tipos = tiposDoArrasto(dt);
  const paraOServico = {
    efeito: (dt && dt.effectAllowed) || "",
    tipos,
    tipos_no_enter: tiposNoEnter,
    itens_no_enter: itensNoEnter,
    itens_no_drop: itensDoArrasto(dt),
    arquivos: (dt && dt.files && dt.files.length) || 0,
    dados: {},
  };
  linhas.push("efeito permitido: " + ((dt && dt.effectAllowed) || "—"));
  linhas.push("arquivos: " + ((dt && dt.files && dt.files.length) || 0));
  linhas.push("tipos no drop (" + tipos.length + "):");
  for (const t of tipos) linhas.push("  " + t);
  linhas.push("tipos no enter (" + tiposNoEnter.length + "):");
  for (const t of tiposNoEnter) linhas.push("  " + t);

  for (const t of tipos) {
    if (t.indexOf(PREFIXO_AVID) !== 0 && t !== "text/uri-list" && t !== "text/plain") continue;
    let bruto = "";
    try { bruto = dt.getData(t) || ""; } catch (e) { bruto = "(getData falhou: " + e.message + ")"; }
    paraOServico.dados[t] = bruto;
    linhas.push("");
    linhas.push("── " + t + " (" + bruto.length + " chars)");
    try { linhas.push(JSON.stringify(JSON.parse(bruto), null, 1)); }
    catch (_) { linhas.push(bruto || "(vazio)"); }
  }

  const arquivos = dt && dt.files ? Array.from(dt.files) : [];
  paraOServico.arquivos_detalhe = arquivos.map((f) => ({ nome: f.name, tamanho: f.size }));
  for (const f of arquivos) {
    linhas.push("");
    linhas.push(`── arquivo: ${f.name} (${f.size} bytes)`);
  }

  const alvo = el("ajArrasto");
  if (alvo) alvo.textContent = new Date().toLocaleTimeString() + "\n" + linhas.join("\n");
  post("/diag/arrasto", paraOServico).catch(() => {});

  tiposNoEnter = [];
  itensNoEnter = [];
}

function lerPayload(dt) {
  const tipos = tiposDoArrasto(dt);
  const mime = tipos.includes(MIME_MC_ASSETS) ? MIME_MC_ASSETS : MIME_PLUGIN_ASSETS;
  const cru = dt.getData(mime);
  if (!cru) return [];
  const dados = JSON.parse(cru);
  return Array.isArray(dados) ? dados : [dados];
}

const ESPECIES = {
  sequence: ["timeline", "timelines"],
  masterclip: ["master clip", "master clips"],
  subclip: ["subclip", "subclips"],
  group: ["group clip", "group clips"],
  "": ["efeito", "efeitos"],
};

function descreverEspecies(itens) {
  const conta = new Map();
  for (const i of itens) {
    const t = (i && i.type) || "";
    conta.set(t, (conta.get(t) || 0) + 1);
  }
  const partes = [...conta].map(([tipo, n]) => {
    const rotulo = ESPECIES[tipo] || [tipo || "item desconhecido", tipo || "itens desconhecidos"];
    return `${n} ${rotulo[n > 1 ? 1 : 0]}`;
  });
  if (partes.length === 1) return partes[0];
  return partes.slice(0, -1).join(", ") + " e " + partes[partes.length - 1];
}

function uriParaCaminho(uri) {
  const cru = String(uri || "").trim();
  if (!/^file:\/\//i.test(cru)) return "";
  let resto = cru.slice("file://".length);
  let host = "";
  const barra = resto.indexOf("/");
  if (barra === -1) return "";
  if (barra > 0) { host = resto.slice(0, barra); resto = resto.slice(barra); }

  let caminho;
  try { caminho = decodeURIComponent(resto); } catch (_) { caminho = resto; }

  if (/^\/[A-Za-z]:/.test(caminho)) return caminho.slice(1).replace(/\//g, "\\");
  if (host && host.toLowerCase() !== "localhost") {
    return ("\\\\" + host + caminho).replace(/\//g, "\\");
  }
  return caminho;
}

function _lerTipo(dt, tipo) {
  try { return (dt && dt.getData(tipo)) || ""; } catch (_) { return ""; }
}

function _urisDe(bruto) {
  return String(bruto || "").split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && l[0] !== "#")
    .map(uriParaCaminho)
    .filter(Boolean);
}

function caminhosDoArrasto(dt) {
  const vistos = new Set();
  const saida = [];
  for (const c of _urisDe(_lerTipo(dt, "text/uri-list"))
                    .concat(_urisDe(_lerTipo(dt, "text/plain")))) {
    if (!vistos.has(c)) { vistos.add(c); saida.push(c); }
  }
  if (!saida.length && dt && dt.files) {
    for (const f of Array.from(dt.files)) {
      let c = "";
      try { c = f.path || ""; } catch (_) {  }
      if (!c && ponte() && typeof ponte().caminhoDoArquivo === "function") {
        try { c = ponte().caminhoDoArquivo(f) || ""; } catch (_) {  }
      }
      if (c && !vistos.has(c)) { vistos.add(c); saida.push(c); }
    }
  }
  return saida;
}


function especieDoArrasto(dt) {
  if (temNossoMime(dt)) return "timeline";
  const t = tiposDoArrasto(dt);
  if (t.includes("text/uri-list") || t.includes("Files")) return "caminho";
  return "";
}

const EXT_LEIO_HOJE = /\.aaf$/i;
const EXT_TIMELINE_FUTURA = /\.(xml|fcpxml|drt|otio|edl|prproj)$/i;

function eArquivoDeTimeline(c) {
  return EXT_LEIO_HOJE.test(c) || EXT_TIMELINE_FUTURA.test(c);
}

async function aoSoltar(ev) {
  ev.preventDefault();
  profundidadeArrasto = 0;
  limparRealce();

  const dt = ev.dataTransfer;
  try { capturarArrasto(dt); } catch (_) {  }

  const especie = especieDoArrasto(dt);
  if (!especie) {
    const tipos = tiposDoArrasto(dt);
    dizerNoArrasto(tipos.length
      ? `não reconheci o que foi solto (chegou como: ${tipos.join(", ")})`
      : "não reconheci o que foi solto — o arrasto chegou sem dados", false);
    return;
  }

  const zona = (ev.target && ev.target.closest) ? ev.target.closest(ZONA) : null;
  if (!zona) {
    if (!zonasQueAceitam(especie).length) {
      dizerNoArrasto("esta etapa não recebe arrasto — volte ao começo para trocar a "
        + "timeline ou as pastas", false);
      return;
    }
    dizerNoArrasto(especie === "timeline"
      ? "solte a timeline dentro do bloco “1 · a timeline”"
      : "solte no bloco “1” (arquivo de timeline) ou no “2” (pastas de mídia)", false);
    return;
  }

  if (especie === "timeline") {
    if (zona.dataset.solta === "exportTimeline") return soltarTimelineParaExportar(dt);
    if (zona.dataset.solta !== "timeline") {
      dizerNoArrasto("isso é uma timeline — solte no bloco “1 · a timeline”", false);
      return;
    }
    return soltarTimelineDaBin(dt);
  }

  if (zona.dataset.solta === "exportTimeline") {
    dizerNoArrasto("aqui entra a timeline arrastada da bin do Media Composer", false);
    return;
  }

  const caminhos = caminhosDoArrasto(dt);
  if (!caminhos.length) {
    dizerNoArrasto(NLE === "resolve"
      ? "esse arrasto não trouxe o caminho do arquivo — clique na área para escolher"
      : "esse arrasto não trouxe caminho, só o nome — use “Escolher…”", false);
    return;
  }
  if (zona.dataset.solta === "referencia") return soltarReferencia(caminhos);
  return zona.dataset.solta === "timeline"
    ? soltarArquivoDeTimeline(caminhos)
    : soltarPastasDeMidia(caminhos);
}

function soltarPastasDeMidia(caminhos) {
  const midias = caminhos.filter((c) => !eArquivoDeTimeline(c));
  if (!midias.length) {
    dizerNoArrasto("isso é uma timeline — solte no bloco “1 · a timeline”", false);
    return;
  }
  const antes = pastas.length;
  addPasta(midias);
  const novas = pastas.length - antes;
  const sobrou = caminhos.length - midias.length;
  dizerNoArrasto(
    (novas ? (novas === 1 ? "pasta adicionada" : `${novas} pastas adicionadas`)
           : "essa pasta já está na lista")
    + (sobrou ? ` — ${sobrou} timeline(s) ficaram de fora, elas vão no bloco 1` : ""),
    !!novas);
}

const EXT_RESOLVE_ABRE = /\.(aaf|json|avb|prproj)$/i;

function soltarArquivoDeTimeline(caminhos) {
  if (NLE === "resolve") {
    const abre = caminhos.filter((c) => EXT_RESOLVE_ABRE.test(c) || eArquivoDeTimeline(c));
    if (abre.length !== 1) {
      dizerNoArrasto(abre.length ? "uma timeline por vez — você soltou " + abre.length
        : "isso não parece uma timeline — pastas de mídia vão no bloco 2", false);
      return;
    }
    irParaModo("aaf");
    abrirArquivo(abre[0]);
    return;
  }
  const arquivos = caminhos.filter(eArquivoDeTimeline);
  if (!arquivos.length) {
    dizerNoArrasto("isso não parece uma timeline — pastas de mídia vão no bloco 2", false);
    return;
  }
  if (arquivos.length > 1) {
    dizerNoArrasto("uma timeline por vez — você soltou " + arquivos.length, false);
    return;
  }
  const arquivo = arquivos[0];
  if (!EXT_LEIO_HOJE.test(arquivo)) {
    const ext = (arquivo.match(/\.[^.\\/]+$/) || [""])[0];
    dizerNoArrasto(`ainda não leio ${ext} — hoje a importação é de AAF`, false);
    return;
  }
  irParaModo("aaf");
  el("campoAaf").value = arquivo;
  dizerNoArrasto("timeline escolhida: " + (arquivo.split(/[\\/]/).pop() || arquivo), true);
}

async function soltarTimelineDaBin(dt) {
  let itens;
  try {
    itens = lerPayload(dt);
  } catch (e) {
    dizerNoArrasto("o Media Composer mandou algo que não consegui ler: " + e.message, false);
    return;
  }

  const sequences = itens.filter((i) => i && i.type === "sequence" && i.id);
  if (!sequences.length) {
    dizerNoArrasto(itens.length
      ? `por enquanto só timeline — você soltou ${descreverEspecies(itens)}`
      : "o arrasto veio vazio", false);
    return;
  }

  if (sequences.length > 1) {
    await adotarVarias(sequences);
    return;
  }
  await adotarSequence(sequences[0], itens.length - 1);
}

async function adotarVarias(sequences) {
  dizerNoArrasto(`lendo ${sequences.length} timelines…`, true);

  const TETO_NOMES = 8;
  const nomes = await Promise.all(sequences.map(async (s, i) => {
    if (i >= TETO_NOMES) return "";
    try { return (await post("/mc/mob", { mob_id: s.id })).nome || ""; }
    catch (_) { return ""; }
  }));

  guardarArrastada(sequences.map((s, i) => ({
    mob_id: s.id, nome: nomes[i] || `timeline ${i + 1}`,
  })));
  aplicarArrastada();
  dizerNoArrasto(sequences.length > 1
    ? `${sequences.length} timelines recebidas — vale a última`
    : "timeline recebida", true);
}

async function adotarSequence(item, ignorados) {
  irParaModo("mc");

  dizerNoArrasto("lendo a timeline arrastada…", true);

  let nome = "";
  try {
    const r = await post("/mc/mob", { mob_id: item.id });
    nome = r.nome || "";
  } catch (e) {
    if (e.status === 404) { dizerNoArrasto(e.message, false); return; }
    dizerNoArrasto("não consegui ler o nome dela (" + e.message + ") — sigo pelo MobID", false);
  }

  const rotulo = nome || "timeline arrastada";

  guardarArrastada([{ mob_id: item.id, nome: rotulo }]);
  aplicarArrastada();

  dizerNoArrasto(`timeline “${rotulo}” recebida${ignorados > 0
      ? ` — ignorei mais ${ignorados} item(ns) soltos junto` : ""}`, true);
}

function guardarArrastada(itens, escolhido) {
  arrastada = { itens, escolhido: escolhido || (itens[0] && itens[0].mob_id) || "" };
}

function aplicarArrastada() {
  const alvo = el("timelineEscolhida");
  const item = arrastada && arrastada.itens.find((x) => x.mob_id === arrastada.escolhido);
  if (!item) { alvo.hidden = true; return; }
  alvo.textContent = item.nome;
  alvo.hidden = false;
  el("dicaSeq").innerHTML = "Arraste outra para trocar.";
}

async function verificarPreset() {
  const alvo = el("estadoPreset");
  const botao = el("btnInstalarPreset");
  const aviso = el("avisoPreset");
  try {
    const d = await api("/mc/preset");
    const texto = {
      ok: `“${d.preset}” instalado e correto`,
      ausente: `“${d.preset}” não está instalado`,
      divergente: `“${d.preset}” está diferente do esperado`,
      sem_settings: "não achei as settings do Media Composer",
    }[d.situacao] || d.situacao;

    alvo.textContent = texto;
    alvo.className = "estado-preset " + (d.situacao === "ok" ? "ok" : "pendente");
    botao.hidden = d.situacao === "ok" || d.situacao === "sem_settings";
    presetOk = d.situacao === "ok";

    if (d.situacao !== "ok" && d.mc_aberto) {
      aviso.textContent = "feche o Media Composer para instalar — ele reescreve as " +
                          "settings ao sair, e desfaria o que fizermos agora.";
      aviso.hidden = false;
    } else if (d.motivo && d.situacao !== "ok") {
      aviso.textContent = d.motivo; aviso.hidden = false;
    } else {
      aviso.hidden = true;
    }
  } catch (_) {
    alvo.textContent = "—"; botao.hidden = true; aviso.hidden = true;
  }
}
let presetOk = true;
let rotaEmUso = "";

function aplicarVisibilidadeDoPreset() {
  const bloco = el("campoPreset");
  if (!bloco) return;
  const dispensavel = rotaEmUso === "avb";
  bloco.hidden = dispensavel;
  const nota = el("notaRotaAvb");
  if (nota) nota.hidden = !dispensavel;
}

async function preparar() {
  if (!pastas.length) {
    dizer("aponte ao menos uma pasta de mídia", false);
    return;
  }
  const corpo = {
    pastas,
    achatar_subclipes: el("optSubclipes").checked,
    achatar_group_clips: el("optGrupos").checked,
  };
  if (modoOrigem === "aaf") {
    corpo.aaf = el("campoAaf").value.trim();
    if (!corpo.aaf) { dizer("informe o caminho do AAF", false); return; }
  } else {
    const item = arrastada && arrastada.itens.find((x) => x.mob_id === arrastada.escolhido);
    if (!item) { dizer("arraste a timeline da bin para cá", false); return; }
    corpo.sequence_mob_id = item.mob_id;
    corpo.sequence = item.nome || "";
  }
  corpo.so_importados = el("optSoImportados").checked;
  corpo.so_offline = el("optSoOffline").checked;
  dizer("");
  try {
    const r = await post("/trabalho/preparar", corpo);
    rotaEmUso = r && r.rota ? r.rota : "aaf";
    aplicarVisibilidadeDoPreset();
    mostrarPasso("passoProgresso");
    acompanhar();
  } catch (e) { dizer(e.message, false); }
}

let filtro = "revisao";

function pill(texto, classe) {
  return `<span class="pill ${classe || ""}">${escapar(texto)}</span>`;
}

async function carregarRevisao() {
  const estado = await api("/trabalho");
  const t = estado.trabalho || {};
  el("placar").innerHTML =
    pill(`${t.resolvidos || 0} de ${t.total || 0} prontos`, "ok") +
    ((t.sugeridos) ? pill(`${t.sugeridos} sugerido${t.sugeridos > 1 ? "s" : ""}`, "warn") : "") +
    ((t.ambiguos) ? pill(`${t.ambiguos} ambíguo${t.ambiguos > 1 ? "s" : ""}`, "warn") : "") +
    ((t.nao_resolvidos) ? pill(`${t.nao_resolvidos} sem mídia`, "err") : "") +
    pill(`${t.segmentos_resolvidos || 0}/${t.segmentos_total || 0} clipes na timeline`) +
    ((t.do_banco) ? pill(`${t.do_banco} do banco`, "ok") : "");

  const r = await api("/trabalho/masters" + (filtro === "revisao" ? "?revisao=1" : ""));
  const itens = r.masters || [];
  el("listaMasters").innerHTML = itens.length
    ? itens.map(desenharMaster).join("")
    : '<div class="vazio">nada a decidir aqui</div>';

  if (!pastas.length && (t.nao_resolvidos || 0) > 0) {
    dizer(`${t.nao_resolvidos} master(s) não estão no banco. Aponte a pasta de mídia no `
          + "bloco 2 e prepare de novo — ou siga assim e relinke só o resto.", false);
  }

  const decididos = t.decididos || 0;
  const b = el("btnAplicar");
  if (NLE === "resolve") {
    b.disabled = false;
    b.textContent = decididos ? `Importar no Resolve · ${decididos} nos originais`
                              : "Importar no Resolve · sem originais";
  } else {
    b.disabled = !decididos;
    b.textContent = decididos
      ? `Relinkar ${decididos} no Media Composer`
      : "Nada decidido ainda";
  }

  const divergencias =
    (t.sugeridos || 0) + (t.ambiguos || 0) + (t.nao_resolvidos || 0);
  const prontos = t.resolvidos || 0;

  el("resumoRevisao").textContent = divergencias
    ? `${divergencias} ${divergencias > 1 ? "divergências encontradas" : "divergência encontrada"}.`
    : "Nenhuma divergência: todos os clipes casaram.";

  el("notaRevisao").textContent = divergencias
    ? (NLE === "resolve"
      ? `A revisão é opcional — você pode importar com os ${prontos} que casaram com segurança; o resto entra como hoje.`
      : `A revisão é opcional — você pode relinkar só os ${prontos} que casaram com segurança.`)
    : "A revisão é opcional, mas é onde um casamento errado apareceria antes do relink.";

  el("conviteRevisao").hidden = VISTA_REVISAO;
}

function desenharMaster(m) {
  const classe = m.arquivo ? "resolvido" : (m.sugestao ? "sugerido" : "aberto");
  const tipo = m.e_audio ? "som" : "img";
  let alvo = "";
  if (m.arquivo) alvo = `<div class="item-alvo">→ ${escapar(nomeDe(m.arquivo))}</div>`;
  else if (m.sugestao) alvo = `<div class="item-alvo">? ${escapar(nomeDe(m.sugestao))}</div>`;

  let acoes = "";
  if (m.sugestao && !m.arquivo) {
    acoes = `<button data-aceitar="${escapar(m.mob_id)}" data-arquivo="${escapar(m.sugestao)}">usar esta</button>`;
  } else if (m.arquivo && m.metodo === "manual") {
    acoes = `<button class="ghost" data-limpar="${escapar(m.mob_id)}">desfazer</button>`;
  }
  if (m.candidatos && m.candidatos.length > 1) {
    acoes += m.candidatos.slice(0, 4).map((c) =>
      `<button data-aceitar="${escapar(m.mob_id)}" data-arquivo="${escapar(c.path)}">` +
      `${escapar(nomeDe(c.path))}</button>`).join("");
  }

  return `<div class="item ${classe}">
    <div class="item-topo">
      <span class="item-nome">${escapar(m.nome)}</span>
      <span class="item-seg">${tipo} · ${m.segmentos}×</span>
    </div>
    ${alvo}
    ${m.motivo && !m.arquivo ? `<div class="item-motivo">${escapar(m.motivo)}</div>` : ""}
    ${acoes ? `<div class="item-acoes">${acoes}</div>` : ""}
  </div>`;
}

let fonte = null;

function acompanhar() {
  if (fonte) fonte.close();
  fonte = new EventSource("/progresso");
  fonte.onmessage = (ev) => {
    let e; try { e = JSON.parse(ev.data); } catch (_) { return; }
    pintarProgresso(e);
  };
  fonte.onerror = () => { fonte.close(); fonte = null; sincronizar(); };
}

function avisarSobreABin(nome) {
  const alvo = el("avisoTrabalho");
  if (!alvo) return;
  if (!nome) { alvo.hidden = true; return; }
  alvo.innerHTML = "Enquanto isto roda, <strong>não abra nem edite a bin "
    + `<bdi>${escapar(nome)}</bdi></strong> — ela ainda está sendo montada.`;
  alvo.hidden = false;
}

function pintarProgresso(e) {
  const pct = e.total ? Math.round(100 * e.feito / e.total) : 0;
  el("progEtapa").textContent = e.etapa || e.fase || "trabalhando";
  el("progBarra").style.width = pct + "%";
  el("progPct").textContent = pct + "%";
  el("progDetalhe").innerHTML = `<bdi>${escapar(e.detalhe || "")}</bdi>`;
  el("progTempo").textContent = e.decorrido ? relogio(e.decorrido) : "";

  if (e.fase !== "aplicando") avisarSobreABin("");

  if (e.fase === "pronto") { fecharFonte(); mostrarPasso("passoRevisao"); carregarRevisao(); }
  else if (e.fase === "feito") { fecharFonte(); mostrarFeito(e.resultado || {}); }
  else if (e.fase === "erro") { fecharFonte(); mostrarPasso("passoOrigem"); dizer(e.erro, false); }
  else if (e.fase === "ocioso" && e.etapa === "cancelado") {
    fecharFonte(); mostrarPasso("passoOrigem"); dizer("cancelado", false);
  }
}

function fecharFonte() { if (fonte) { fonte.close(); fonte = null; } }

function relogio(s) {
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`;
}

function mostrarFeito(r) {
  const abriu = r.aberta_no_mc !== false;
  el("resumoFinal").innerHTML =
    (abriu
      ? `<div><strong>${escapar(r.bin || "")}</strong> aberta no Media Composer</div>`
      : `<div><strong>${escapar(r.bin || "")}</strong> foi gravada, mas não abriu ` +
        `sozinha — abra por File ▸ Open Bin:</div>` +
        `<div class="caminho">${escapar(r.avb || "")}</div>`) +
    `<div class="dica">${r.masters_online || 0} de ${r.masters_pedidos || 0} mídias online · ` +
    `${r.segmentos_cobertos || 0} clipes na timeline` +
    (r.subclipes_achatados ? ` · ${r.subclipes_achatados} subclipes desfeitos` : "") +
    (r.group_clips_achatados ? ` · ${r.group_clips_achatados} group clips desfeitos` : "") +
    `</div>` +
    ((r.falhas && r.falhas.length)
      ? `<div class="item-motivo">${r.falhas.length} arquivo(s) não puderam ser abertos</div>`
      : "");
  mostrarPasso("passoFeito");
  dizer("");
  carregarBanco();
}

async function sincronizar() {
  try {
    const e = await api("/trabalho");
    if (e.ocupada) { mostrarPasso("passoProgresso"); pintarProgresso(e); acompanhar(); }
    else if (e.fase === "pronto") { mostrarPasso("passoRevisao"); carregarRevisao(); }
    else if (e.fase === "feito") mostrarFeito(e.resultado || {});
    else mostrarPasso("passoOrigem");
  } catch (_) { mostrarPasso("passoOrigem"); }
}

const CHAVE_REGISTROS = "dlpc.registrosPendentes";

function registrosPendentes() {
  try { return JSON.parse(localStorage.getItem(CHAVE_REGISTROS) || "[]") || []; } catch (_) { return []; }
}

function guardarPendentes(lista) {
  try {
    if (lista.length) localStorage.setItem(CHAVE_REGISTROS, JSON.stringify(lista));
    else localStorage.removeItem(CHAVE_REGISTROS);
  } catch (_) {  }
}

async function registrar(rota, corpo) {
  try { return await post(rota, corpo); } catch (e) {
    if (e && e.status) {
      console.warn("[registro] recusado:", e.message);
      return { recusado: e.message };
    }
    console.warn("[registro] ficou pendente:", e && e.message);
    guardarPendentes(registrosPendentes().concat([{ rota, corpo }]));
    return null;
  }
}

const registrarTrazido = (corpo) => registrar("/receber/trazido", corpo);

async function reenviarPendentes() {
  const lista = registrosPendentes();
  while (lista.length) {
    const item = lista[0] && lista[0].rota ? lista[0] : { rota: "/receber/trazido", corpo: lista[0] };
    try { await post(item.rota, item.corpo); } catch (e) {
      if (!(e && e.status)) break;
      console.warn("[registro] pendente recusado, descartado:", e.message);
    }
    lista.shift();
  }
  guardarPendentes(lista);
}

const avisoRecusado = (motivo) => `A timeline entrou, mas esta máquina não anotou de onde ela veio (${motivo}). `
  + "Na próxima atualização dela, escolha a versão antiga com Escolher….";

const AVISO_PENDENTE = "O serviço do De Lá Pra Cá caiu antes de anotar esta timeline. O registro "
  + "fica guardado e é gravado quando ele voltar (o painel religa em alguns segundos). Até lá, "
  + "não atualize de novo.";

let servicoOk = false;

let falhasSeguidas = 0;
function bloquear(sim) { document.body.classList.toggle("sem-motor", !!sim); }

function pintarForaDoAr() {
  if (ponte() && typeof ponte().esperarMotor === "function") {
    ponte().esperarMotor().catch(() => {  });
  }
  bloquear(true);
  el("prTitulo").textContent = "serviço não encontrado";
  el("prTexto").textContent = "Abra o aplicativo De Lá Pra Cá. Esta tela conecta sozinha quando ele abrir"
    + " — não precisa fechar nada.";
  el("prItens").innerHTML = "";
  el("prAbrirApp").hidden = !(ponte() && typeof ponte().abrirApp === "function");
}

function pintarProntidao(pr) {
  if ((pr && pr.ok) || VISTA_CONFIG) { bloquear(false); return; }
  bloquear(true);
  el("prTitulo").textContent = "o motor do De Lá Pra Cá não está pronto";
  el("prTexto").textContent = "Antes de trabalhar, isto precisa estar resolvido — senão a timeline "
    + "sairia errada (ou offline) só no fim.";
  el("prItens").innerHTML = ((pr && pr.itens) || []).map((i) =>
    `<div class="pr-item ${i.ok ? "ok" : "err"}"><span class="marca-pr">${i.ok ? "✓" : "✗"}</span>` +
    `<span>${escapar(i.nome)}${i.ok ? "" : `: ${escapar(i.erro || "falhou")}` +
      (i.gesto ? `<span class="gesto">${escapar(i.gesto)}</span>` : "")}</span></div>`).join("");
  const semApp = ((pr && pr.itens) || []).some((i) => i.id === "aplicativo" && !i.ok);
  el("prAbrirApp").hidden = !(semApp && ponte() && typeof ponte().abrirApp === "function");
}

async function conferirProntidao(forcar) {
  try {
    const pr = await api("/prontidao" + (forcar ? "?forcar=1" : ""));
    if (!(pr && pr.ok) && rcOcupado) return;
    pintarProntidao(pr);
  }
  catch (e) { console.warn("[prontidão]", e && e.message); }
}
el("prConferir").onclick = () => (servicoOk ? conferirProntidao(true) : baterNoServico());
el("prAbrirApp").onclick = async () => {
  try {
    const r = await ponte().abrirApp();
    el("prTexto").textContent = r && r.ok ? "Abrindo o aplicativo… esta tela conecta sozinha em seguida."
      : (r && r.erro) || "não consegui abrir o aplicativo: abra pelo Finder/Iniciar";
  } catch (e) { el("prTexto").textContent = e.message; }
};
bloquear(true);

async function baterNoServico() {
  try {
    const s = await api(NLE === "mc" ? "/health" : `/receber/estado?nle=${NLE}`);
    const voltou = !servicoOk;
    servicoOk = true;
    el("versao").textContent = "v" + s.versao;
    el("conn").className = "conn ok";
    el("connTexto").textContent = "conectado";
    falhasSeguidas = 0;
    await conferirProntidao(false);
    if (voltou && NLE !== "mc") { await reenviarPendentes(); procurarNovos(true); }
    if (voltou && NLE === "mc") {
      sincronizar();
      carregarBanco();
      if (modoOrigem === "mc") { verificarPreset(); }
    }
  } catch (e) {
    console.warn("[serviço] a batida falhou:", e && e.message);
    servicoOk = false;
    el("conn").className = "conn err";
    el("connTexto").textContent = "serviço fora do ar";
    falhasSeguidas += 1;
    if (falhasSeguidas >= 2 && !rcOcupado) pintarForaDoAr();
  }
}

function carregarAjustes() {
  el("ajServico").textContent = location.origin;
  carregarCanais();
  carregarCache();
  if (NLE === "resolve") { consultarResolve(); carregarCodigoDeCores(); }
}

async function consultarResolve() {
  const alvo = el("dvEstado");
  if (!ponte()) { alvo.textContent = "aberto fora do Resolve"; return; }
  try {
    const r = await ponte().projeto();
    alvo.innerHTML = [`DaVinci Resolve ${escapar(r.versao || "")}`.trim(),
      r.projeto ? `Projeto: ${escapar(r.projeto)}` : "sem projeto aberto"].join("<br>");
  } catch (e) { alvo.textContent = "—"; dizer(e.message, false); }
}

async function consultarMC() {
  const alvo = el("mcEstado");
  alvo.textContent = "consultando…";
  try {
    const r = await api("/mc/info");
    if (!r.aberto) { alvo.textContent = "Media Composer não respondeu. Ele está aberto?"; return; }
    const app = r.app || {}, proj = r.projeto || {};
    alvo.innerHTML = [
      `${escapar(app.name || "Media Composer")} ${escapar(app.version || "")}`.trim(),
      proj.name ? `Projeto: ${escapar(proj.name)}` : "",
      proj.path ? `<span class="caminho"><bdi>${escapar(proj.path)}</bdi></span>` : "",
    ].filter(Boolean).join("<br>");
  } catch (e) { alvo.textContent = "—"; dizer(e.message, false); }
}

el("tabDeLaPraCa").onclick = () => irPara("deLaPraCa");
el("tabDaquiPraLa").onclick = () => irPara("daquiPraLa");
el("tabAjustes").onclick = () => irPara("ajustes");
el("btnConsultar").onclick = consultarMC;

el("modoOrigem").onclick = (ev) => {
  const b = ev.target.closest("button[data-modo]");
  if (b && !b.disabled) irParaModo(b.dataset.modo);
};

el("btnInstalarPreset").onclick = async () => {
  dizer("");
  try {
    const r = await post("/mc/preset/instalar");
    dizer(`preset ${r.acao} — cópia de segurança guardada`, true);
    verificarPreset();
  } catch (e) { dizer(e.message, false); }
};
el("btnEscolherPasta").onclick = () => escolherNativo("/escolher/pasta", addPasta);

let bancoConectado = false;

function pintarBanco(e) {
  const ligado = !!(e && e.conectado);
  bancoConectado = ligado;
  el("bancoDesligado").hidden = ligado;
  el("bancoLigado").hidden = !ligado;
  el("pastasOpcional").hidden = true;
  if (ligado) {
    el("bancoNome").textContent = e.nome || "";
    const n = e.casamentos || 0;
    el("bancoResumo").textContent = n
      ? `${n} mídia(s) já confirmada(s) — os próximos relinks usam isto`
      : "ainda vazio — o primeiro relink o alimenta";
  }
  const aviso = el("bancoAviso");
  aviso.textContent = (e && e.aviso) || "";
  aviso.hidden = !aviso.textContent;
}

async function acaoDoBanco(rota) {
  dizer("");
  try {
    pintarBanco(await post(rota));
  } catch (err) { dizer(err.message, false); }
}

async function carregarBanco() {
  try {
    pintarBanco(await api("/banco"));
  } catch (_) {
    pintarBanco({ conectado: false });
  }
}

el("btnCriarBanco").onclick = () => acaoDoBanco("/banco/criar");
el("btnConectarBanco").onclick = () => acaoDoBanco("/banco/conectar");
el("btnDesconectarBanco").onclick = () => acaoDoBanco("/banco/desconectar");

el("btnEscolherAaf").onclick = () =>
  escolherNativo("/escolher/timeline", (c) => { el("campoAaf").value = c; dizer(""); });
el("listaPastas").onclick = (ev) => {
  const b = ev.target.closest("button[data-i]");
  if (!b) return;
  pastas.splice(Number(b.dataset.i), 1);
  gravarPastas(); renderPastas();
};

el("btnPreparar").onclick = preparar;
el("btnVoltar").onclick = () => { mostrarPasso("passoOrigem"); dizer(""); };
el("btnNovo").onclick = () => { mostrarPasso("passoOrigem"); dizer(""); };
el("btnCancelar").onclick = () => post("/trabalho/cancelar").catch(() => {});

el("btnConcluirRevisao").onclick = async () => {
  const b = el("btnConcluirRevisao");
  b.disabled = true;
  b.textContent = "fechando…";
  try {
    if (NLE === "resolve" && ponte() && ponte().fecharJanela) await ponte().fecharJanela();
    else await post("/app/revisao/fechar");
  } catch (e) {
    b.disabled = false;
    b.textContent = "Concluir revisão";
    dizer("não consegui fechar a janela — pode fechá-la no X.", "err");
  }
};

el("btnJanelaRevisao").onclick = async () => {
  const b = el("btnJanelaRevisao");
  const aviso = el("avisoJanelaRevisao");
  b.disabled = true;
  try {
    const r = NLE === "resolve" && ponte() && ponte().abrirRevisao
      ? await ponte().abrirRevisao() : await post("/app/revisao");
    if (r.janela) {
      aviso.hidden = true;
    } else {
      aviso.textContent = "— o aplicativo não está aberto; a revisão continua aqui.";
      aviso.hidden = false;
    }
  } catch (e) {
    aviso.textContent = "— não consegui chamar o aplicativo; a revisão continua aqui.";
    aviso.hidden = false;
  } finally {
    b.disabled = false;
  }
};

el("filtro").onclick = (ev) => {
  const b = ev.target.closest("button[data-filtro]");
  if (!b) return;
  filtro = b.dataset.filtro;
  for (const x of el("filtro").children) x.classList.toggle("on", x === b);
  carregarRevisao();
};

el("listaMasters").onclick = async (ev) => {
  const b = ev.target.closest("button[data-aceitar], button[data-limpar]");
  if (!b) return;
  try {
    if (b.dataset.limpar) await post("/trabalho/escolher", { mob_id: b.dataset.limpar });
    else await post("/trabalho/escolher", { mob_id: b.dataset.aceitar, arquivo: b.dataset.arquivo });
    carregarRevisao();
  } catch (e) { dizer(e.message, false); }
};

el("btnAplicar").onclick = async () => {
  dizer("");
  if (NLE === "resolve") { await importarComOriginais(); return; }
  try {
    const r = await post("/trabalho/aplicar", { nome_bin: el("nomeBin").value.trim() });
    avisarSobreABin(r && r.bin);
    mostrarPasso("passoProgresso");
    acompanhar();
  } catch (e) { dizer(e.message, false); }
};

for (const [id, campo] of [["optSubclipes", "achatar_subclipes"],
                           ["optGrupos", "achatar_group_clips"]]) {
  el(id).onchange = () => post("/trabalho/opcoes", { [campo]: el(id).checked }).catch(() => {});
}

el("autor").onclick = () => post("/link/abrir", { qual: "autor" }).catch(() => {});

document.addEventListener("dragenter", (ev) => {
  ev.preventDefault();
  const t = tiposDoArrasto(ev.dataTransfer);
  if (t.length) tiposNoEnter = t;
  const itens = itensDoArrasto(ev.dataTransfer);
  if (itens.length) itensNoEnter = itens;
  profundidadeArrasto++;
  realcar(especieDoArrasto(ev.dataTransfer), ev.target);
}, true);

document.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  const especie = especieDoArrasto(ev.dataTransfer);
  if (!especie) return;
  const efeito = efeitoCompativel(ev.dataTransfer);
  if (efeito) ev.dataTransfer.dropEffect = efeito;
  realcar(especie, ev.target);
}, true);

document.addEventListener("dragleave", (ev) => {
  profundidadeArrasto = Math.max(0, profundidadeArrasto - 1);
  if (profundidadeArrasto === 0) limparRealce();
}, true);

document.addEventListener("dragend", limparRealce, true);

document.addEventListener("drop", (ev) => {
  aoSoltar(ev).catch((e) => dizerNoArrasto(e.message, false));
}, true);

const CHAVE_FORMATO_EXPORT = "delapraca.daquiprala.formato";
const EXT_EXPORT = { carta_aberta: ".json", drt: ".drt" };

let exPronto = false;
let exRodando = false;
let exNomeOk = false;
let exFonte = null;
let exTimer = null;
let exCamposDesenhados = false;
let mlCanais = [];

function lerFormatoExport() {
  try {
    const salvo = localStorage.getItem(CHAVE_FORMATO_EXPORT);
    const op = Array.from(el("exFormato").options).find((o) => o.value === salvo);
    if (op && !op.disabled) return salvo;
  } catch (_) {  }
  return "carta_aberta";
}

function gravarFormatoExport(v) {
  try { localStorage.setItem(CHAVE_FORMATO_EXPORT, v); } catch (_) {  }
}

function atualizarBotaoExport() {
  const pode = exPronto && exNomeOk && !exRodando;
  el("exBtnExportar").disabled = !pode;
  for (const b of el("mlEnviarTimeline").querySelectorAll("button[data-canal]")) {
    b.disabled = !(pode && b.dataset.conectado === "1");
  }
  const f = el("exFormato").value;
  el("exNotaComentario").hidden = f !== "drt";
}

async function soltarTimelineParaExportar(dt) {
  let itens;
  try {
    itens = lerPayload(dt);
  } catch (e) {
    dizerNoArrasto("o Media Composer mandou algo que não consegui ler: " + e.message, false);
    return;
  }
  const seqs = itens.filter((i) => i && i.type === "sequence" && i.id);
  if (!seqs.length) {
    dizerNoArrasto(itens.length
      ? `aqui entra timeline — você soltou ${descreverEspecies(itens)}`
      : "o arrasto veio vazio", false);
    return;
  }
  if (seqs.length > 1) {
    dizerNoArrasto(`uma timeline por vez — você soltou ${seqs.length}`, false);
    return;
  }
  if (exRodando) {
    dizerNoArrasto("espere a exportação em andamento terminar", false);
    return;
  }
  const item = seqs[0];
  dizerNoArrasto("lendo a timeline arrastada…", true);

  let nome = "";
  try {
    nome = (await post("/mc/mob", { mob_id: item.id })).nome || "";
  } catch (e) {
    if (e.status === 404) { dizerNoArrasto(e.message, false); return; }
  }

  exPronto = false;
  el("exSequencia").textContent = nome || "timeline arrastada";
  el("exSequencia").hidden = false;
  el("exDica").hidden = true;
  el("exResumo").hidden = true;
  el("exFeito").hidden = true;
  el("exNome").value = lerPadrao("timeline") || nome.replace(/%/g, "%%");
  previaDoNome();
  atualizarBotaoExport();
  try {
    await post("/export/preparar", { mob_id: item.id, sequence: nome });
    acompanharExport();
  } catch (e) {
    dizerNoArrasto(e.message, false);
  }
}

function acompanharExport() {
  if (exFonte) exFonte.close();
  exFonte = new EventSource("/export/progresso");
  exFonte.onmessage = (ev) => {
    let e; try { e = JSON.parse(ev.data); } catch (_) { return; }
    pintarExport(e);
  };
  exFonte.onerror = () => { if (exFonte) exFonte.close(); exFonte = null; exRodando = false;
                            atualizarBotaoExport(); };
}

function fecharFonteExport() { if (exFonte) { exFonte.close(); exFonte = null; } }

function pintarExport(e) {
  exRodando = e.fase === "preparando" || e.fase === "gerando";
  el("exProgresso").hidden = !exRodando;
  el("exBtnCancelar").hidden = !exRodando;
  if (exRodando) {
    const pct = e.total ? Math.round(100 * e.feito / e.total) : 0;
    el("exEtapa").textContent = e.etapa || e.fase;
    el("exBarra").style.width = (e.total ? pct : 100) + "%";
    el("exPct").textContent = e.total ? `${e.feito}/${e.total}` : "";
    el("exDetalhe").innerHTML = `<bdi>${escapar(e.detalhe || "")}</bdi>`;
  }
  const leu = !!(e.resultado && e.resultado.sequencia);
  if (e.fase === "pronto") {
    fecharFonteExport(); exPronto = true; pintarResumoExport(e.resultado);
    dizer("timeline lida — exporte ou envie pelo Magic Link", true);
  } else if (e.fase === "feito") {
    fecharFonteExport(); exPronto = true; pintarResumoExport(e.resultado);
    pintarFeitoExport(e.resultado);
  } else if (e.fase === "erro") {
    fecharFonteExport(); exPronto = leu; dizer(e.erro, false);
  } else if (e.fase === "ocioso" && e.etapa === "cancelado") {
    fecharFonteExport(); exPronto = leu; dizer("cancelado", false);
  }
  atualizarBotaoExport();
}

function pintarResumoExport(r) {
  const alvo = el("exResumo");
  if (!r || !r.sequencia) { alvo.hidden = true; return; }
  let h = '<div class="placar">'
    + pill(`${r.eventos} eventos`, "")
    + pill(`V${r.trilhas_video} · A${r.trilhas_audio}`, "")
    + pill(`${r.masters_com_arquivo} online`, "ok")
    + (r.masters_offline_total ? pill(`${r.masters_offline_total} offline`, "") : "")
    + pill(`${r.fps} fps · ${r.tc_inicial}`, "")
    + "</div>";
  if (r.masters_offline_total) {
    h += `<details class="avancado"><summary>${r.masters_offline_total} chegam offline ao `
      + "Resolve, para religar aos originais</summary><div class=\"avancado-corpo\">"
      + r.masters_offline.map((m) => `<div class="dica"><strong>${escapar(m.arquivo || m.reel || m.nome)}</strong>`
        + (m.tem_tc ? "" : " — <em>sem TC de origem</em>") + "</div>").join("")
      + (r.masters_offline_total > r.masters_offline.length
        ? `<div class="dica">… e mais ${r.masters_offline_total - r.masters_offline.length}</div>`
        : "")
      + "</div></details>";
    if (r.pastas_sem_indice) {
      h += `<p class="aviso">${r.pastas_sem_indice} pasta(s) de mídia do Avid sem índice `
        + "(storage de rede?) — clipes que estejam só nelas saem offline.</p>";
    }
    if (r.offline_sem_tc) {
      h += `<p class="aviso">${r.offline_sem_tc} original(is) sem TC de origem na bin — `
        + "o colorista terá de religar esses pelo nome.</p>";
    }
  }
  for (const a of (r.avisos || [])) {
    h += `<p class="aviso">${escapar(a.texto)}${a.vezes > 1 ? ` (×${a.vezes})` : ""}</p>`;
  }
  alvo.innerHTML = h;
  alvo.hidden = false;
}

function pintarFeitoExport(r) {
  const s = r.saida || {};
  let h = `<div class="bloco"><p class="bloco-titulo">pronto</p>`
    + `<div class="caminho"><bdi>${escapar(r.arquivo || "")}</bdi></div>`;
  if (r.formato === "drt") {
    h += `<p class="dica">${escapar(s.clips_generated)} clipes de vídeo, `
      + `${escapar(s.audio_clips)} de áudio, ${escapar(s.pool_clips)} arquivos no media pool</p>`;
    if (s.clipes_offline) {
      h += `<p class="dica">${escapar(s.clipes_offline)} eventos apontam para originais que `
        + "não estão nesta máquina — no Resolve, selecione-os e use <em>Relink</em> na "
        + "pasta dos originais.</p>";
    }
    if (s.comentario_ignorado) {
      h += '<p class="aviso">o comentário não virou marcador — no DRT isso ainda não é '
        + "escrito. Exporte também a Carta Aberta para guardá-lo.</p>";
    }
  } else {
    h += `<p class="dica">${escapar(s.segmentos)} eventos, ${escapar(s.com_arquivo)} online, `
      + `${escapar(s.offline || 0)} offline</p>`;
  }
  el("exFeito").innerHTML = h + "</div>";
  el("exFeito").hidden = false;
  dizer("exportado", true);
}

function previaDoNome() {
  clearTimeout(exTimer);
  exTimer = setTimeout(async () => {
    const alvo = el("exPrevia");
    try {
      const r = await post("/export/nome", { padrao: el("exNome").value });
      if (!exCamposDesenhados && r.campos) desenharCampos(r.campos);
      exNomeOk = !!r.nome;
      alvo.className = "dica " + (r.nome ? "previa-ok" : "previa-er");
      alvo.textContent = r.nome
        ? "→ " + r.nome + (EXT_EXPORT[el("exFormato").value] || "")
        : (r.erro || "");
    } catch (e) {
      exNomeOk = false;
      alvo.className = "dica previa-er";
      alvo.textContent = e.message;
    }
    atualizarBotaoExport();
  }, 180);
}

function desenharCampos(campos) {
  el("exCampos").innerHTML = campos.map((c) =>
    `<button type="button" data-campo="${escapar(c.campo)}" title="${escapar(c.valor)}">`
    + `${escapar(c.rotulo)}</button>`).join("");
  exCamposDesenhados = true;
}

el("exCampos").onclick = (ev) => {
  const b = ev.target.closest("button[data-campo]");
  if (!b) return;
  const campo = el("exNome");
  const tag = `%{${b.dataset.campo}}`;
  const ini = campo.selectionStart ?? campo.value.length;
  const fim = campo.selectionEnd ?? campo.value.length;
  campo.value = campo.value.slice(0, ini) + tag + campo.value.slice(fim);
  campo.focus();
  campo.setSelectionRange(ini + tag.length, ini + tag.length);
  previaDoNome();
};

el("exNome").oninput = () => { gravarPadrao("timeline", el("exNome").value); previaDoNome(); };
el("exFormato").onchange = () => {
  gravarFormatoExport(el("exFormato").value);
  previaDoNome();
  atualizarBotaoExport();
};
el("exBtnCancelar").onclick = () => post("/export/cancelar").catch(() => {});
el("exBtnExportar").onclick = async () => {
  dizer("");
  el("exFeito").hidden = true;
  let pasta = "";
  try {
    pasta = (await post("/escolher/destino")).caminho || "";
  } catch (e) { dizer(e.message, false); return; }
  if (!pasta) return;
  try {
    await post("/export/gerar", {
      formato: el("exFormato").value,
      pasta,
      padrao: el("exNome").value,
      comentario: el("exComentario").value,
    });
    exRodando = true;
    atualizarBotaoExport();
    acompanharExport();
  } catch (e) { dizer(e.message, false); }
};

el("exFormato").value = lerFormatoExport();
previaDoNome();

const CHAVE_MODO_EXPORT = "delapraca.daquiprala.modo";
let insItens = [];
let insOcupado = false;

function trilhaDe(t) { return t < 1000 ? `V${t}` : `A${t - 999}`; }

function desenharBotoesCanal(alvo, dica) {
  el(alvo).innerHTML = mlCanais.map((c) =>
    `<button class="primary" data-canal="${escapar(c.setor)}" data-conectado="${c.conectado ? 1 : 0}"
       title="${escapar(c.conectado ? c.pasta : "canal não conectado — veja em Ajustes")}" disabled>`
    + `Enviar ${escapar(c.para)}</button>`).join("");
  const faltam = mlCanais.filter((c) => !c.conectado).map((c) => c.rotulo);
  el(dica).textContent = faltam.length === mlCanais.length
    ? "Nenhum canal conectado. Conecte em ⚙ Ajustes."
    : faltam.length ? `${faltam.join(" e ")}: não conectado (⚙ Ajustes).` : "";
}

function pintarCanaisEnvio(r) {
  mlCanais = (r && r.canais) || [];
  desenharBotoesCanal("mlEnviarTimeline", "mlDicaTimeline");
  desenharBotoesCanal("mlEnviarInsert", "mlDicaInsert");
}

function pintarMagic(r) {
  if (!r) return;
  if (r.canais) pintarCanaisEnvio(r);
  const c = r.coleta || {};
  insItens = c.itens || [];
  el("insLista").innerHTML = insItens.map((i) =>
    `<div class="item"><div class="item-topo">`
    + `<span class="item-seg">${escapar(trilhaDe(i.track))}</span>`
    + `<span class="item-nome">${escapar(i.clip_name || "?")}</span>`
    + `<span class="item-seg">${escapar(i.timeline_tc_in)}–${escapar(i.timeline_tc_out)}</span>`
    + `<button class="x" title="tirar da lista" data-chave="${escapar(JSON.stringify(
        [i.track, i.timeline_tc_in, i.mob_id]))}">✕</button>`
    + `</div></div>`).join("");
  el("insVazia").hidden = insItens.length > 0;
  el("insBtnLimpar").hidden = insItens.length === 0;
  el("insResumo").textContent = insItens.length
    ? `${insItens.length} clipe(s) · ${(c.trilhas || []).join(" ")}`
      + (c.sequencia && c.sequencia.nome ? ` · ${c.sequencia.nome}` : "")
    : "";
  const p = c.pendente;
  el("insConfirmar").hidden = !p;
  if (p) {
    el("insConfirmarTexto").textContent = p.troca
      ? "A lista é de outra sequência. Trocar pela seleção desta?"
      : `A seleção pega ${p.trilhas.length} trilhas (${p.trilhas.join(", ")}) — `
        + `${p.itens.length} clipe(s). É isso?`;
  }
  previaDoInsert();
  atualizarBotoesMagic();
  atualizarBotaoExport();
}

function atualizarBotoesMagic() {
  el("insBtnColetar").disabled = insOcupado;
  const pode = !insOcupado && insItens.length > 0 && el("insConfirmar").hidden && insNomeOk;
  for (const b of el("mlEnviarInsert").querySelectorAll("button[data-canal]")) {
    b.disabled = !(pode && b.dataset.conectado === "1");
  }
  el("insBtnExportar").disabled = !pode;
}

const CHAVE_PADRAO = { timeline: "delapraca.nome.timeline", insert: "delapraca.nome.insert" };
let insNomeOk = true;
let insCamposDesenhados = false;
let insTimer = null;

const temCampo = (p) => /%(\{?[a-z])/i.test(String(p || "").replace(/%%/g, ""));

function lerPadrao(modo) {
  try { const p = localStorage.getItem(CHAVE_PADRAO[modo]); return temCampo(p) ? p : ""; }
  catch (_) { return ""; }
}
function gravarPadrao(modo, p) {
  try { if (temCampo(p)) localStorage.setItem(CHAVE_PADRAO[modo], p); } catch (_) {  }
}

function inserirNoCampo(campo, texto, noComeco) {
  if (noComeco) {
    if (!campo.value.startsWith(texto)) campo.value = texto + campo.value;
  } else {
    const ini = campo.selectionStart == null ? campo.value.length : campo.selectionStart;
    const fim = campo.selectionEnd == null ? campo.value.length : campo.selectionEnd;
    campo.value = campo.value.slice(0, ini) + texto + campo.value.slice(fim);
  }
  campo.focus();
  campo.dispatchEvent(new Event("input"));
}

function canalDaPrevia() {
  const c = mlCanais.find((x) => x.conectado) || mlCanais[0];
  return c ? c.setor : "";
}

function previaDoInsert() {
  clearTimeout(insTimer);
  insTimer = setTimeout(async () => {
    const alvo = el("insPrevia");
    try {
      const r = await post("/export/nome", { padrao: el("insNome").value, modo: "insert",
                                              tipo: canalDaPrevia() });
      if (!insCamposDesenhados && r.campos) {
        el("insCampos").innerHTML = r.campos.map((c) =>
          `<button type="button" data-campo="${escapar(c.campo)}" title="${escapar(c.valor)}">`
          + `${escapar(c.rotulo)}</button>`).join("");
        insCamposDesenhados = true;
      }
      insNomeOk = !!r.nome;
      alvo.className = "dica " + (r.nome ? "previa-ok" : "previa-er");
      const muda = temCampo(el("insNome").value) && /%\{?tipo/.test(el("insNome").value)
        && mlCanais.length > 1 ? " (o %tipo muda com o botão)" : "";
      alvo.textContent = r.nome ? "→ " + r.nome + muda : (r.erro || "");
    } catch (e) {
      insNomeOk = false;
      alvo.className = "dica previa-er";
      alvo.textContent = e.message;
    }
    atualizarBotoesMagic();
  }, 180);
}

async function carregarMagic() {
  try { pintarMagic(await api("/magic")); } catch (_) {  }
}

function mostrarModo(modo) {
  for (const b of el("exModo").children) b.classList.toggle("on", b.dataset.modo === modo);
  el("exModoInsert").hidden = modo !== "insert";
  el("exModoTimeline").hidden = modo !== "timeline";
  try { localStorage.setItem(CHAVE_MODO_EXPORT, modo); } catch (_) {  }
}

el("exModo").onclick = (ev) => {
  const b = ev.target.closest("button[data-modo]");
  if (b) mostrarModo(b.dataset.modo);
};

el("insNome").oninput = () => { gravarPadrao("insert", el("insNome").value); previaDoInsert(); };
el("insCampos").onclick = (ev) => {
  const b = ev.target.closest("button[data-campo]");
  if (b) inserirNoCampo(el("insNome"), `%{${b.dataset.campo}}`, false);
};
el("insPrefixos").onclick = (ev) => {
  const b = ev.target.closest("button[data-prefixo]");
  if (b) inserirNoCampo(el("insNome"), b.dataset.prefixo, true);
};
if (lerPadrao("insert")) el("insNome").value = lerPadrao("insert");
if (NLE === "mc") previaDoInsert();

function dizerDecisao(d) {
  if (!d) return;
  dizer(d.texto || "", d.status === "adicionado");
  for (const a of (d.avisos || []).slice(0, 3)) console.warn("[magic link]", a);
}

el("insBtnColetar").onclick = async () => {
  insOcupado = true;
  atualizarBotoesMagic();
  el("insBtnColetar").textContent = "coletando…";
  dizer("coletando a seleção — a timeline pisca e volta", true);
  try {
    const r = await post("/insert/coletar");
    pintarMagic(r);
    dizerDecisao(r.decisao);
  } catch (e) {
    dizer(e.message, false);
  } finally {
    insOcupado = false;
    el("insBtnColetar").textContent = "Coletar seleção";
    atualizarBotoesMagic();
  }
};

async function responderConfirmacao(sim) {
  try {
    const r = await post("/insert/confirmar", { ok: sim });
    pintarMagic(r);
    dizerDecisao(r.decisao);
  } catch (e) { dizer(e.message, false); }
}
el("insBtnOk").onclick = () => responderConfirmacao(true);
el("insBtnCancelarColeta").onclick = () => responderConfirmacao(false);

el("insLista").onclick = async (ev) => {
  const b = ev.target.closest("button[data-chave]");
  if (!b) return;
  try { pintarMagic(await post("/insert/remover", { chave: JSON.parse(b.dataset.chave) })); }
  catch (e) { dizer(e.message, false); }
};
el("insBtnLimpar").onclick = async () => {
  try { pintarMagic(await post("/insert/limpar")); dizer("lista limpa", true); }
  catch (e) { dizer(e.message, false); }
};

async function enviarInsert(canal) {
  const c = mlCanais.find((x) => x.setor === canal);
  insOcupado = true;
  atualizarBotoesMagic();
  dizer(canal ? "enviando o insert…" : "", true);
  el("insFeito").hidden = true;
  const corpo = { padrao: el("insNome").value, comentario: el("insComentario").value,
                  so_video: el("insSoVideo").checked };
  let r;
  try {
    r = canal ? await post("/magic/enviar", { ...corpo, tipo: "insert", canal })
              : await post("/insert/exportar", corpo);
  } catch (e) {
    dizer(e.message, false);
    insOcupado = false;
    atualizarBotoesMagic();
    return;
  }
  insOcupado = false;
  atualizarBotoesMagic();
  if (r.cancelado) { dizer(""); return; }
  let h = `<div class="bloco"><p class="bloco-titulo">${canal
      ? `enviado ${escapar(c ? c.para : canal)}` : "exportado"}</p>`
    + `<div class="caminho"><bdi>${escapar(r.arquivo || "")}</bdi></div>`
    + `<p class="dica">${escapar(r.clipes)} clipe(s).`
    + (canal ? ` Do outro lado: Procurar novos → Importar.` : "") + "</p>";
  if ((r.mudaram || []).length) {
    h += `<p class="aviso">${r.mudaram.length} clipe(s) da lista mudaram na timeline e `
      + "ficaram de fora — colete de novo: "
      + r.mudaram.map((i) => escapar(`${trilhaDe(i.track)} ${i.timeline_tc_in}`)).join(", ")
      + "</p>";
  }
  el("insFeito").innerHTML = h + "</div>";
  el("insFeito").hidden = false;
  dizer(canal ? "insert enviado" : "insert exportado", true);
}

el("mlEnviarInsert").onclick = (ev) => {
  const b = ev.target.closest("button[data-canal]");
  if (b && !b.disabled) enviarInsert(b.dataset.canal);
};
el("insBtnExportar").onclick = () => enviarInsert(null);

el("mlEnviarTimeline").onclick = async (ev) => {
  const b = ev.target.closest("button[data-canal]");
  if (!b || b.disabled) return;
  dizer("");
  el("exFeito").hidden = true;
  try {
    await post("/magic/enviar", { tipo: "timeline", canal: b.dataset.canal,
                                  padrao: el("exNome").value, comentario: el("exComentario").value });
    exRodando = true;
    atualizarBotaoExport();
    acompanharExport();
  } catch (e) { dizer(e.message, false); }
};

try { mostrarModo(localStorage.getItem(CHAVE_MODO_EXPORT) === "insert" ? "insert" : "timeline"); }
catch (_) { mostrarModo("timeline"); }
if (NLE === "mc") carregarMagic();

function ponte() { return window.delapraca || null; }

const ETAPAS_RC = {
  lendo:      { nome: "Lendo a Carta",                     peso: 2 },
  indexando:  { nome: "Procurando a mídia desta máquina",  peso: 13 },
  casando:    { nome: "Casando os clipes",                 peso: 3 },
  midia:      { nome: "Lendo os arquivos de mídia",        peso: 40 },
  montando:   { nome: "Montando a timeline",               peso: 31 },
  trazendo:   { nome: "Trazendo a mídia para o Resolve",   peso: 1 },
  importando: { nome: `Importando no ${NLE === "resolve" ? "Resolve" : "programa"}`, peso: 10 },
  fusion:     { nome: "Criando as composições do Fusion",  peso: 2 },
  compostos:  { nome: "Juntando os nests em Compound Clip", peso: 1 },
  conferindo: { nome: "Conferindo pela referência",       peso: 0 },
};
const RESOLVE_OCUPADO = new Set(["trazendo", "importando", "fusion", "compostos", "conferindo"]);
const ORDEM_RC = Object.keys(ETAPAS_RC);
const TRAZ_AQUI = NLE === "resolve";

let rcOcupado = false;
let atComparando = false;
let rcCaixa = { canais: [] };
let rcInicio = 0;

function rotuloOrigem(envio) {
  const app = String(envio.aplicativo || "").toLowerCase();
  if (app.includes("resolve")) return "RESOLVE";
  if (app.includes("premiere")) return "PREMIERE";
  return "AVID";
}

function quando(iso) {
  const m = /^(\d+)-(\d+)-(\d+)T(\d+):(\d+)/.exec(String(iso || ""));
  return m ? `${m[3]}/${m[2]} ${m[4]}:${m[5]}` : "";
}

function rotuloTipo(tipo) {
  const t = String(tipo || "");
  if (t.indexOf("insert") !== 0) return "Timeline";
  return t.indexOf("insert_") === 0 ? `Insert ${t.slice(7).toUpperCase()}` : "Insert";
}

const CHAVE_FILTRO_ML = "delapraca.magic.filtro";
const CHAVE_ORDEM_ML = "delapraca.magic.ordem";
let mlFiltro = "novos";
let mlOrdem = { campo: "enviado_em", desc: true };
try {
  mlFiltro = localStorage.getItem(CHAVE_FILTRO_ML) || "novos";
  const o = JSON.parse(localStorage.getItem(CHAVE_ORDEM_ML) || "null");
  if (o && typeof o.campo === "string") mlOrdem = { campo: o.campo, desc: !!o.desc };
} catch (_) {  }

function rotuloLado(lado) {
  return (rcCaixa.lados || {})[lado] || lado;
}

function opcoesDoFiltro() {
  const conectados = (rcCaixa.canais || []).filter((c) => c.conectado);
  const op = [["novos", "Novos (ainda não importados)"]];
  if (conectados.length) op.push(["de:edicao", `Da ${rotuloLado("edicao")}`]);
  for (const c of conectados) op.push([`de:${c.setor}`, `${c.de.charAt(0).toUpperCase()}${c.de.slice(1)}`]);
  op.push(["importados", "Já importados"], ["tudo", "Tudo"]);
  return op;
}

function passaNoFiltro(e) {
  if (mlFiltro === "tudo") return true;
  if (mlFiltro === "importados") return !!e.importado;
  if (mlFiltro.indexOf("de:") === 0) return !e.importado && e.de === mlFiltro.slice(3);
  return !e.importado;
}

const CHAVE_LARGURAS_ML = "delapraca.magic.larguras";
const LARGURA_MINIMA = 44;

function conteudoDe(e) {
  return String(e.tipo || "").indexOf("insert") === 0
    ? `${e.clipes_insert || 0} clipe(s)` : `${e.segmentos != null ? e.segmentos : "?"} eventos`;
}
function remetenteDe(e) {
  const rem = e.remetente || {};
  return [rem.maquina, rem.usuario ? `(${rem.usuario})` : ""].filter(Boolean).join(" ");
}

const COLUNAS = [
  { id: "enviado_em", rotulo: "Data",       largura: 96,  texto: (e) => quando(e.enviado_em) },
  { id: "nome",       rotulo: "Nome",       largura: 220, texto: (e) => e.nome || e.sequencia || "?" },
  { id: "tipo",       rotulo: "Tipo",       largura: 100, texto: (e) => rotuloTipo(e.tipo) },
  { id: "conteudo",   rotulo: "Conteúdo",   largura: 92,  texto: conteudoDe },
  { id: "sequencia",  rotulo: "Sequência",  largura: 160, texto: (e) => e.sequencia || "" },
  { id: "remetente",  rotulo: "Remetente",  largura: 170, texto: (e) => [rotuloOrigem(e), remetenteDe(e)].filter(Boolean).join(" · ") },
  { id: "comentario", rotulo: "Comentário", largura: 220, texto: (e) => e.comentario || "" },
  { id: "trazido_em", rotulo: "Importado",  largura: 96,  texto: (e) => quando(e.trazido_em) },
];
const LARGURA_ACAO = 96;

let mlLarguras = {};
try { mlLarguras = JSON.parse(localStorage.getItem(CHAVE_LARGURAS_ML) || "{}") || {}; }
catch (_) { mlLarguras = {}; }
const larguraDe = (c) => Math.max(LARGURA_MINIMA, Number(mlLarguras[c.id]) || c.largura);

function aplicarLarguras() {
  el("mlColunas").innerHTML = COLUNAS.map((c) => `<col style="width:${larguraDe(c)}px">`).join("")
    + `<col style="width:${LARGURA_ACAO}px">`;
  el("mlTabela").style.width = `${COLUNAS.reduce((s, c) => s + larguraDe(c), 0) + LARGURA_ACAO}px`;
}

function desenharCabecalho() {
  el("mlCabecalho").innerHTML = COLUNAS.map((c) =>
    `<th data-ordem="${c.id}" class="col-${c.id}" title="clique para ordenar">${escapar(c.rotulo)}`
    + `<span class="alca" data-alca="${c.id}" title="arraste para mudar a largura"></span></th>`).join("")
    + `<th class="col-acao"><span class="oculto">ação</span></th>`;
  aplicarLarguras();
}

function valorDeOrdem(e, campo) {
  if (campo === "enviado_em" || campo === "trazido_em") return String(e[campo] || "");
  if (campo === "conteudo") {
    const n = String(e.tipo || "").indexOf("insert") === 0 ? e.clipes_insert : e.segmentos;
    return Number(n) || 0;
  }
  const c = COLUNAS.find((x) => x.id === campo);
  return String(c ? c.texto(e) : e[campo] || "").toLowerCase();
}

function desenharLinha(e) {
  const eInsert = String(e.tipo || "").indexOf("insert") === 0;
  const celulas = COLUNAS.map((c) => {
    const t = c.texto(e);
    if (c.id === "tipo") {
      return `<td class="col-tipo"><span class="origem ${eInsert ? (e.canal === "vfx" ? "vfx" : "cor") : "avid"}">`
        + `${escapar(t)}</span></td>`;
    }
    return `<td class="col-${c.id}" title="${escapar(t)}">${escapar(t)}</td>`;
  }).join("");
  const botao = TRAZ_AQUI
    ? `<button class="sm ${e.importado ? "" : "primary"}" data-carregar="${escapar(e.id)}"
         ${rcOcupado ? "disabled" : ""}>Carregar</button>`
    : `<button class="sm" disabled title="importar no Media Composer chega numa próxima etapa">`
      + `Importar <span class="em-breve">em breve</span></button>`;
  const carregado = rcCarga && rcCarga.envio && rcCarga.envio.id === e.id;
  return `<tr class="${[e.importado ? "importado" : "", carregado ? "carregado" : ""].join(" ").trim()}">`
    + `${celulas}<td class="col-acao">${botao}</td></tr>`;
}

function pintarTabela() {
  const filtro = el("mlFiltro");
  const ops = opcoesDoFiltro();
  if (!ops.some(([v]) => v === mlFiltro)) mlFiltro = "novos";
  filtro.innerHTML = ops.map(([v, r]) =>
    `<option value="${escapar(v)}"${v === mlFiltro ? " selected" : ""}>${escapar(r)}</option>`).join("");

  for (const th of el("mlTabela").querySelectorAll("th[data-ordem]")) {
    const ativo = th.dataset.ordem === mlOrdem.campo;
    th.classList.toggle("ordem-ativa", ativo);
    th.setAttribute("aria-sort", ativo ? (mlOrdem.desc ? "descending" : "ascending") : "none");
    th.dataset.seta = ativo ? (mlOrdem.desc ? "▾" : "▴") : "";
  }

  const conectados = (rcCaixa.canais || []).filter((c) => c.conectado);
  const linhas = (rcCaixa.envios || []).filter(passaNoFiltro);
  const sinal = mlOrdem.desc ? -1 : 1;
  linhas.sort((a, b) => {
    const x = valorDeOrdem(a, mlOrdem.campo);
    const y = valorDeOrdem(b, mlOrdem.campo);
    return (x < y ? -1 : x > y ? 1 : 0) * sinal
      || String(b.enviado_em || "").localeCompare(String(a.enviado_em || ""));
  });
  el("mlCorpo").innerHTML = linhas.map(desenharLinha).join("")
    || `<tr class="vazia"><td colspan="${COLUNAS.length + 1}">${conectados.length ? "nada aqui" : ""}</td></tr>`;
  el("mlTabela").parentElement.hidden = !conectados.length;
  el("mlBtnProcurar").disabled = rcOcupado || !conectados.length;
  const novos = (rcCaixa.envios || []).filter((e) => !e.importado).length;
  el("mlSelo").hidden = !novos;
  el("mlSelo").textContent = novos === 1 ? "1 novo" : `${novos} novos`;

  const problemas = (rcCaixa.canais || []).filter((c) => c.pasta && !c.conectado).map((c) => c.rotulo);
  const erros = (rcCaixa.canais || []).filter((c) => c.erro).map((c) => `${c.rotulo}: ${c.erro}`);
  const ruins = (rcCaixa.invalidas || []).length;
  el("mlDicaReceber").innerHTML = !conectados.length
    ? `Nenhum canal conectado. <button class="sm" data-ir-ajustes="1">Conectar em Ajustes</button>`
    : [problemas.length ? `canal ${problemas.join(" e ")} inacessível agora` : "",
       ...erros, ruins ? `${ruins} arquivo(s) ilegível(is)` : "",
       rcCaixa.cortado ? "pasta com arquivos demais — mostro só os mais recentes" : ""]
      .filter(Boolean).map(escapar).join(" · ");
}

async function procurarNovos(silencioso) {
  if (rcOcupado) return;
  try {
    rcCaixa = await api(`/receber/caixa?nle=${NLE}`);
    pintarTabela();
    if (!silencioso) {
      const n = (rcCaixa.envios || []).filter((e) => !e.importado).length;
      dizer(`${n} envio(s) novo(s)`, true);
    }
  } catch (e) { if (!silencioso) dizer(e.message, false); }
}

el("mlFiltro").onchange = () => {
  mlFiltro = el("mlFiltro").value;
  try { localStorage.setItem(CHAVE_FILTRO_ML, mlFiltro); } catch (_) {  }
  pintarTabela();
};

let mlArrasto = null;
let mlAcabouDeArrastar = false;
el("mlTabela").querySelector("thead").addEventListener("mousedown", (ev) => {
  const alca = ev.target.closest("[data-alca]");
  if (!alca) return;
  ev.preventDefault();
  const c = COLUNAS.find((x) => x.id === alca.dataset.alca);
  mlArrasto = { c, x0: ev.clientX, w0: larguraDe(c) };
  document.body.classList.add("redimensionando");
});
document.addEventListener("mousemove", (ev) => {
  if (!mlArrasto) return;
  mlLarguras[mlArrasto.c.id] = Math.max(LARGURA_MINIMA, Math.round(mlArrasto.w0 + ev.clientX - mlArrasto.x0));
  aplicarLarguras();
});
document.addEventListener("mouseup", () => {
  if (!mlArrasto) return;
  mlArrasto = null;
  mlAcabouDeArrastar = true;
  setTimeout(() => { mlAcabouDeArrastar = false; }, 0);
  document.body.classList.remove("redimensionando");
  try { localStorage.setItem(CHAVE_LARGURAS_ML, JSON.stringify(mlLarguras)); } catch (_) {  }
});
el("mlTabela").querySelector("thead").addEventListener("dblclick", (ev) => {
  const alca = ev.target.closest("[data-alca]");
  if (!alca) return;
  delete mlLarguras[alca.dataset.alca];
  aplicarLarguras();
  try { localStorage.setItem(CHAVE_LARGURAS_ML, JSON.stringify(mlLarguras)); } catch (_) {  }
});
el("mlTabela").querySelector("thead").onclick = (ev) => {
  if (mlAcabouDeArrastar || ev.target.closest("[data-alca]")) return;
  const th = ev.target.closest("th[data-ordem]");
  if (!th) return;
  const campo = th.dataset.ordem;
  mlOrdem = campo === mlOrdem.campo ? { campo, desc: !mlOrdem.desc }
                                    : { campo, desc: campo === "enviado_em" || campo === "trazido_em" };
  try { localStorage.setItem(CHAVE_ORDEM_ML, JSON.stringify(mlOrdem)); } catch (_) {  }
  pintarTabela();
};
el("mlBtnProcurar").onclick = () => procurarNovos(false);
desenharCabecalho();

const UNIDADE_RC = { casando: "arquivos", midia: "arquivos", trazendo: "arquivos", montando: "itens",
                     fusion: "composições", compostos: "grupos", conferindo: "quadros" };

function pintarAndamento(p) {
  const etapa = ETAPAS_RC[p.etapa] ? p.etapa : "lendo";
  const i = ORDEM_RC.indexOf(etapa);
  const conta = !!p.total;
  el("rcBarraFundo").classList.toggle("indeterminada", !conta);
  el("rcBarra").style.width = conta ? `${Math.round(100 * Math.min(1, p.feitos / p.total))}%` : "";
  el("rcEtapa").textContent = ETAPAS_RC[etapa].nome;
  el("rcEtapaN").textContent = `etapa ${i + 1} de ${ORDEM_RC.length}`;
  el("rcConta").textContent = (conta ? `${p.feitos} de ${p.total} ${UNIDADE_RC[etapa] || ""}`.trim() : "") +
    (p.sufixo ? ` · ${p.sufixo}` : "");
  el("rcAlerta").hidden = !RESOLVE_OCUPADO.has(etapa);
  el("rcAlertaTexto").textContent = etapa === "conferindo"
    ? "O De Lá Pra Cá está usando o Resolve: troca de página, anda a agulha e liga e desliga trilhas. Mexer agora pode estragar a conferência."
    : "O De Lá Pra Cá está usando o Resolve. Mexer agora pode cair na timeline errada ou trocar a página no meio.";
  el("rcTempo").textContent = relogio(Math.round((Date.now() - rcInicio) / 1000));
  el("rcAtualOQue").textContent = p.detalhe || "";
  el("rcAtualNome").textContent = p.atual || "";
  el("rcAtualPasta").textContent = p.pasta ? `\u200e${p.pasta}` : "";
  el("rcAtualPasta").title = p.pasta || "";
}

function mostrarTrazendo(sim, titulo) {
  rcOcupado = sim;
  el("rcTrazendo").hidden = !sim;
  if (!sim) el("rcAlerta").hidden = true;
  el("origemAAF").hidden = sim || modoOrigem !== "aaf";
  el("rcBtnAbrir").disabled = sim;
  if (titulo) el("rcTrazendoTitulo").textContent = titulo;
  el("rcBtnCancelar").disabled = false;
  pintarCarga();
}

async function trazer(id) {
  const envio = (rcCaixa.envios || []).find((e) => e.id === id);
  if (!envio) return;
  await trazerPedido({ id }, envio.nome || envio.sequencia || "", id);
}

async function trazerPedido(pedido, titulo, idEnvio) {
  if (rcOcupado) return;
  if (!ponte()) { dizer("abra este painel pelo Resolve: Workspace › Workflow Integrations", false); return; }
  if (rcAvulso && rcAvulso.tipo === "prproj" && pedido.avulso === rcAvulso.chave && !pedido.analisado) {
    await analisarPrproj(pedido, titulo);
    return;
  }
  if (pastas.length && !pedido.originais && !pedido.analisado) {
    await casarOriginais(pedido, titulo, idEnvio);
    return;
  }
  dizer("");
  el("rcFeito").hidden = true;
  rcInicio = Date.now();
  mostrarTrazendo(true, `Importando ${titulo}`);
  pintarAndamento({ etapa: "lendo" });
  const relogioRc = setInterval(async () => {
    try { const p = await api("/receber/progresso"); if (p.ativo) pintarAndamento(p); } catch (_) {  }
  }, 500);

  let p;
  try {
    const marcada = (id) => !!(el(id) && el(id).checked);
    const { analisado, ...base } = pedido;
    const extra = rcAvulso && rcAvulso.tipo === "prproj"
      ? { audio: marcada("rcPrprojAudio"), referencia: rcRef.caminho || "", som_referencia: rcRef.som,
          compound: marcada("rcPrprojCompound"),
          ignorar: Array.from(rcIgnorados) } : {};
    p = await post("/receber/preparar", { ...base, ...extra, notas: marcada("rcNotas"),
                                          marcar: marcada("rcMarcar"), colorir: marcada("rcColorir") });
  } catch (e) {
    clearInterval(relogioRc);
    mostrarTrazendo(false);
    dizer(e.corpo && e.corpo.cancelado ? "cancelado — nada foi importado" : e.message, !!(e.corpo && e.corpo.cancelado));
    return;
  }
  clearInterval(relogioRc);
  el("rcBtnCancelar").disabled = true;
  let midias = null;
  if (ponte() && typeof ponte().midias === "function") {
    pintarAndamento({ etapa: "trazendo", feitos: 0, total: 0,
                      detalhe: `importando ${p.midias || 0} arquivos no Media Pool do Resolve, numa chamada só` });
    try { midias = await ponte().midias({ drt: p.drt, nome: p.nome }); }
    catch (e) { midias = { ok: false, erro: e.message }; }
  }
  pintarAndamento({ etapa: "importando",
                    detalhe: `o Resolve está lendo a timeline (${p.clipes || 0} planos) e religando a ` +
                             "mídia. Ele não informa o andamento: a barra se move até ele terminar." });

  let r;
  try {
    r = await ponte().importar({ drt: p.drt, pasta: p.pasta_no_resolve, nome: p.nome });
  } catch (e) {
    r = { ok: false, erro: e.message };
  }
  let fusion = null;
  if (r.ok && (p.fusion || []).length) {
    pintarAndamento({ etapa: "fusion", detalhe: `montando ${p.fusion.length} ` +
                      (p.fusion.length === 1 ? "composição" : "composições") +
                      " do Fusion (máscara, desfoque, Basic 3D), uma por clipe, e conferindo cada uma" });
    if (typeof ponte().fusion === "function") {
      try { fusion = await ponte().fusion({ nome: r.nome, uid: r.uid || "", clipes: p.fusion }); }
      catch (e) { fusion = { ok: false, erro: e.message }; }
    } else {
      fusion = { ok: false, erro: "a casca instalada é antiga: atualize o De Lá Pra Cá" };
    }
  }
  let compostos = null;
  if (r.ok && (p.compostos || []).length) {
    pintarAndamento({ etapa: "compostos", detalhe: `juntando ${p.compostos.length} ` +
                      (p.compostos.length === 1 ? "nest" : "nests") + " do Premiere em Compound Clip" });
    if (typeof ponte().compostos === "function") {
      try { compostos = await ponte().compostos({ nome: r.nome, uid: r.uid || "", grupos: p.compostos }); }
      catch (e) { compostos = { ok: false, erro: e.message }; }
    } else {
      compostos = { ok: false, erro: "a casca instalada é antiga: atualize o De Lá Pra Cá" };
    }
  }
  const confCtx = r.ok && p.conferencia ? { id: p.conferencia.id, pontos: p.conferencia.pontos,
                                            segundos: p.conferencia.segundos, nome: r.nome || p.nome, uid: r.uid || "" } : null;
  let conf = null;
  if (confCtx && el("rcPrprojConferir") && el("rcPrprojConferir").checked) {
    conf = await conferir(confCtx, false);
  }
  rcConf = confCtx ? { ctx: confCtx, resultado: conf } : null;
  let arquivo = {}, pendente = false;
  if (idEnvio) {
    const resp = await registrarTrazido({ id: idEnvio, ok: !!r.ok, detalhe: r.erro || "",
                                          timeline: r.ok ? { uid: r.uid || "", nome: r.nome || "" } : null });
    arquivo = resp || {};
    pendente = !resp && !!r.ok;
  }
  mostrarTrazendo(false);
  if (!r.ok) {
    await procurarNovos(true);
    dizer(r.erro || "o Resolve não importou a timeline", false);
    return;
  }

  const online = (p.clipes || 0) - (p.offline || 0);
  const noPool = (midias && midias.ok ? midias.importadas || 0 : 0) + (r.movidas || 0);
  const avisos = (p.avisos || []).map((a) => `<div class="dica">⚠ ${escapar(a)}</div>`).join("");
  el("rcFeito").innerHTML =
    `<p class="previa-ok">✓ “${escapar(r.nome || p.nome)}” na pasta ${escapar(p.pasta_no_resolve)}</p>
     <div class="placar">` +
    (p.nos_originais ? `<span class="pill ok">${p.nos_originais} nos originais</span>` : "") +
    (p.na_gerenciada ? `<span class="pill">${p.na_gerenciada} na mídia do Avid</span>` : "") +
    (!p.nos_originais && !p.na_gerenciada ? `<span class="pill ok">${online} com mídia</span>` : "") +
    (p.offline ? `<span class="pill warn">${p.offline} offline</span>` : "") +
    (p.titulos ? `<span class="pill ok">${p.titulos} ${p.titulos === 1 ? "título" : "títulos"}</span>` : "") +
    (p.notas ? `<span class="pill">${p.notas} ${p.notas === 1 ? "nota" : "notas"}</span>` : "") +
    (p.marcadores ? `<span class="pill warn">${p.marcadores} ${p.marcadores === 1 ? "marcador" : "marcadores"}</span>` : "") +
    (fusion && fusion.ok && fusion.criadas ? `<span class="pill ok">${fusion.criadas} ${fusion.criadas === 1 ? "composição" : "composições"} do Fusion</span>` : "") +
    (compostos && compostos.ok && compostos.criados ? `<span class="pill ok">${compostos.criados} Compound ${compostos.criados === 1 ? "Clip" : "Clips"}</span>` : "") +
    ((p.faltam || []).length ? `<span class="pill warn">${p.faltam.length} no proxy</span>` : "") +
    (p.ignorados ? `<span class="pill">${p.ignorados} ${p.ignorados === 1 ? "ignorado" : "ignorados"}</span>` : "") +
    (p.referencia ? `<span class="pill">referência: ${escapar(p.referencia)}</span>` : "") +
    (noPool ? `<span class="pill">${noPool} arquivos no Media Pool</span>` : "") +
    `<span class="pill">${relogio(Math.round((Date.now() - rcInicio) / 1000))}</span></div>` +
    (noPool ? `<div class="dica">A mídia está em De Lá Pra Cá › Mídia › ${escapar(r.nome || p.nome)}.</div>` : "") +
    (midias && !midias.ok ? `<p class="aviso">A mídia não foi importada antes (${escapar(midias.erro || "erro")}); a timeline usou o que o DRT declara.</p>` : "") +
    (midias && midias.faltaram ? `<p class="aviso">${midias.faltaram} arquivos o Resolve não importou; esses usam o que o DRT declara.</p>` : "") +
    (p.offline ? `<div class="dica">Os offline apontam para o original, com reel e TC: religue quando a mídia estiver nesta máquina.</div>` : "") +
    (fusion && !fusion.ok ? `<p class="aviso">As composições do Fusion não foram criadas (${escapar(fusion.erro || "erro")}). A timeline está completa; refaça máscara, blur e Basic 3D nos clipes com marcador amarelo.</p>` : "") +
    (fusion && fusion.ok && (fusion.clipes || []).some((c) => !c.ok) ? `<p class="aviso">Composições que não entraram: ${escapar(fusion.clipes.filter((c) => !c.ok).map((c) => `${c.arquivo} (${c.erro})`).join(" · "))}</p>` : "") +
    (compostos && !compostos.ok ? `<p class="aviso">Os nests não viraram Compound Clip (${escapar(compostos.erro || "erro")}): entraram abertos em trilhas, com a imagem certa.</p>` : "") +
    (compostos && compostos.ok && (compostos.grupos || []).some((g) => !g.ok) ? `<p class="aviso">Nests que ficaram em trilhas: ${escapar(compostos.grupos.filter((g) => !g.ok).map((g) => `${g.nome} (${g.erro})`).join(" · "))}</p>` : "") +
    ((p.faltam || []).length ? `<div class="dica">Os planos no proxy têm marcador vermelho. Quando o original chegar, religue pela pasta.</div>` : "") +
    ((p.fusion_recusadas || []).length ? `<p class="aviso">Sem composição do Fusion (refaça à mão): ${escapar(p.fusion_recusadas.join(" · "))}</p>` : "") +
    (arquivo.arquivado ? `<div class="dica">A Carta foi para “já importados”.</div>` : "") +
    (pendente ? `<div class="dica">⚠ ${escapar(AVISO_PENDENTE)}</div>` : "") +
    (arquivo.recusado ? `<div class="dica">⚠ ${escapar(avisoRecusado(arquivo.recusado))}</div>` : "") +
    (arquivo.aviso ? `<p class="aviso">${escapar(arquivo.aviso)}</p>` : "") +
    avisos + `<div id="rcConferencia"></div>`;
  pintarConferencia();
  el("rcFeito").hidden = false;
  await procurarNovos(true);
  dizer("");
}

let rcConf = null;
let rcConferindo = false;
let rcCancelarConf = false;
const LOTE_CONF = 6;

async function conferir(ctx, doComeco) {
  if (!ponte() || typeof ponte().conferirInicio !== "function") {
    return { erro: "a casca instalada é antiga: atualize o De Lá Pra Cá" };
  }
  rcConferindo = true;
  rcCancelarConf = false;
  el("rcBtnCancelar").disabled = false;
  const t0 = Date.now();
  let ini, iniciou = false, cancelado = false, erro = "";
  try {
    ini = await post("/conferencia/iniciar", { id: ctx.id, do_comeco: !!doComeco });
    pintarAndamento({ etapa: "conferindo", feitos: ini.comparados || 0, total: ini.total,
                      detalhe: "preparando o Resolve: desligando a referência e indo para a página Color" });
    const s = await ponte().conferirInicio({ nome: ctx.nome, uid: ctx.uid, trilhaReferencia: ini.trilha_referencia });
    if (!s || !s.ok) throw new Error((s && s.erro) || "o Resolve não começou a conferência");
    iniciou = true;
    const base = ini.total - ini.faltam.length;
    let feitos = 0, ruins = ini.nao_conferem || 0;
    for (let i = 0; i < ini.faltam.length; i += LOTE_CONF) {
      if (rcCancelarConf) { cancelado = true; break; }
      const lote = ini.faltam.slice(i, i + LOTE_CONF);
      const ult = lote[lote.length - 1];
      const ritmo = feitos ? (Date.now() - t0) / feitos : 500;
      const resta = Math.max(0, ini.faltam.length - feitos) * ritmo / 1000;
      pintarAndamento({ etapa: "conferindo", feitos: base + feitos, total: ini.total,
                        sufixo: `${ruins} ${ruins === 1 ? "não confere" : "não conferem"} · faltam ~${duracaoAprox(resta)}`,
                        detalhe: "exportando do Resolve e comparando com o vídeo de referência",
                        atual: `${ult.tc} · ${ult.plano || ""} (V${ult.trilha || "?"}, ${ult.ponta || ""})` });
      const q = await ponte().conferirQuadros({ pontos: lote });
      if (!q || !q.ok) throw new Error((q && q.erro) || "o Resolve não exportou os quadros");
      await post("/conferencia/quadros", { id: ctx.id, quadros: lote.map((x) => x.quadro) });
      feitos += lote.length;
      if (feitos % (LOTE_CONF * 5) === 0) {
        try { ruins = (await post("/conferencia/estado", { id: ctx.id })).nao_conferem || 0; } catch (_) {  }
      }
    }
  } catch (e) {
    erro = e.message || String(e);
  }
  let estado = null;
  for (let k = 0; k < 600; k++) {
    try { estado = await post("/conferencia/estado", { id: ctx.id }); } catch (_) { break; }
    if (!estado.na_fila) break;
    pintarAndamento({ etapa: "conferindo", feitos: estado.comparados, total: estado.total,
                      detalhe: "terminando as últimas comparações" });
    await new Promise((ok) => setTimeout(ok, 500));
  }
  let marcadores = 0, erroMarcas = "";
  if (iniciou) {
    let m = { marcas: [], cor: "Purple", dado: "dlpc-conferencia" };
    try { m = await post("/conferencia/marcas", { id: ctx.id }); } catch (_) {  }
    try {
      const f = (await ponte().conferirFim(m)) || {};
      marcadores = f.marcadores || 0;
      if (!f.ok) erroMarcas = f.erro || "a casca não respondeu";
    } catch (e) { erroMarcas = e.message || String(e); }
  }
  rcConferindo = false;
  el("rcBtnCancelar").disabled = true;
  return { estado, cancelado, erro, marcadores, erroMarcas, segundos: Math.round((Date.now() - t0) / 1000) };
}

function pintarConferencia() {
  const c = el("rcConferencia");
  if (!c) return;
  if (!rcConf) { c.innerHTML = ""; return; }
  const { ctx, resultado } = rcConf;
  const est = resultado && resultado.estado;
  if (!resultado) {
    c.innerHTML = `<div class="acoes"><button class="primary sm" data-conf="comecar">Conferir pela referência</button></div>
      <div class="dica">${ctx.pontos.toLocaleString("pt-BR")} quadros · cerca de ${duracaoAprox(ctx.segundos)} · dá para cancelar a qualquer momento</div>`;
    return;
  }
  const faltam = est ? est.total - est.comparados : 0;
  const pills = est ? `<span class="pill ok">${est.conferem} conferem</span>` +
    (est.nao_conferem ? `<span class="pill warn">${est.nao_conferem} não ${est.nao_conferem === 1 ? "confere" : "conferem"}</span>` : "") +
    (faltam > 0 ? `<span class="pill">${faltam} não conferidos</span>` : "") +
    `<span class="pill">${relogio(resultado.segundos || 0)}</span>` : "";
  const titulo = resultado.erro ? `<p class="aviso">A conferência parou: ${escapar(resultado.erro)}</p>`
    : (resultado.cancelado ? `<p class="bloco-titulo">Conferência cancelada</p>`
      : `<p class="previa-ok">✓ Conferido pela referência</p>`);
  c.innerHTML = titulo + `<div class="placar">${pills}</div>` +
    (est && est.nao_conferem && resultado.marcadores ? `<div class="dica">${resultado.marcadores} ${resultado.marcadores === 1 ? "ponto marcado" : "pontos marcados"} na timeline com marcador roxo (o nome diz o tipo; as Notes, o plano, a trilha e o TC).</div>` : "") +
    (est && est.nao_conferem && !resultado.marcadores ? `<p class="aviso">Os marcadores não entraram na timeline${resultado.erroMarcas ? ` (${escapar(resultado.erroMarcas)})` : ""}. Os resultados estão guardados: “Conferir de novo” tenta outra vez.</p>` : "") +
    ((resultado.cancelado || resultado.erro) ? `<div class="dica">Trilhas, página e agulha do Resolve voltaram como estavam.</div>` : "") +
    `<div class="acoes">` +
    (faltam > 0 ? `<button class="primary sm" data-conf="continuar">Continuar de onde parou</button>` : "") +
    `<button class="ghost sm" data-conf="comeco">${faltam > 0 ? "Conferir do começo" : "Conferir de novo"}</button></div>` +
    (est && est.comparados ? `<div class="acoes"><button class="sm" data-conf-pasta="1">Abrir a pasta desta conferência</button></div>` : "");
}

el("rcFeito").addEventListener("click", async (ev) => {
  if (ev.target.closest("[data-conf-pasta]") && rcConf) {
    try { await post("/pasta/abrir", { qual: "conferencia", id: rcConf.ctx.id }); dizer(""); }
    catch (e) { dizer("Não consegui abrir a pasta: " + e.message, false); }
    return;
  }
  const b = ev.target.closest("[data-conf]");
  if (!b || !rcConf || rcConferindo || rcOcupado) return;
  const doComeco = b.dataset.conf === "comeco";
  dizer("");
  rcInicio = Date.now();
  mostrarTrazendo(true, `Conferindo ${rcConf.ctx.nome}`);
  for (const x of el("rcFeito").querySelectorAll("[data-conf]")) x.disabled = true;
  const res = await conferir(rcConf.ctx, doComeco);
  mostrarTrazendo(false);
  rcConf.resultado = res;
  pintarConferencia();
});

let rcPendente = null;

async function casarOriginais(pedido, titulo, idEnvio) {
  dizer("");
  try {
    await post("/receber/originais", { ...pedido, pastas });
  } catch (e) { dizer(e.message, false); return; }
  rcPendente = { pedido, titulo, idEnvio };
  mostrarPasso("passoProgresso");
  acompanhar();
}

async function importarComOriginais() {
  if (!rcPendente) { dizer("escolha de novo o que importar", false); return; }
  const { pedido, titulo, idEnvio } = rcPendente;
  mostrarPasso("passoOrigem");
  await trazerPedido({ ...pedido, originais: true }, titulo, idEnvio);
  rcPendente = null;
}

let rcAntes = null;
let rcRef = { auto: null, caminho: "", som: "A" };

let rcConfEstimativa = null;
function pintarCaixaConferir() {
  const linha = el("rcPrprojConferirLinha");
  if (!linha) return;
  const temRef = !!(rcRef.caminho || rcRef.auto);
  const est = rcConfEstimativa;
  linha.hidden = !(temRef && est && est.pontos);
  if (linha.hidden) { el("rcPrprojConferir").checked = false; return; }
  el("rcPrprojConferirInfo").textContent =
    `(+ cerca de ${duracaoAprox(est.segundos)}, ≈ ${est.pontos.toLocaleString("pt-BR")} quadros)`;
}
function duracaoAprox(seg) {
  const m = Math.round((seg || 0) / 60);
  return m < 1 ? "1 min" : `${m} min`;
}

function pintarReferencia() {
  const tem = !!(rcRef.caminho || rcRef.auto);
  el("rcRefEscolher").hidden = tem;
  el("rcRefCartao").hidden = !tem;
  if (rcRef.caminho) {
    el("rcRefSelo").textContent = "escolhida";
    el("rcRefNome").textContent = rcRef.caminho.split(/[\\/]/).pop();
    el("rcRefDe").textContent = rcRef.caminho;
  } else if (rcRef.auto) {
    el("rcRefSelo").textContent = "achada na timeline";
    el("rcRefNome").textContent = rcRef.auto.arquivo || "";
    el("rcRefDe").textContent = `na trilha V${rcRef.auto.trilha} da sequência`;
  }
  el("rcRefSelo").className = `ref-selo${rcRef.caminho ? " escolhida" : ""}`;
  el("rcRefTirar").hidden = !rcRef.caminho;
  el("rcRefSom").hidden = !tem;
  for (const b of el("rcRefSom").children) b.classList.toggle("on", b.dataset.som === rcRef.som);
  pintarCaixaConferir();
}
const EXT_REFERENCIA = /\.(mov|mp4|mxf)$/i;
function soltarReferencia(caminhos) {
  const videos = caminhos.filter((c) => EXT_REFERENCIA.test(c));
  if (videos.length !== 1) {
    dizerNoArrasto(videos.length ? "um vídeo de referência por vez" : "a referência é um vídeo MOV, MP4 ou MXF", false);
    return;
  }
  rcRef.caminho = videos[0];
  pintarReferencia();
  dizerNoArrasto("vídeo de referência: " + (videos[0].split(/[\\/]/).pop() || videos[0]), true);
}
const escolherReferencia = () => escolherNativo("/escolher/referencia", (c) => {
  rcRef.caminho = Array.isArray(c) ? c[0] : c;
  pintarReferencia();
});
el("rcRefEscolher").onclick = escolherReferencia;
el("rcRefTrocar").onclick = escolherReferencia;
el("rcRefTirar").onclick = () => { rcRef.caminho = ""; pintarReferencia(); };
el("rcRefSom").onclick = (ev) => {
  const b = ev.target.closest("button[data-som]");
  if (!b) return;
  rcRef.som = b.dataset.som;
  pintarReferencia();
};

async function analisarPrproj(pedido, titulo) {
  dizer("");
  el("rcFeito").hidden = true;
  el("rcPrproj").hidden = true;
  rcInicio = Date.now();
  mostrarTrazendo(true, `Analisando ${titulo}`);
  pintarAndamento({ etapa: "lendo" });
  const relogioRc = setInterval(async () => {
    try { const p = await api("/receber/progresso"); if (p.ativo) pintarAndamento(p); } catch (_) {  }
  }, 500);
  let a;
  try {
    a = await post("/receber/prproj/analisar", { avulso: pedido.avulso, mob_id: pedido.mob_id, pastas });
  } catch (e) {
    clearInterval(relogioRc);
    mostrarTrazendo(false);
    dizer(e.corpo && e.corpo.cancelado ? "cancelado — nada foi importado" : e.message, !!(e.corpo && e.corpo.cancelado));
    return;
  }
  clearInterval(relogioRc);
  mostrarTrazendo(false);
  rcAntes = { pedido, titulo };
  pintarAntesDeMontar(a, titulo);
}

let rcMidias = [];
let rcIgnorados = new Set();
let rcMidSel = new Set();
let rcMidAncora = null;
let rcMidOrdem = null;
let rcAnalisePlanos = null;
const CHAVE_LARGURA_CODEC = "delapraca.midias.codec";
const ESTADO_MIDIA = {
  original: { classe: "ok", rotulo: "casou com o original" },
  sem_original: { classe: "warn", rotulo: "sem original: entra pelo proxy, com marcador vermelho" },
  nao_encontrado: { classe: "err", rotulo: "não encontrado: nem o proxy está no disco — entra OFFLINE no lugar, para religar depois" },
};
const RANK_ESTADO = { nao_encontrado: 0, sem_original: 1, original: 2 };

function midiasVisiveis() {
  const so = el("rcMidiasSoFaltam").checked;
  const lista = rcMidias.filter((m) => !so || m.estado !== "original");
  const cmp = (a, b) => String(a || "").localeCompare(String(b || ""), "pt-BR", { numeric: true, sensitivity: "base" });
  lista.sort(rcMidOrdem
    ? (a, b) => (rcMidOrdem.desc ? -1 : 1) * (cmp(a[rcMidOrdem.campo], b[rcMidOrdem.campo]) || cmp(a.nome, b.nome))
    : (a, b) => (RANK_ESTADO[a.estado] - RANK_ESTADO[b.estado]) || cmp(a.nome, b.nome));
  return lista;
}

function ignoravel(m) { return m.estado !== "original"; }

function pintarPlacarMidias() {
  const sem = rcMidias.filter((m) => m.estado === "sem_original");
  const noProxy = sem.filter((m) => !rcIgnorados.has(m.caminho)).length;
  const nada = rcMidias.filter((m) => m.estado === "nao_encontrado").length;
  const ign = rcMidias.filter((m) => ignoravel(m) && rcIgnorados.has(m.caminho)).length;
  const p = rcAnalisePlanos || { no_original: 0, planos: 0 };
  el("rcPrprojPlacar").innerHTML =
    `<span class="pill ok">${p.no_original} de ${p.planos} planos nos originais</span>` +
    (noProxy ? `<span class="pill warn">${noProxy} ${noProxy === 1 ? "arquivo" : "arquivos"} no proxy</span>` : "") +
    (nada ? `<span class="pill err">${nada} não ${nada === 1 ? "encontrado" : "encontrados"}</span>` : "") +
    (ign ? `<span class="pill">${ign} ${ign === 1 ? "ignorado" : "ignorados"}</span>` : "");
  el("rcMidiasConta").textContent = `${rcMidias.length} ${rcMidias.length === 1 ? "arquivo" : "arquivos"}`
    + (noProxy + nada ? ` · ${noProxy + nada} sem original` : "");
}

function pintarMidias() {
  const linhas = midiasVisiveis();
  const visiveis = new Set(linhas.map((m) => m.caminho));
  for (const c of Array.from(rcMidSel)) if (!visiveis.has(c)) rcMidSel.delete(c);
  el("rcMidiasColCodec").style.width = `${larguraCodec()}px`;
  for (const th of el("rcMidias").querySelectorAll("th[data-ordem]")) {
    const ativo = rcMidOrdem && th.dataset.ordem === rcMidOrdem.campo;
    th.dataset.seta = ativo ? (rcMidOrdem.desc ? "▾" : "▴") : "";
    th.setAttribute("aria-sort", ativo ? (rcMidOrdem.desc ? "descending" : "ascending") : "none");
  }
  el("rcMidiasCorpo").innerHTML = linhas.map((m) => {
    const e = ESTADO_MIDIA[m.estado] || ESTADO_MIDIA.sem_original;
    const ign = ignoravel(m) && rcIgnorados.has(m.caminho);
    const titulo = e.rotulo + (ign ? (m.estado === "nao_encontrado"
      ? " — ignorado, mas continua avisado (não está no disco)" : " — ignorado: sem marcador") : "");
    const botao = !ignoravel(m) ? ""
      : `<button type="button" class="midia-x" data-ign="${escapar(m.caminho)}"
           title="${ign ? "Voltar a avisar" : "Ignorar: sem versão em alta, tudo bem"}"
           aria-label="${ign ? "Voltar a avisar" : "Ignorar"} ${escapar(m.nome)}">${ign ? "↺" : "×"}</button>`;
    return `<tr data-c="${escapar(m.caminho)}" class="${[rcMidSel.has(m.caminho) ? "sel" : "", ign ? "ignorado" : ""].join(" ").trim()}"
              aria-selected="${rcMidSel.has(m.caminho)}">
      <td title="${escapar(titulo)}"><span class="midia-ponto ${e.classe}"></span><bdi>${escapar(m.nome)}</bdi>${ign ? `<span class="midia-tag">ignorado</span>` : ""}</td>
      <td title="${escapar(m.codec || "")}">${escapar(m.codec || "")}</td>
      <td class="midias-col-x">${botao}</td></tr>`;
  }).join("") || `<tr class="vazia"><td colspan="3">${rcMidias.length ? "nada a mostrar com este filtro" : "nenhum arquivo de vídeo"}</td></tr>`;
  pintarBotaoIgnorar(linhas);
  pintarPlacarMidias();
}

function alvosDoBotao(linhas) {
  const base = rcMidSel.size ? linhas.filter((m) => rcMidSel.has(m.caminho)) : linhas;
  return base.filter(ignoravel);
}
function pintarBotaoIgnorar(linhas) {
  const alvos = alvosDoBotao(linhas || midiasVisiveis());
  const todosIgnorados = alvos.length && alvos.every((m) => rcIgnorados.has(m.caminho));
  const quais = rcMidSel.size ? `selecionados (${alvos.length})` : "todos";
  const b = el("rcMidiasIgnorar");
  b.textContent = todosIgnorados ? `Voltar a avisar ${quais}` : `Ignorar ${quais}`;
  b.disabled = !alvos.length;
}

function ignorar(caminhos, sim) {
  for (const c of caminhos) { if (sim) rcIgnorados.add(c); else rcIgnorados.delete(c); }
  pintarMidias();
}

function larguraCodec() {
  let w = 96;
  try { w = Number(localStorage.getItem(CHAVE_LARGURA_CODEC)) || 96; } catch (_) {  }
  return Math.max(56, Math.min(260, w));
}

el("rcMidiasSoFaltam").onchange = () => pintarMidias();
el("rcMidiasIgnorar").onclick = () => {
  const alvos = alvosDoBotao(midiasVisiveis());
  const todosIgnorados = alvos.every((m) => rcIgnorados.has(m.caminho));
  ignorar(alvos.map((m) => m.caminho), !todosIgnorados);
};
el("rcMidiasCorpo").addEventListener("click", (ev) => {
  const x = ev.target.closest("button[data-ign]");
  if (x) {
    ignorar([x.dataset.ign], !rcIgnorados.has(x.dataset.ign));
    return;
  }
  const tr = ev.target.closest("tr[data-c]");
  if (!tr) return;
  const c = tr.dataset.c;
  const ordem = midiasVisiveis().map((m) => m.caminho);
  const somar = ev.ctrlKey || ev.metaKey;
  if (ev.shiftKey && rcMidAncora && ordem.indexOf(rcMidAncora) >= 0) {
    const [i, j] = [ordem.indexOf(rcMidAncora), ordem.indexOf(c)].sort((p, q) => p - q);
    if (!somar) rcMidSel = new Set();
    for (const k of ordem.slice(i, j + 1)) rcMidSel.add(k);
  } else if (somar) {
    if (rcMidSel.has(c)) rcMidSel.delete(c); else rcMidSel.add(c);
    rcMidAncora = c;
  } else {
    rcMidSel = rcMidSel.size === 1 && rcMidSel.has(c) ? new Set() : new Set([c]);
    rcMidAncora = c;
  }
  pintarMidias();
});
el("rcMidiasCaixa").addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && (ev.key === "a" || ev.key === "A")) {
    ev.preventDefault();
    rcMidSel = new Set(midiasVisiveis().map((m) => m.caminho));
    pintarMidias();
  } else if (ev.key === "Escape" && rcMidSel.size) {
    rcMidSel = new Set();
    pintarMidias();
  }
});
el("rcMidias").querySelector("thead").addEventListener("dblclick", (ev) => {
  const th = ev.target.closest("th[data-ordem]");
  if (!th || ev.target.closest(".midias-divisor")) return;
  const campo = th.dataset.ordem;
  rcMidOrdem = rcMidOrdem && rcMidOrdem.campo === campo ? { campo, desc: !rcMidOrdem.desc } : { campo, desc: false };
  pintarMidias();
});
el("rcMidiasDivisor").addEventListener("mousedown", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  const x0 = ev.clientX, w0 = larguraCodec();
  const mover = (e) => {
    const w = Math.max(56, Math.min(260, w0 - (e.clientX - x0)));
    el("rcMidiasColCodec").style.width = `${w}px`;
    try { localStorage.setItem(CHAVE_LARGURA_CODEC, String(Math.round(w))); } catch (_) {  }
  };
  const soltar = () => { document.removeEventListener("mousemove", mover); document.removeEventListener("mouseup", soltar); };
  document.addEventListener("mousemove", mover);
  document.addEventListener("mouseup", soltar);
});

function pintarAntesDeMontar(a, titulo) {
  const linha = (rotulo, classe, valor) =>
    `<div class="item rc-item"><div class="rc-texto"><span class="item-nome">${escapar(rotulo)}</span></div>` +
    `<span class="pill ${classe}">${escapar(valor)}</span></div>`;
  const e = a.efeitos || {};
  const sem = Object.entries(a.sem_traducao || {});
  const au = a.audio || {};
  const efa = Object.entries(au.efeitos || {});
  el("rcPrprojAudioInfo").textContent = au.clipes ? `(${au.clipes} clipes, ${au.trilhas} trilhas)` : "(a sequência não tem áudio)";
  rcRef = { auto: a.referencia || null, caminho: "", som: "A" };
  rcConfEstimativa = a.conferencia || null;
  pintarReferencia();
  el("rcPrprojAudio").disabled = !au.clipes;
  el("rcPrprojCompoundLinha").hidden = !e.nests;
  el("rcPrprojCompoundInfo").textContent = e.nests ? `(${e.nests} ${e.nests === 1 ? "nest" : "nests"})` : "";
  el("rcPrprojTitulo").textContent = "sequência analisada · resumo";
  rcAnalisePlanos = { no_original: a.no_original, planos: a.planos };
  rcMidias = a.midias || [];
  const presentes = new Set(rcMidias.map((m) => m.caminho));
  rcIgnorados = new Set((a.ignorados || []).concat(Array.from(rcIgnorados)).filter((c) => presentes.has(c)));
  rcMidSel = new Set();
  rcMidAncora = null;
  pintarMidias();
  el("rcPrprojLinhas").innerHTML = [
    e.velocidade ? linha(`Velocidade, reverso (${e.velocidade})`, "ok", "traduz") : "",
    e.keyframes ? linha(`Zoom e posição com keyframe (${e.keyframes})`, "ok", "traduz") : "",
    e.crop_flip ? linha(`Crop, flip (${e.crop_flip})`, "ok", "traduz") : "",
    e.fusion ? linha(`Máscara, blur, Basic 3D (${e.fusion} ${e.fusion === 1 ? "clipe" : "clipes"})`, "ok", "Fusion") : "",
    e.luma_key ? linha(`Luma Key (${e.luma_key})`, "warn", "Screen · conferir") : "",
    e.graficos ? linha(`Gráficos e títulos (${e.graficos})`, "warn", "conferir") : "",
    sem.length ? linha(`Sem tradução: ${sem.map(([n, k]) => `${n} (${k})`).join(", ")}`, "err", "aviso") : "",
    au.clipes ? linha(`Áudio: ${au.clipes} clipes em ${au.trilhas} trilhas, ${au.transicoes} transições`, "ok", "traduz") : "",
    au.volume ? linha(`Volume do clipe (${au.volume})`, "warn", "ainda não") : "",
    efa.length ? linha(`Efeitos de áudio: ${efa.slice(0, 4).map(([n, k]) => `${n} (${k})`).join(", ")}${efa.length > 4 ? "…" : ""}`, "err", "não traduz") : "",
  ].join("") || `<div class="dica">Só cortes: nada a traduzir além do enquadramento.</div>`;
  el("rcPrproj").hidden = false;
  pintarCarga();
}

let reanalise = null;
function reanalisarSePreciso() {
  if (!rcAntes || rcOcupado) return;
  clearTimeout(reanalise);
  reanalise = setTimeout(() => {
    if (!rcAntes || rcOcupado) return;
    const { pedido, titulo } = rcAntes;
    analisarPrproj(pedido, titulo);
  }, 400);
}

el("rcPrprojMontar").onclick = async () => {
  if (!rcAntes) return;
  const { pedido, titulo } = rcAntes;
  el("rcPrproj").hidden = true;
  rcAntes = null;
  await trazerPedido({ ...pedido, analisado: true }, titulo, null);
};
el("rcPrprojCancelar").onclick = () => {
  rcAntes = null;
  el("rcPrproj").hidden = true;
  pintarCarga();
  dizer("nada foi importado", true);
};

let rcAvulso = null;

const ROTULO_TIPO_CARGA = { aaf: "AAF", carta: "Carta Aberta", avb: "bin do Avid", prproj: "projeto do Premiere" };

function sequenciasDaCarga() {
  return rcCarga && rcCarga.arquivo && ["avb", "prproj"].includes(rcCarga.arquivo.tipo)
    ? (rcCarga.arquivo.sequencias || []) : [];
}

function taxaLegivel(fps) {
  const n = Number(fps) || 0;
  return n ? `${String(Math.round(n * 1000) / 1000).replace(".", ",")} fps` : "";
}

function fatosDe(x) {
  if (!x) return [];
  const trilhas = x.video != null && x.audio != null ? `${x.video} de vídeo · ${x.audio} de áudio` : "";
  return [["duração", x.duracao || ""], ["taxa", taxaLegivel(x.fps)], ["trilhas", trilhas],
          ["planos", x.planos != null ? String(x.planos) : ""]].filter(([, v]) => v);
}

function pintarSequencias() {
  const seqs = sequenciasDaCarga();
  const bloco = el("rcAvulso");
  if (!seqs.length || (rcCarga.arquivo.tipo === "avb" && seqs.length < 2)) { bloco.hidden = true; return; }
  el("rcAvulsoTitulo").textContent = `${seqs.length} ${seqs.length === 1 ? "sequência" : "sequências"} — marque qual importar`;
  el("rcSequencias").innerHTML = seqs.map((s) => {
    const marcada = s.mob_id === rcSeqEscolhida;
    const info = [s.duracao || "", s.planos ? `${s.planos} planos` : ""].filter(Boolean).join(" · ");
    return `<label class="seq${marcada ? " sel" : ""}">
      <input type="radio" name="rcSeq" value="${escapar(s.mob_id)}"${marcada ? " checked" : ""}${rcOcupado ? " disabled" : ""}>
      <span class="seq-texto"><span class="seq-nome">${escapar(s.nome || "(sem nome)")}</span>
        <span class="seq-info">${escapar(info)}</span></span></label>`;
  }).join("");
  bloco.hidden = false;
}

function pintarCarga() {
  if (NLE !== "resolve") return;
  const naAba = modoOrigem === "aaf";
  const vazio = !rcCarga;
  el("rcBtnAbrir").hidden = !vazio;
  el("rcCarga").hidden = vazio;
  el("rcAvancadas").hidden = rcOcupado || (modoOrigem !== "aaf" && modoOrigem !== "atualizar");
  el("rcAvProjeto").hidden = !(rcCarga && rcCarga.arquivo && rcCarga.arquivo.tipo === "prproj");
  const antesDeMontar = !el("rcPrproj").hidden;
  el("rcAcoes").hidden = vazio || !naAba || rcOcupado || antesDeMontar;
  el("rcAcoesMontar").hidden = !antesDeMontar || rcOcupado;
  el("barraAcoes").hidden = el("rcAcoes").hidden && el("rcAcoesMontar").hidden;
  el("mlAcordeao").hidden = antesDeMontar;
  ajustarBarra();
  if (vazio) return;

  const seq = sequenciasDaCarga().find((s) => s.mob_id === rcSeqEscolhida) || null;
  let nome, de, fatos;
  if (rcCarga.envio) {
    const e = rcCarga.envio;
    nome = e.nome || e.sequencia || "?";
    de = ["Magic Link", rotuloOrigem(e), quando(e.enviado_em)].filter(Boolean).join(" · ");
    fatos = fatosDe(e);
  } else {
    const r = rcCarga.arquivo;
    const seqs = r.sequencias || [];
    nome = r.tipo === "carta" || r.tipo === "aaf" ? (r.nome || r.arquivo) : r.arquivo;
    de = [ROTULO_TIPO_CARGA[r.tipo] || "", r.tipo === "carta" || r.tipo === "aaf" ? r.arquivo
          : antesDeMontar && seq ? seq.nome || "(sem nome)"
          : `${seqs.length} ${seqs.length === 1 ? "sequência" : "sequências"}`].filter(Boolean).join(" · ");
    fatos = fatosDe(seq || r);
  }
  el("rcCargaNome").textContent = nome;
  el("rcCargaDe").textContent = de;
  el("rcCargaFatos").innerHTML = fatos.map(([k, v]) => `<div><dt>${escapar(k)}</dt><dd>${escapar(v)}</dd></div>`).join("");
  el("rcCargaTirar").disabled = rcOcupado;
  el("rcCargaTrocar").disabled = rcOcupado;
  el("rcCargaTrocar").hidden = !!rcCarga.envio || antesDeMontar;
  pintarSequencias();
  if (antesDeMontar) el("rcAvulso").hidden = true;

  const precisaSeq = !rcCarga.envio && sequenciasDaCarga().length > 0;
  el("rcImportar").disabled = precisaSeq && !seq;
  el("rcImportar").textContent = rcCarga.envio ? "Importar como nova" : "Ler Timeline Selecionada";
  el("rcAtualizarEnvio").hidden = !rcCarga.envio;
}

function ajustarBarra() {
  const b = el("barraAcoes");
  document.body.style.paddingBottom = b.hidden ? "" : `${b.offsetHeight + 16}px`;
}
window.addEventListener("resize", () => { if (NLE === "resolve") ajustarBarra(); });

function carregarArquivo(r) {
  const seqs = r.sequencias || [];
  desfazerLeitura();
  rcCarga = { arquivo: r };
  rcAvulso = r;
  rcSeqEscolhida = seqs.length === 1 ? seqs[0].mob_id : null;
  rcIgnorados = new Set();
  if (r.tipo === "prproj" && !pastas.length && (r.pastas || []).length) {
    addPasta(r.pastas);
  }
  el("rcFeito").hidden = true;
  pintarCarga();
  pintarTabela();
  dizer(seqs.length > 1 ? `${seqs.length} sequências em ${r.arquivo} — marque qual importar` : "", true);
}

async function abrirArquivo(caminho) {
  if (rcOcupado) return;
  let r;
  try {
    r = await post("/receber/abrir", typeof caminho === "string" && caminho ? { caminho } : {});
  } catch (e) { dizer(e.message, false); return; }
  if (r.cancelado) return;
  carregarArquivo(r);
}

function desfazerLeitura() {
  clearTimeout(reanalise);
  rcAntes = null;
  el("rcPrproj").hidden = true;
  rcRef = { auto: null, caminho: "", som: "A" };
  rcConfEstimativa = null;
  rcMidias = [];
  rcMidSel = new Set();
  rcMidAncora = null;
}

function carregarEnvio(id) {
  const envio = (rcCaixa.envios || []).find((e) => e.id === id);
  if (!envio || rcOcupado) return;
  desfazerLeitura();
  rcCarga = { envio };
  rcAvulso = null;
  rcSeqEscolhida = null;
  el("rcFeito").hidden = true;
  pintarCarga();
  pintarTabela();
  dizer("");
  el("rcCarga").scrollIntoView({ block: "nearest" });
}

function tirarCarga() {
  if (rcOcupado) return;
  desfazerLeitura();
  rcCarga = null;
  rcAvulso = null;
  rcSeqEscolhida = null;
  rcIgnorados = new Set();
  pintarCarga();
  pintarTabela();
}

function pedidoDaCarga() {
  if (!rcCarga) return null;
  if (rcCarga.envio) {
    const e = rcCarga.envio;
    return { pedido: { id: e.id }, titulo: e.nome || e.sequencia || "", idEnvio: e.id, fonte: "o envio" };
  }
  const r = rcCarga.arquivo;
  const fonte = { aaf: "o próprio AAF", carta: "a Carta", avb: "a bin", prproj: "o projeto do Premiere" }[r.tipo] || "o arquivo";
  if (r.tipo === "carta" || r.tipo === "aaf") return { pedido: { avulso: r.chave }, titulo: r.nome || r.arquivo, idEnvio: null, fonte };
  const seq = (r.sequencias || []).find((s) => s.mob_id === rcSeqEscolhida);
  return seq ? { pedido: { avulso: r.chave, mob_id: seq.mob_id }, titulo: seq.nome || r.arquivo, idEnvio: null, fonte } : null;
}

let originaisDispensadoEm = null;

async function projetoAberto() {
  try { return ponte() && typeof ponte().projeto === "function" ? String((await ponte().projeto()).projeto || "") : ""; }
  catch (_) { return ""; }
}

async function perguntarOriginais(fonte) {
  const projeto = await projetoAberto();
  if (originaisDispensadoEm !== null && originaisDispensadoEm === projeto) return true;
  const prproj = fonte === "o projeto do Premiere";
  el("semOriginaisTexto").textContent = "Nenhuma pasta de originais foi indicada. A timeline vai ser montada "
    + `com os arquivos que ${fonte} referencia — onde estiverem. `
    + (prproj ? "O que não tiver original entra no proxy, com marcador vermelho."
              : "O que não for encontrado entra offline.");
  el("semOriginaisProjeto").textContent = projeto
    ? `Não perguntar de novo neste projeto (${projeto})` : "Não perguntar de novo nesta sessão";
  el("semOriginaisNaoPerguntar").checked = false;
  el("semOriginais").hidden = false;
  el("semOriginaisMontar").focus();
  return new Promise((fim) => {
    const fechar = (montar) => {
      el("semOriginais").hidden = true;
      el("semOriginaisMontar").onclick = el("semOriginaisIndicar").onclick = el("semOriginais").onkeydown = null;
      if (montar && el("semOriginaisNaoPerguntar").checked) originaisDispensadoEm = projeto;
      fim(montar);
    };
    el("semOriginaisMontar").onclick = () => fechar(true);
    el("semOriginaisIndicar").onclick = () => { fechar(false); el("btnEscolherPasta").click(); };
    el("semOriginais").onkeydown = (ev) => { if (ev.key === "Escape") fechar(false); };
  });
}

async function importarCarga() {
  if (rcOcupado || !rcCarga) return;
  const alvo = pedidoDaCarga();
  if (!alvo) { dizer("marque qual sequência importar", false); return; }
  if (!pastas.length && !(await perguntarOriginais(alvo.fonte))) return;
  await trazerPedido(alvo.pedido, alvo.titulo, alvo.idEnvio);
}

let atNovaPedida = null;
function atualizarComEnvio() {
  if (rcOcupado || !rcCarga || !rcCarga.envio) return;
  atNovaPedida = rcCarga.envio.id;
  irParaModo("atualizar");
}

el("rcBtnAbrir").onclick = () => abrirArquivo();
el("rcCargaTrocar").onclick = () => abrirArquivo();
el("rcCargaTirar").onclick = tirarCarga;
el("rcImportar").onclick = importarCarga;
el("rcAtualizarEnvio").onclick = atualizarComEnvio;
el("rcSequencias").addEventListener("change", (ev) => {
  const r = ev.target.closest("input[name=rcSeq]");
  if (!r) return;
  rcSeqEscolhida = r.value;
  pintarCarga();
});
el("rcBtnCancelar").onclick = async () => {
  el("rcBtnCancelar").disabled = true;
  if (rcConferindo) { rcCancelarConf = true; return; }
  try { await post("/receber/cancelar"); } catch (_) {  }
};
el("origemAAF").addEventListener("click", (ev) => {
  const t = ev.target.closest("button");
  if (!t || t.disabled) return;
  if (t.dataset.carregar) carregarEnvio(t.dataset.carregar);
  else if (t.dataset.procurar) procurarNovos(false);
  else if (t.dataset.irAjustes) irPara("ajustes");
});

function pintarAjustesCanais(r) {
  if (!r) return;
  el("ajCanais").innerHTML = (r.canais || []).map((c) =>
    `<div class="item rc-item"><div class="rc-texto"><span class="item-nome">Edição ⇄ ${escapar(c.rotulo)}</span>`
    + `<div class="item-seg"><bdi>${escapar(c.pasta || "não conectado")}</bdi>`
    + `${c.pasta && !c.conectado ? " — inacessível agora" : ""}</div></div>`
    + `<button class="sm" data-conectar="${escapar(c.setor)}">${c.pasta ? "Trocar…" : "Conectar…"}</button></div>`
  ).join("");
  el("ajCanaisDica").textContent = "Conecte só os canais do seu trabalho: a edição conecta um "
    + "para cada time; a cor, só o da Cor; o VFX, só o do VFX. Para um setor não abrir a pasta "
    + "do outro nem pelo Explorer, peça a quem cuida da rede as permissões de cada pasta.";
}

async function carregarCanais() {
  try { pintarAjustesCanais(await api(`/canais?nle=${NLE}`)); } catch (_) {  }
}

async function canaisMudaram(r) {
  pintarAjustesCanais(r);
  if (NLE === "mc") carregarMagic();
  procurarNovos(true);
}

el("ajCanais").onclick = async (ev) => {
  const b = ev.target.closest("button[data-conectar]");
  if (!b) return;
  try {
    const r = await post("/canais/conectar", { setor: b.dataset.conectar, nle: NLE });
    await canaisMudaram(r);
    if (!r.cancelado) dizer(NLE === "mc" ? "canal guardado neste projeto" : "canal guardado nesta máquina", true);
  } catch (e) { dizer(e.message, false); }
};

pastas = lerPastas();
renderPastas();

if (VISTA_REVISAO) {
  document.body.classList.add("vista-revisao");
  irPara("deLaPraCa");
  mostrarPasso("passoRevisao");
  filtro = "tudo";
  for (const x of el("filtro").children) {
    x.classList.toggle("on", x.dataset.filtro === "tudo");
  }
  el("btnConcluirRevisao").hidden = false;
  el("btnVoltar").hidden = true;
  el("btnAplicar").parentElement.hidden = true;
  carregarRevisao().catch(() => {});
} else if (VISTA_CONFIG) {
  document.body.classList.add("vista-config");
  document.title = "De Lá Pra Cá — Configurações";
  carregarCache();
} else {
  irPara(lerAba());
  mostrarPasso("passoOrigem");
  if (NLE === "resolve") irParaModo("aaf");
}

baterNoServico();
setInterval(baterNoServico, 4000);
if (VISTA_REVISAO) {
  setInterval(() => carregarRevisao().catch(() => {}), 3000);
} else {
  setInterval(() => {
    if (!el("passoRevisao").hidden) carregarRevisao().catch(() => {});
  }, 3000);
}


for (const id of ["rcMarcar", "rcColorir", "rcNotas"]) {
  const caixa = el(id);
  if (!caixa) continue;
  try {
    const salvo = localStorage.getItem("dlpc." + id);
    if (salvo !== null) caixa.checked = salvo === "1";
  } catch (_) {  }
  caixa.addEventListener("change", () => {
    try { localStorage.setItem("dlpc." + id, caixa.checked ? "1" : "0"); } catch (_) {  }
  });
}


const AMOSTRA_MARCADOR = {
  Blue: "#3a7bea", Cyan: "#23c5c9", Green: "#2fae4a", Yellow: "#e8cf1c", Red: "#e03a3a",
  Pink: "#f06bc8", Purple: "#8a4fd8", Fuchsia: "#c0307a", Rose: "#f0a0bd", Lavender: "#b8a6e8",
  Sky: "#8cc6f0", Mint: "#9be0b5", Lemon: "#f3ec8a", Sand: "#d9bf8c", Cocoa: "#8a5a3c",
  Cream: "#f2ead5",
};
const AMOSTRA_CLIPE = {
  Orange: "#eb6e01", Apricot: "#ffa833", Yellow: "#d9a906", Lime: "#9fc615", Olive: "#5f9923",
  Green: "#448f65", Teal: "#00ad9f", Navy: "#1b4f8a", Blue: "#4b97d4", Purple: "#8e5fc0",
  Violet: "#b25fc0", Pink: "#e36fa8", Tan: "#b9af97", Beige: "#c4915e", Brown: "#99662f",
  Chocolate: "#7a4a2c",
};
let codigoDeCores = null;

function pintarLegendaDasCores(codigo) {
  const alvo = el("rcLegenda");
  if (!alvo || !codigo) return;
  const partes = Object.keys(codigo.cores).map(cat =>
    `${codigo.cores[cat].clipe} = ${{ distorcao: "possível distorção (Stretch)",
      refazer: "refazer", conferir: "conferir" }[cat] || cat}`);
  alvo.textContent = "cor local (só o segmento, nunca o clipe na bin): " + partes.join(", ");
}

function pintarCodigoDeCores(estado) {
  codigoDeCores = estado.codigo;
  const alvo = el("ajCores");
  const escolha = (cat, campo, tabela, amostras, rotulo) => {
    const atual = estado.codigo.cores[cat][campo];
    const opcoes = tabela.map(n =>
      `<option value="${escapar(n)}"${n === atual ? " selected" : ""}>${escapar(n)}</option>`).join("");
    return `<label class="cor-escolha"><span class="cor-rot">${rotulo}</span>
      <span class="cor-linha"><i class="cor-amostra${campo === "marcador" ? " mk" : ""}"
        style="background:${amostras[atual] || "#777"}"></i>
      <select data-cat="${escapar(cat)}" data-campo="${campo}">${opcoes}</select></span></label>`;
  };
  alvo.innerHTML = Object.keys(estado.categorias).map(cat => {
    const c = estado.categorias[cat];
    return `<div class="cor-cat">
      <p class="cor-nome">${escapar(c.rotulo)}${c.sub ? ` <em>· ${escapar(c.sub)}</em>` : ""}</p>
      <div class="cor-escolhas">
        ${escolha(cat, "marcador", estado.tabelas.marcador, AMOSTRA_MARCADOR, "marcador")}
        ${escolha(cat, "clipe", estado.tabelas.clipe, AMOSTRA_CLIPE, "segmento")}
      </div>
      <p class="dica">${c.quais.map(escapar).join(" · ")}</p></div>`;
  }).join("");
  if (document.activeElement !== el("ajPalavra")) el("ajPalavra").value = estado.codigo.palavra;
  const aviso = el("ajCoresAviso");
  aviso.hidden = !estado.avisos.length;
  aviso.textContent = estado.avisos.join(". ") + (estado.avisos.length ? "." : "");
  pintarLegendaDasCores(estado.codigo);
}

async function carregarCodigoDeCores() {
  if (NLE !== "resolve") return;
  try { pintarCodigoDeCores(await api("/ajustes/sinais")); }
  catch (e) { dizer("Não consegui ler o código de cores: " + e.message, false); }
}

async function gravarCodigoDeCores(pedido) {
  try { pintarCodigoDeCores(await post("/ajustes/sinais", pedido)); dizer("Código de cores guardado.", true); }
  catch (e) { dizer("Não consegui guardar o código de cores: " + e.message, false); }
}

el("ajCores").addEventListener("change", ev => {
  const s = ev.target;
  if (!s.matches || !s.matches("select[data-cat]") || !codigoDeCores) return;
  const codigo = JSON.parse(JSON.stringify(codigoDeCores));
  codigo.cores[s.dataset.cat][s.dataset.campo] = s.value;
  gravarCodigoDeCores({ codigo });
});
el("ajPalavra").addEventListener("change", () => {
  if (!codigoDeCores) return;
  gravarCodigoDeCores({ codigo: { ...codigoDeCores, palavra: el("ajPalavra").value } });
});
el("btnCoresFabrica").onclick = () => gravarCodigoDeCores({ fabrica: true });
carregarCodigoDeCores();


const FAIXA_AT = {
  atualizacao: { rotulo: "atualização", classe: "ok" },
  grande: { rotulo: "mudança grande", classe: "warn" },
  nova: { rotulo: "sugere importar como nova", classe: "err" },
};
const MOTIVO_AT = {
  desta_timeline: "de onde esta timeline veio",
  mesmo_nome_de_timeline: "importada com este nome",
  mesma_sequencia: "a mesma sequência do Avid",
  mesmo_nome: "o mesmo nome, outra versão",
  recente: "importada nesta máquina",
};
const CLASSES_AT = [
  ["igual", "iguais"], ["deslocado", "só deslocados"], ["aparado", "aparados ou esticados"],
  ["novo", "novos no corte"], ["retirado", "retirados"], ["trocado", "takes trocados (entram como novos)"],
];
const atOpcoes = { antiga: [], nova: [] };
let atTimelines = [];
let atUltimo = null;

const selDe = (lado) => el(lado === "antiga" ? "atAntiga" : "atNova");

function pintarOpcoes(lado, escolhido) {
  const sel = selDe(lado);
  sel.innerHTML = `<option value="">— escolha —</option>` + atOpcoes[lado].map((o, i) =>
    `<option value="${i}">${escapar(o.rotulo)}</option>`).join("");
  sel.value = escolhido != null && escolhido >= 0 ? String(escolhido) : "";
}

function opcao(lado, versao, rotulo) {
  const chave = JSON.stringify(versao);
  const i = atOpcoes[lado].findIndex((o) => JSON.stringify(o.versao) === chave);
  if (i >= 0) return i;
  atOpcoes[lado].push({ versao, rotulo });
  return atOpcoes[lado].length - 1;
}

function versaoDe(lado) {
  const o = atOpcoes[lado][Number(selDe(lado).value)];
  return selDe(lado).value === "" || !o ? null : o.versao;
}

function timelineEscolhida() {
  return el("atTrabalho").value === "" ? null : atTimelines[Number(el("atTrabalho").value)] || null;
}

function conferirExportada(t, tl) {
  if (t.ok && t.nome && t.nome !== tl.nome) {
    return { ok: false, erro: `o Resolve exportou “${t.nome}” em vez de “${tl.nome}” — nada foi alterado; `
      + "abra a timeline de trabalho no Resolve e tente de novo" };
  }
  return t;
}

function envioRotulo(e) {
  return `${e.nome || e.sequencia || "?"} · Magic Link ${quando(e.enviado_em)}${e.importado ? " · já importada" : ""}`;
}

async function carregarTimelines(preferida) {
  const r = await ponte().timelines();
  if (!r.ok) throw new Error(r.erro || "o Resolve não listou as timelines");
  atTimelines = r.timelines || [];
  const sel = el("atTrabalho");
  sel.innerHTML = atTimelines.map((t, i) =>
    `<option value="${i}">${escapar(t.nome)}${t.atual ? " · aberta agora" : ""}</option>`).join("");
  const alvo = atTimelines.find((t) => preferida && t.nome === preferida.nome)
    || atTimelines.find((t) => t.atual) || atTimelines[0];
  if (alvo) sel.value = String(atTimelines.indexOf(alvo));
}

let atSugestao = 0;
async function sugerirAntiga() {
  const vez = ++atSugestao;
  const tl = timelineEscolhida();
  let lista = [];
  try {
    lista = (await post("/receber/partidas", { timeline: tl ? { uid: tl.uid, nome: tl.nome } : null })).partidas || [];
  } catch (_) {  }
  if (vez !== atSugestao) return;
  const antes = versaoDe("antiga");
  atOpcoes.antiga = atOpcoes.antiga.filter((o) => !o.versao.partida);
  let escolhida = -1;
  for (const p of lista) {
    const i = opcao("antiga", { partida: p.id },
      `${p.timeline_nome || p.sequencia || p.id} · ${quando(p.trazido_em)} · ${MOTIVO_AT[p.motivo] || p.motivo}`);
    const forte = p.motivo === "desta_timeline" || p.motivo === "mesmo_nome_de_timeline";
    if (escolhida < 0 && forte) escolhida = i;
  }
  if (antes && antes.avulso) escolhida = opcao("antiga", antes, "");
  pintarOpcoes("antiga", escolhida);
}

function travarEscolhas(sim) {
  for (const id of ["atTrabalho", "atAntiga", "atNova", "atBtnAntiga", "atBtnNova", "atComparar", "atAudio"]) {
    el(id).disabled = sim;
  }
}
const atOcupado = () => rcOcupado || atComparando;

function oferecerNovasDoCanal() {
  const envios = (rcCaixa.envios || []).filter((e) => String(e.tipo || "").indexOf("insert") !== 0)
    .sort((a, b) => (a.importado - b.importado) || String(b.enviado_em).localeCompare(String(a.enviado_em)));
  atOpcoes.nova = atOpcoes.nova.filter((o) => !o.versao.envio);
  for (const e of envios) opcao("nova", { envio: e.id }, envioRotulo(e));
}

async function abaAtualizar() {
  el("rcAtualizar").hidden = true;
  if (!ponte() || typeof ponte().timelines !== "function") {
    dizer("abra este painel pelo Resolve (e rode o ATUALIZAR.bat se ele for antigo)", false);
    return;
  }
  try { await carregarTimelines(null); } catch (e) { dizer(e.message, false); return; }
  oferecerNovasDoCanal();
  const pedida = atNovaPedida;
  atNovaPedida = null;
  pintarOpcoes("nova", pedida ? atOpcoes.nova.findIndex((o) => o.versao.envio === pedida) : -1);
  await sugerirAntiga();
}

async function escolherVersao(lado) {
  if (atOcupado()) return;
  let r;
  try { r = await post("/receber/abrir"); } catch (e) { dizer(e.message, false); return; }
  if (r.cancelado) return;
  let i = -1;
  if (r.tipo === "carta" || r.tipo === "aaf") {
    i = opcao(lado, { avulso: r.chave }, r.arquivo);
  } else {
    const seqs = r.sequencias || [];
    for (const s of seqs) {
      const j = opcao(lado, { avulso: r.chave, mob_id: s.mob_id }, `${r.arquivo} › ${s.nome || "(sem nome)"}`);
      if (i < 0) i = j;
    }
    if (seqs.length > 1) dizer(`${seqs.length} sequências em ${r.arquivo}: confira na lista qual é a versão ${lado}`, true);
  }
  pintarOpcoes(lado, i);
  atUltimo = null;
  el("rcAtualizar").hidden = true;
}

async function compararAtualizar(reusar) {
  const tl = timelineEscolhida();
  const antiga = versaoDe("antiga");
  const nova = versaoDe("nova");
  if (atOcupado()) return;
  if (!tl || !antiga || !nova) { dizer("escolha a timeline de trabalho, a versão antiga e a nova", false); return; }
  const anterior = reusar && atUltimo ? atUltimo.trabalho : null;
  dizer("");
  atUltimo = null;
  atComparando = true;
  travarEscolhas(true);
  el("atTitulo").textContent = `Atualizar “${tl.nome}”`;
  el("rcAtualizar").hidden = false;
  el("atAplicar").disabled = true;
  el("atBackup").textContent = "";
  el("atResumo").innerHTML = "";
  try {
    let t = anterior;
    if (!t) {
      el("atMotivo").textContent = "lendo a timeline de trabalho no Resolve (nada é alterado)…";
      try { t = conferirExportada(await ponte().exportarTrabalho({ uid: tl.uid, nome: tl.nome }), tl); }
      catch (e) { t = { ok: false, erro: e.message }; }
      if (!t.ok) {
        el("atMotivo").textContent = "";
        el("atResumo").innerHTML = `<p class="aviso">${escapar(t.erro || "o Resolve não exportou a timeline de trabalho")}</p>`;
        return;
      }
    }
    el("atMotivo").textContent = "comparando…";
    let r;
    try {
      r = await post("/receber/comparar", { antiga, nova, audio: el("atAudio").checked,
                                            trabalho: t.drt, contagem: t.contagem, inicio: t.inicio });
    } catch (e) {
      el("atMotivo").textContent = "";
      el("atResumo").innerHTML = `<p class="aviso">${escapar(e.message)}</p>`;
      return;
    }
    el("atMotivo").textContent = "";
    atUltimo = { antiga, nova, timeline: tl, nome: r.nome || "", trabalho: t };
    pintarAtualizar(r, tl);
  } finally {
    atComparando = false;
    travarEscolhas(false);
  }
}

function pintarAtualizar(r, tl) {
  const f = FAIXA_AT[r.faixa] || FAIXA_AT.grande;
  const faixas = r.faixas || [25, 60];
  const pct = Math.max(0, Math.min(100, Number(r.pct_imagem) || 0));
  const conta = r.contagem || {};
  const bate = !r.encaixe || r.encaixe.bate;
  const linhas = CLASSES_AT.map(([k, rotulo]) =>
    `<div class="at-linha"><span>${rotulo}</span><span>${Number(conta[k]) || 0}</span></div>`).join("");
  el("atResumo").innerHTML =
    (bate ? "" : `<p class="aviso">${escapar(r.encaixe.texto)}</p>`)
    + `<div class="placar"><span class="pill ${f.classe}">${pct.toLocaleString("pt-BR")}% da imagem mudou · ${f.rotulo}</span>`
    + (r.pct_audio != null ? `<span class="pill">${Number(r.pct_audio).toLocaleString("pt-BR")}% do áudio</span>` : "")
    + `</div>
     <div class="at-medida" aria-hidden="true"><i class="${f.classe}" style="width:${pct}%"></i>
       <b style="left:${faixas[0]}%"></b><b style="left:${faixas[1]}%"></b></div>
     <div class="at-marcas"><span>0%</span><span>${faixas[0]}%</span><span>${faixas[1]}%</span><span>100%</span></div>
     ${linhas}
     ${r.devolvidos ? `<div class="at-linha"><span>devolvidos pela cor (fora da conta)</span><span>${r.devolvidos}</span></div>` : ""}`;
  el("atBackup").textContent = bate
    ? `Ao atualizar, “${tl.nome}” vai para De Lá Pra Cá › Backup, com data e hora, e nunca é apagada.`
    : "";
  el("atAplicar").disabled = !bate || rcOcupado;
}

async function executarAtualizar() {
  const u = atUltimo;
  if (!u || atOcupado()) return;
  if (!ponte() || typeof ponte().atualizar !== "function") {
    dizer("abra este painel pelo Resolve (e rode o ATUALIZAR.bat se ele for antigo)", false);
    return;
  }
  await reenviarPendentes();
  if (registrosPendentes().length) { dizer(AVISO_PENDENTE, false); return; }
  const marcada = (id) => !!(el(id) && el(id).checked);
  rcOcupado = true;
  travarEscolhas(true);
  el("atAplicar").disabled = true;
  let rotulo = "", detalhe = "", desde = Date.now();
  const mostrar = () => {
    const s = Math.floor((Date.now() - desde) / 1000);
    el("atMotivo").textContent = rotulo
      ? `${rotulo}${detalhe ? ` — ${detalhe}` : ""}${s >= 2 ? ` · ${s} s` : ""}` : "";
  };
  const passo = (t) => { rotulo = t; detalhe = ""; desde = Date.now(); mostrar(); };
  const relogio = setInterval(mostrar, 1000);
  const ouvir = typeof ponte().aoAndamento === "function";
  if (ouvir) ponte().aoAndamento((t) => { detalhe = String(t || ""); desde = Date.now(); mostrar(); });
  try {
    passo("1 de 4 · exportando a sua timeline de trabalho…");
    const t = conferirExportada(await ponte().exportarTrabalho({ uid: u.timeline.uid, nome: u.timeline.nome }), u.timeline);
    if (!t.ok) throw new Error(t.erro || "o Resolve não exportou a timeline de trabalho");
    passo("2 de 4 · aplicando as mudanças da montagem…");
    const p = await post("/receber/atualizar", {
      antiga: u.antiga, nova: u.nova, trabalho: t.drt, contagem: t.contagem, inicio: t.inicio,
      marcar: marcada("rcMarcar"), colorir: marcada("rcColorir") });
    passo("3 de 4 · trazendo a mídia dos planos novos…");
    if (p.inserir && p.inserir.length) {
      try { await ponte().midias({ drt: p.drt, nome: u.timeline.nome }); } catch (_) {  }
    }
    passo("4 de 4 · montando a timeline atualizada…");
    const r = await ponte().atualizar({
      drt: p.drt, esperado: p.esperado, antiga: { uid: u.timeline.uid, nome: t.nome || u.timeline.nome },
      nome: p.nome || u.nome, inserir: p.inserir, marcas: p.marcas, marcar: p.marcar, colorir: p.colorir });
    if (!r.ok) throw new Error(r.erro || "o Resolve não montou a timeline atualizada");
    const registrado = await registrar("/receber/atualizada", {
      nova: u.nova, substitui: u.antiga.partida || "", registro: p.registro || "",
      timeline: { uid: r.uid || "", nome: r.nome || "" } });
    const avisoRegistro = !registrado ? [AVISO_PENDENTE]
      : registrado.recusado ? [avisoRecusado(registrado.recusado)] : [];
    const avisos = avisoRegistro.concat(p.avisos || []).concat((r.faltaram || []).map((n) => `${n}: a mídia não entrou no pool — o plano não foi inserido`)).concat(r.conferir || []);
    el("atResumo").innerHTML =
      `<p class="previa-ok">✓ “${escapar(r.nome)}” atualizada. A anterior está em De Lá Pra Cá › Backup como “${escapar(r.backup)}”.</p>
       <div class="placar">
         <span class="pill ok">${Number(r.inseridos) || 0} plano(s) inserido(s)</span>
         ${r.marcadores ? `<span class="pill warn">${r.marcadores} marcador(es)</span>` : ""}
         ${r.cores ? `<span class="pill warn">${r.cores} plano(s) colorido(s)</span>` : ""}
         ${p.acoes && p.acoes.apagar ? `<span class="pill">${p.acoes.apagar} item(ns) saíram</span>` : ""}
         ${p.acoes && p.acoes.transicoes_removidas ? `<span class="pill warn">${p.acoes.transicoes_removidas} transição(ões) saíram</span>` : ""}
       </div>` + avisos.map((a) => `<div class="dica">⚠ ${escapar(a)}</div>`).join("");
    passo("");
    el("atBackup").textContent = "";
    atUltimo = null;
    await procurarNovos(true);
    try { await carregarTimelines({ uid: r.uid, nome: r.nome }); } catch (_) {  }
    oferecerNovasDoCanal();
    pintarOpcoes("nova", -1);
    await sugerirAntiga();
  } catch (e) {
    passo("");
    dizer(e.message, false);
    el("atAplicar").disabled = false;
  } finally {
    clearInterval(relogio);
    if (ouvir) ponte().aoAndamento(null);
    rcOcupado = false;
    travarEscolhas(false);
  }
}

const escolhaMudou = () => { atUltimo = null; el("rcAtualizar").hidden = true; };
el("atTrabalho").onchange = () => { escolhaMudou(); sugerirAntiga(); };
el("atAntiga").onchange = escolhaMudou;
el("atNova").onchange = escolhaMudou;
el("atBtnAntiga").onclick = () => escolherVersao("antiga");
el("atBtnNova").onclick = () => escolherVersao("nova");
el("atComparar").onclick = () => compararAtualizar();
el("atAplicar").onclick = () => executarAtualizar();
el("atAudio").onchange = () => { if (atUltimo) compararAtualizar(true); };
el("atFechar").onclick = () => { el("rcAtualizar").hidden = true; atUltimo = null; };
el("atComoNova").onclick = () => {
  const nova = versaoDe("nova");
  if (!nova || rcOcupado) return;
  const rotulo = (atOpcoes.nova[Number(el("atNova").value)] || {}).rotulo || "";
  el("rcAtualizar").hidden = true;
  atUltimo = null;
  irParaModo("aaf");
  if (nova.envio) trazer(nova.envio);
  else trazerPedido({ avulso: nova.avulso, mob_id: nova.mob_id || "" }, rotulo, null);
};


function tamanho(b) {
  const n = Number(b) || 0;
  const fmt = (x) => x.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  if (n >= 1e9) return `${fmt(n / 1e9)} GB`;
  if (n >= 1e6) return `${fmt(n / 1e6)} MB`;
  if (n >= 1e3) return `${fmt(n / 1e3)} KB`;
  return `${n} bytes`;
}

function textoUso(e) {
  const n = Number(e.arquivos) || 0;
  if (!n) return "Vazio. As conferências e as timelines geradas aparecem aqui conforme você usa o app.";
  return `${n.toLocaleString("pt-BR")} ${n === 1 ? "arquivo" : "arquivos"}: conferências e timelines geradas. `
    + "Apagar não perde trabalho: o app refaz quando precisar.";
}

function pintarUso(prefixo, e) {
  const max = (Number(e.max_gb) || 0) * 1024 ** 3;
  const frac = max ? Math.min(1, (Number(e.bytes) || 0) / max) : 0;
  el(prefixo + "UsoGrande").textContent = tamanho(e.bytes);
  el(prefixo + "UsoDe").textContent = `de ${e.max_gb} GB`;
  const barra = el(prefixo + "UsoBarra");
  barra.style.width = `${(frac * 100).toFixed(1)}%`;
  barra.className = frac >= 0.9 ? "cheio" : "";
}

function textoIndisponivel(e) {
  return e.indisponivel
    ? `O local escolhido (${e.escolhida}) não está acessível agora: o app está usando o padrão.` : "";
}

function pintarAntigo(e) {
  for (const alvo of document.querySelectorAll("[data-cache-antigo]")) {
    const a = e && e.antigo;
    alvo.hidden = !a;
    alvo.innerHTML = a
      ? `<p class="aviso"><b>${escapar(tamanho(a.bytes))} das versões anteriores</b> continuam no lugar antigo `
        + `(${Number(a.arquivos).toLocaleString("pt-BR")} ${Number(a.arquivos) === 1 ? "arquivo" : "arquivos"}). `
        + "O app não usa mais esse cache; nada se perdeu com a mudança.</p>"
        + `<div class="acoes"><button class="sm" data-antigo="apagar">Apagar o antigo</button>`
        + `<button class="sm ghost" data-antigo="manter">Manter</button></div>`
      : "";
  }
}

function pintarCache(e) {
  el("ajCacheDir").textContent = e.dir;
  el("ajCacheUso").textContent = textoUso(e);
  pintarUso("aj", e);
  const aviso = textoIndisponivel(e);
  el("ajCacheAviso").hidden = !aviso;
  el("ajCacheAviso").textContent = aviso;
  pintarAntigo(e);
}

let cfSalvo = null;
let cfDirNovo;

function cfMudouLocal() {
  return cfSalvo !== null && cfDirNovo !== undefined && cfDirNovo !== (cfSalvo.escolhida || null);
}

function pintarConfig(e) {
  if (e) { cfSalvo = e; cfDirNovo = undefined; el("cfTeto").value = e.max_gb; }
  if (!cfSalvo) return;
  const mudou = cfMudouLocal();
  el("cfDir").textContent = mudou ? (cfDirNovo || cfSalvo.padrao) : cfSalvo.dir;
  el("cfUso").textContent = mudou ? "Novo local: começa vazio. O cache atual não é movido."
    : textoUso(cfSalvo);
  pintarUso("cf", mudou ? { ...cfSalvo, bytes: 0, arquivos: 0 } : cfSalvo);
  el("cfLogs").textContent = cfSalvo.logs;
  el("cfLimpar").disabled = mudou;
  el("cfAbrir").disabled = mudou;
  el("cfPadrao").disabled = mudou ? cfDirNovo === null : !cfSalvo.escolhida;
  const aviso = mudou ? "Salve o novo local antes de limpar: limpar agora apagaria o do lugar anterior."
    : textoIndisponivel(cfSalvo);
  el("cfAviso").hidden = !aviso;
  el("cfAviso").textContent = aviso;
  pintarAntigo(cfSalvo);
}

async function carregarCache() {
  try {
    const e = await api("/cache");
    pintarCache(e);
    if (VISTA_CONFIG) pintarConfig(e);
  } catch (e) { dizer("Não consegui ler o cache: " + e.message, false); }
}

function depoisDoCache(r) {
  pintarCache(r);
  if (VISTA_CONFIG) pintarConfig(r);
}

async function limparCache(botao) {
  botao.disabled = true;
  try {
    const r = await post("/cache/limpar");
    depoisDoCache(r);
    dizer(`Cache limpo: ${tamanho(r.liberado.bytes)} liberados.`, true);
  } catch (e) { dizer(e.message, false); }
  finally { botao.disabled = VISTA_CONFIG && cfMudouLocal(); }
}

async function abrirPasta(qual) {
  try { await post("/pasta/abrir", { qual }); dizer(""); }
  catch (e) { dizer("Não consegui abrir a pasta: " + e.message, false); }
}

document.addEventListener("click", async (ev) => {
  const c = ev.target.closest("[data-cache]");
  if (c) {
    const acao = c.dataset.cache;
    if (acao === "abrir-cache") abrirPasta("cache");
    else if (acao === "abrir-logs") abrirPasta("logs");
    else if (acao === "limpar") limparCache(c);
    return;
  }
  const a = ev.target.closest("[data-antigo]");
  if (a) {
    a.disabled = true;
    try {
      const r = await post("/cache/antigo", { acao: a.dataset.antigo });
      depoisDoCache(r);
      dizer(r.liberado ? `Cache antigo apagado: ${tamanho(r.liberado.bytes)} liberados.` : "", true);
    } catch (e) { a.disabled = false; dizer(e.message, false); }
  }
});

el("cfEscolher").onclick = async () => {
  try {
    const r = await post("/escolher/pasta");
    if (!r.caminho) return;
    cfDirNovo = r.caminho;
    pintarConfig();
  } catch (e) { dizer(e.message, false); }
};
el("cfPadrao").onclick = () => { cfDirNovo = null; pintarConfig(); };
el("cfAbrir").onclick = () => abrirPasta("cache");
el("cfAbrirLogs").onclick = () => abrirPasta("logs");
el("cfLimpar").onclick = () => limparCache(el("cfLimpar"));
el("cfCancelar").onclick = () => { dizer(""); carregarCache(); if (versaoInfo) pintarVersao(versaoInfo); };
el("cfSalvar").onclick = async () => {
  const pedido = { max_gb: Number(el("cfTeto").value) };
  if (cfMudouLocal()) pedido.dir = cfDirNovo;
  el("cfSalvar").disabled = true;
  try {
    const r = await post("/cache/configurar", pedido);
    depoisDoCache(r);
    if (versaoInfo && el("cfAvisar").checked !== versaoInfo.avisar) {
      pintarVersao(await post("/versao/avisar", { ligado: el("cfAvisar").checked }));
    }
    dizer(Number(r.max_gb) !== pedido.max_gb
      ? `Salvo. O tamanho máximo vai de 1 a 2000 GB: ficou ${r.max_gb} GB.` : "Configurações salvas.", true);
  } catch (e) { dizer(e.message, false); }
  finally { el("cfSalvar").disabled = false; }
};


const ONDE_ABRE = {
  mc: "Abra o Media Composer: o painel fica em Tools › De Lá Pra Cá (ou Extensions, conforme a versão).",
  resolve: "Abra o Resolve: Workspace › Workflow Integrations › De Lá Pra Cá.",
};

function estadoDaIntegracao(i) {
  if (i.em_breve) return ["em breve", ""];
  if (i.instalado && i.desatualizado) return [`versão antiga · ${i.versao} (o app é ${i.versao_app})`, "warn"];
  if (i.instalado) return [`instalado · ${i.versao}`, "ok"];
  return [i.encontrado ? "não instalado" : "programa não encontrado", ""];
}

function pintarIntegracoes(r) {
  const alvo = el("cfIntegracoes");
  if (!alvo) return;
  alvo.innerHTML = r.integracoes.map((i) => {
    const [estado, classe] = estadoDaIntegracao(i);
    const travado = i.aberto || (!i.encontrado && !i.instalado);
    const principal = i.instalado ? (i.desatualizado ? "Atualizar" : "Reinstalar") : "Instalar";
    const botoes = i.em_breve ? "" : `<div class="acoes">
        <button class="sm${i.instalado && !i.desatualizado ? "" : " primary"}" data-integ="${escapar(i.id)}" data-acao="instalar"${travado ? " disabled" : ""}>${principal}</button>
        ${i.instalado ? `<button class="sm ghost" data-integ="${escapar(i.id)}" data-acao="remover"${i.aberto ? " disabled" : ""}>Remover</button>` : ""}
      </div>`;
    return `<div class="integracao">
      <div class="integracao-topo"><span class="integracao-nome">${escapar(i.nome)}</span>
        <span class="integracao-estado ${classe}">${escapar(estado)}</span></div>
      ${i.impedimento ? `<p class="dica">${escapar(i.impedimento)}</p>` : ""}
      ${botoes}</div>`;
  }).join("");
}

async function carregarIntegracoes() {
  try { pintarIntegracoes(await api("/integracoes")); }
  catch (e) { dizer("Não consegui ler as integrações: " + e.message, false); }
}

if (el("cfIntegracoes")) {
  el("cfIntegracoes").addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-integ]");
    if (!b) return;
    const id = b.dataset.integ, acao = b.dataset.acao;
    for (const x of el("cfIntegracoes").querySelectorAll("button")) x.disabled = true;
    dizer(acao === "instalar" ? "Instalando…" : "Removendo…", true);
    try {
      const r = await post("/integracoes/acao", { id, acao });
      pintarIntegracoes(r);
      const nome = (r.integracoes.find((i) => i.id === id) || {}).nome || "";
      dizer(acao === "instalar" ? `Plugin do ${nome} instalado. ${ONDE_ABRE[id] || ""}`
        : `Plugin do ${nome} removido.`, true);
    } catch (e) {
      dizer(e.message, false);
      if (e.corpo && e.corpo.integracoes) pintarIntegracoes(e.corpo); else carregarIntegracoes();
    }
  });
}

async function avisarPluginAntigo() {
  try {
    const r = await api("/integracoes");
    const i = r.integracoes.find((x) => x.id === NLE);
    const aviso = el("avisoPlugin");
    aviso.hidden = !(i && i.desatualizado);
    if (i && i.desatualizado) {
      aviso.textContent = `O plugin do De Lá Pra Cá neste programa é da versão ${i.versao}, e o app é ${i.versao_app}. `
        + "Atualize o plugin pelo app (⚙ Configurações › Integrações) com este programa fechado.";
    }
  } catch (_) {  }
}

if (VISTA_CONFIG) {
  carregarIntegracoes();
  setInterval(() => { if (!document.hidden) carregarIntegracoes(); }, 3000);
} else if (!VISTA_REVISAO) {
  avisarPluginAntigo();
}


let versaoInfo = null;
const TAG_VERSAO = { em_dia: "em dia", erro: "não consegui conferir", desligado: "aviso desligado" };

function pintarVersao(v) {
  versaoInfo = v;
  const nova = v.estado === "nova";
  const link = el("versaoNovaLink");
  link.hidden = !nova;
  if (nova) link.textContent = `versão nova: ${v.versao}`;
  if (!VISTA_CONFIG) return;
  el("cfVersaoAtual").textContent = `Você está na ${v.atual}`;
  const tag = el("cfVersaoTag");
  tag.className = `ver-tag ${v.estado}`;
  tag.textContent = nova ? `${v.versao} disponível` : (TAG_VERSAO[v.estado] || "");
  const texto = v.estado === "erro" ? "Sem internet ou o GitHub não respondeu. Tento de novo daqui a pouco." : "";
  el("cfVersaoTexto").hidden = !texto;
  el("cfVersaoTexto").textContent = texto;
  el("cfBaixar").hidden = !nova;
  if (nova) el("cfBaixar").textContent = `Baixar a ${v.versao}`;
  el("cfConferirVersao").hidden = v.estado !== "erro";
  el("cfVersaoAcoes").hidden = el("cfBaixar").hidden && el("cfConferirVersao").hidden;
  el("cfAvisar").checked = !!v.avisar;
}

async function carregarVersao(forcar) {
  try { pintarVersao(forcar ? await post("/versao/conferir") : await api("/versao")); }
  catch (_) {  }
}

async function baixarVersao() {
  try { await post("/versao/baixar"); dizer(""); }
  catch (e) { dizer("Não consegui abrir a página da versão: " + e.message, false); }
}

el("versaoNovaLink").onclick = baixarVersao;
el("cfBaixar").onclick = baixarVersao;
el("cfConferirVersao").onclick = () => carregarVersao(true);
if (!VISTA_REVISAO) {
  carregarVersao(false);
  setInterval(() => { if (!document.hidden) carregarVersao(false); }, 30 * 60 * 1000);
}
