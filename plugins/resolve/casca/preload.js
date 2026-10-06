"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("delapraca", {
  nle: "resolve",
  importar: (pedido) => ipcRenderer.invoke("dlpc:importar", pedido),
  midias: (pedido) => ipcRenderer.invoke("dlpc:midias", pedido),
  projeto: () => ipcRenderer.invoke("dlpc:projeto"),
  timelines: () => ipcRenderer.invoke("dlpc:timelines"),
  exportarTrabalho: (pedido) => ipcRenderer.invoke("dlpc:exportarTrabalho", pedido),
  atualizar: (pedido) => ipcRenderer.invoke("dlpc:atualizar", pedido),
  fusion: (pedido) => ipcRenderer.invoke("dlpc:fusion", pedido),
  compostos: (pedido) => ipcRenderer.invoke("dlpc:compostos", pedido),
  conferirInicio: (pedido) => ipcRenderer.invoke("dlpc:conferirInicio", pedido),
  conferirQuadros: (pedido) => ipcRenderer.invoke("dlpc:conferirQuadros", pedido),
  conferirFim: (pedido) => ipcRenderer.invoke("dlpc:conferirFim", pedido),
  aoAndamento: (cb) => {
    ipcRenderer.removeAllListeners("dlpc:andamento");
    if (typeof cb === "function") ipcRenderer.on("dlpc:andamento", (_e, d) => cb(d && d.texto));
  },
  religar: () => ipcRenderer.invoke("dlpc:religar"),
  abrirApp: () => ipcRenderer.invoke("dlpc:abrirApp"),
  esperarMotor: () => ipcRenderer.invoke("dlpc:esperarMotor"),
  caminhoDoArquivo: (arquivo) => {
    try {
      if (webUtils && typeof webUtils.getPathForFile === "function") return webUtils.getPathForFile(arquivo) || "";
      return (arquivo && arquivo.path) || "";
    } catch (_) { return ""; }
  },
  abrirRevisao: () => ipcRenderer.invoke("dlpc:revisao"),
  fecharJanela: () => ipcRenderer.invoke("dlpc:fechar"),
});
