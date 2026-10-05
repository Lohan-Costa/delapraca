const el = (id) => document.getElementById(id);

const ponte = window.__TAURI__ && window.__TAURI__.core;
const invoke = ponte ? ponte.invoke : null;

function dizer(texto, tipo) {
  const m = el("msg");
  m.textContent = texto || "";
  m.className = "msg" + (tipo ? " " + tipo : "");
}

function pintar(e) {
  const cartao = el("cartao");
  const rodando = !!(e && e.rodando);
  const deOutro = !rodando && !!(e && e.de_outro);
  cartao.className = "estado " + (rodando || deOutro ? "ligado" : "parado");
  el("estadoTitulo").textContent = rodando ? "no ar"
    : deOutro ? "no ar (fora deste app)" : "parado";
  el("estadoDetalhe").textContent = rodando
    ? `porta ${e.porta} · processo ${e.pid}`
    : deOutro
      ? `já há um serviço na porta ${e.porta} — encerrar aqui não desliga aquele`
      : "o painel do Media Composer não vai responder";
  el("btnEncerrar").textContent = rodando ? "Encerrar" : "Fechar";
}

async function atualizar() {
  if (!invoke) {
    pintar({ rodando: false });
    dizer("a ponte com o aplicativo não carregou — falta `withGlobalTauri: true` "
          + "no tauri.conf.json", "err");
    el("btnLiberar").disabled = true;
    el("btnRevisar").disabled = true;
    el("btnConfig").disabled = true;
    return;
  }
  try {
    pintar(await invoke("estado_do_servico"));
  } catch (e) {
    pintar({ rodando: false });
    dizer(String(e), "err");
  }
}

el("btnLiberar").onclick = async () => {
  const b = el("btnLiberar");
  b.disabled = true;
  dizer("procurando o processo que segura as portas…");
  try {
    const r = await invoke("liberar_porta");
    if (!r.alvos.length) {
      dizer("nenhum processo preso — as portas já estão livres.", "ok");
    } else if (r.restantes.length) {
      dizer(`ainda há processo segurando as portas (${r.restantes.join(", ")}). `
            + "Feche o Media Composer e tente de novo.", "err");
    } else if (r.mc_aberto) {
      dizer(`liberei ${r.mortos.length} processo(s). ⚠️ O Media Composer está ABERTO — `
            + "feche e reabra para o painel voltar ao menu.", "ok");
    } else {
      dizer(`pronto: ${r.mortos.length} processo(s) liberados. Pode abrir o Avid.`, "ok");
    }
  } catch (e) {
    dizer(String(e), "err");
  } finally {
    b.disabled = false;
  }
};

el("btnRevisar").onclick = async () => {
  const b = el("btnRevisar");
  b.disabled = true;
  try {
    await invoke("revisar");
    dizer("");
  } catch (e) {
    dizer(String(e), "err");
  } finally {
    b.disabled = false;
  }
};

el("btnConfig").onclick = async () => {
  const b = el("btnConfig");
  b.disabled = true;
  try {
    await invoke("configuracoes");
    dizer("");
  } catch (e) {
    dizer(String(e), "err");
  } finally {
    b.disabled = false;
  }
};

el("btnEncerrar").onclick = async () => {
  dizer("encerrando o serviço…");
  try {
    await invoke("encerrar");
  } catch (e) {
    dizer(String(e), "err");
  }
};

el("autor").onclick = () => { if (invoke) void invoke("abrir_autor"); };

async function conferirVersao() {
  if (!invoke) return;
  try {
    const v = await invoke("versao_nova");
    el("versaoNova").hidden = !v;
    if (v) el("versaoNovaNumero").textContent = v;
  } catch (_) { el("versaoNova").hidden = true; }
}
el("btnBaixar").onclick = async () => {
  try { await invoke("baixar_versao_nova"); dizer(""); }
  catch (e) { dizer(String(e), "err"); }
};
conferirVersao();
setInterval(conferirVersao, 60000);

atualizar();
setInterval(atualizar, 2000);
