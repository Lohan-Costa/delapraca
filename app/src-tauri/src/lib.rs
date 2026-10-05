use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, State, WebviewUrl, WebviewWindowBuilder};

const PORTA: u16 = 7823;

const JANELA_PRINCIPAL: &str = "main";

const JANELA_REVISAO: &str = "revisao";

const JANELA_CONFIG: &str = "configuracoes";

const ARG_REVISAO: &str = "--revisao";

const ARG_FECHAR_REVISAO: &str = "--fechar-revisao";

const AUTOR_URL: &str = "https://www.linkedin.com/in/lohan-costa/";

#[derive(Default)]
struct Servico(Mutex<Option<Child>>);

#[derive(Serialize)]
struct EstadoServico {
    rodando: bool,
    pid: Option<u32>,
    porta: u16,
    de_outro: bool,
}

fn porta_ocupada() -> bool {
    use std::net::{Ipv4Addr, SocketAddr, TcpStream};
    use std::time::Duration;

    let alvo = SocketAddr::from((Ipv4Addr::LOCALHOST, PORTA));
    TcpStream::connect_timeout(&alvo, Duration::from_millis(250)).is_ok()
}

fn pasta_dados() -> Option<PathBuf> {
    Some(if cfg!(windows) {
        PathBuf::from(std::env::var_os("LOCALAPPDATA")?).join("DeLaPraCa")
    } else {
        PathBuf::from(std::env::var_os("HOME")?).join("Library/Application Support/DeLaPraCa")
    })
}

fn convite_pendente() -> bool {
    let Some(arq) = pasta_dados().map(|p| p.join("configuracoes.json")) else { return true };
    match std::fs::read_to_string(arq) {
        Ok(t) => serde_json::from_str::<serde_json::Value>(&t)
            .map(|v| v.get("convite_integracoes_visto") != Some(&serde_json::Value::Bool(true)))
            .unwrap_or(true),
        Err(_) => true,
    }
}

fn token_do_motor() -> Option<String> {
    let t = std::fs::read_to_string(pasta_dados()?.join("token")).ok()?;
    let t = t.trim().to_string();
    if !t.is_empty() && t.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(t)
    } else {
        None
    }
}

fn pedir_a_porta() -> bool {
    use std::io::{Read, Write};
    use std::net::{Ipv4Addr, SocketAddr, TcpStream};
    use std::time::Duration;

    let Some(tk) = token_do_motor() else { return false };
    let alvo = SocketAddr::from((Ipv4Addr::LOCALHOST, PORTA));
    let Ok(mut s) = TcpStream::connect_timeout(&alvo, Duration::from_millis(500)) else { return false };
    let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
    let pedido = format!(
        "POST /receber/motor/ceder HTTP/1.1\r\nHost: 127.0.0.1:{PORTA}\r\nX-DeLaPraCa-Token: {tk}\r\n\
         Content-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if s.write_all(pedido.as_bytes()).is_err() {
        return false;
    }
    let mut resposta = String::new();
    let _ = s.read_to_string(&mut resposta);
    resposta.starts_with("HTTP/1.1 200") || resposta.starts_with("HTTP/1.0 200")
}

fn comando_do_servico() -> Option<(PathBuf, Vec<String>)> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?.to_path_buf();

    let nome = if cfg!(windows) { "delapraca-servico.exe" } else { "delapraca-servico" };
    let usar_montado =
        !cfg!(debug_assertions) || std::env::var_os("DLPC_MOTOR_MONTADO").is_some_and(|v| v == "1");
    if usar_montado {
        let mut candidatos = vec![dir.join("motor").join(nome), dir.join(nome)];
        if let Some(conteudo) = dir.parent() {
            candidatos.push(conteudo.join("Resources").join("motor").join(nome));
        }
        candidatos.push(dir.join("..").join("..").join("motor").join(nome));
        if let Some(motor) = candidatos.into_iter().find(|p| p.is_file()) {
            return Some((motor, vec![]));
        }
    }

    let mut raiz = dir.clone();
    for _ in 0..6 {
        let servico = raiz.join("plugins/media-composer/servico/main.py");
        if servico.is_file() {
            let py = if cfg!(windows) {
                raiz.join(".venv-win/Scripts/python.exe")
            } else {
                raiz.join(".venv/bin/python")
            };
            if py.is_file() {
                return Some((py, vec![servico.to_string_lossy().into_owned()]));
            }
        }
        raiz = raiz.parent()?.to_path_buf();
    }
    None
}

fn sem_janela(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }
    let _ = cmd;
}

#[tauri::command]
fn estado_do_servico(servico: State<Servico>) -> EstadoServico {
    let mut guarda = servico.0.lock().unwrap();
    if let Some(filho) = guarda.as_mut() {
        match filho.try_wait() {
            Ok(None) => {
                return EstadoServico {
                    rodando: true,
                    pid: Some(filho.id()),
                    porta: PORTA,
                    de_outro: false,
                }
            }
            _ => {
                *guarda = None;
            }
        }
    }
    EstadoServico {
        rodando: false,
        pid: None,
        porta: PORTA,
        de_outro: porta_ocupada(),
    }
}

#[tauri::command]
fn iniciar_servico(servico: State<Servico>) -> Result<EstadoServico, String> {
    {
        let mut guarda = servico.0.lock().unwrap();
        if let Some(filho) = guarda.as_mut() {
            if matches!(filho.try_wait(), Ok(None)) {
                return Ok(EstadoServico {
                    rodando: true,
                    pid: Some(filho.id()),
                    porta: PORTA,
                    de_outro: false,
                });
            }
        }
        if porta_ocupada() && pedir_a_porta() {
            for _ in 0..25 {
                if !porta_ocupada() {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
        }
        if porta_ocupada() {
            return Ok(EstadoServico {
                rodando: false,
                pid: None,
                porta: PORTA,
                de_outro: true,
            });
        }
        let (prog, mut args) = comando_do_servico()
            .ok_or_else(|| "não achei o serviço para iniciar".to_string())?;

        if let Ok(eu) = std::env::current_exe() {
            args.push("--app".into());
            args.push(eu.to_string_lossy().into_owned());
            args.push("--pai".into());
            args.push(std::process::id().to_string());
        }

        let mut cmd = Command::new(prog);
        cmd.args(args);
        sem_janela(&mut cmd);
        let filho = cmd.spawn().map_err(|e| format!("não consegui iniciar: {e}"))?;
        *guarda = Some(filho);
    }
    Ok(estado_do_servico(servico))
}

fn abrir_revisao(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(j) = app.get_webview_window(JANELA_REVISAO) {
        let _ = j.unminimize();
        let _ = j.show();
        let _ = j.set_focus();
        return Ok(());
    }

    if !porta_ocupada() {
        return Err("o serviço não está no ar — inicie-o antes de revisar".into());
    }

    let endereco = format!("http://127.0.0.1:{PORTA}/painel?vista=revisao");
    let url: tauri::Url = endereco
        .parse()
        .map_err(|e| format!("endereço inválido: {e}"))?;

    WebviewWindowBuilder::new(app, JANELA_REVISAO, WebviewUrl::External(url))
        .title("De Lá Pra Cá — Revisar mídias")
        .inner_size(1100.0, 700.0)
        .min_inner_size(760.0, 420.0)
        .resizable(true)
        .center()
        .build()
        .map_err(|e| format!("não consegui abrir a janela de revisão: {e}"))?;
    Ok(())
}

fn abrir_configuracoes(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(j) = app.get_webview_window(JANELA_CONFIG) {
        let _ = j.unminimize();
        let _ = j.show();
        let _ = j.set_focus();
        return Ok(());
    }
    if !porta_ocupada() {
        return Err("o serviço não está no ar — inicie-o antes de abrir as configurações".into());
    }
    let endereco = format!("http://127.0.0.1:{PORTA}/painel?vista=configuracoes");
    let url: tauri::Url = endereco
        .parse()
        .map_err(|e| format!("endereço inválido: {e}"))?;
    WebviewWindowBuilder::new(app, JANELA_CONFIG, WebviewUrl::External(url))
        .title("De Lá Pra Cá — Configurações")
        .inner_size(520.0, 640.0)
        .min_inner_size(400.0, 480.0)
        .resizable(true)
        .center()
        .build()
        .map_err(|e| format!("não consegui abrir as configurações: {e}"))?;
    Ok(())
}

#[tauri::command]
async fn configuracoes(app: tauri::AppHandle) -> Result<(), String> {
    abrir_configuracoes(&app)
}

#[tauri::command]
async fn revisar(app: tauri::AppHandle) -> Result<(), String> {
    abrir_revisao(&app)
}

#[tauri::command]
fn liberar_porta() -> Result<serde_json::Value, String> {
    let (prog, mut args) =
        comando_do_servico().ok_or_else(|| "não achei o serviço".to_string())?;
    args.push("--liberar-porta".into());
    let mut cmd = Command::new(prog);
    cmd.args(args);
    sem_janela(&mut cmd);
    let saida = cmd.output().map_err(|e| format!("falhou: {e}"))?;
    let texto = String::from_utf8_lossy(&saida.stdout);
    let ultima = texto.lines().rev().find(|l| l.trim_start().starts_with('{'));
    match ultima {
        Some(l) => serde_json::from_str(l).map_err(|e| format!("resposta ilegível: {e}")),
        None => Err(format!(
            "o serviço não respondeu em JSON: {}",
            String::from_utf8_lossy(&saida.stderr).trim()
        )),
    }
}

#[tauri::command]
fn abrir_autor(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;

    app.opener()
        .open_url(AUTOR_URL, None::<&str>)
        .map_err(|e| format!("não consegui abrir o navegador: {e}"))
}

#[tauri::command]
fn encerrar(app: tauri::AppHandle, servico: State<Servico>) {
    parar(&servico);
    app.exit(0);
}

fn parar(servico: &State<Servico>) {
    if let Some(mut filho) = servico.0.lock().unwrap().take() {
        let _ = filho.kill();
        let _ = filho.wait();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if args.iter().any(|a| a == ARG_FECHAR_REVISAO) {
                if let Some(j) = app.get_webview_window(JANELA_REVISAO) {
                    let _ = j.close();
                }
                if let Some(j) = app.get_webview_window(JANELA_PRINCIPAL) {
                    let _ = j.set_focus();
                }
                return;
            }
            if args.iter().any(|a| a == ARG_REVISAO) {
                if let Err(e) = abrir_revisao(app) {
                    eprintln!("não consegui abrir a revisão: {e}");
                }
                return;
            }
            if let Some(j) = app.get_webview_window(JANELA_PRINCIPAL) {
                let _ = j.set_focus();
            }
        }))
        .manage(Servico::default())
        .setup(|app| {
            let estado = app.state::<Servico>();
            if let Err(e) = iniciar_servico(estado) {
                eprintln!("não consegui iniciar o serviço: {e}");
            }
            if convite_pendente() {
                let app2 = app.handle().clone();
                std::thread::spawn(move || {
                    for _ in 0..100 {
                        if porta_ocupada() {
                            if !convite_pendente() {
                                return;
                            }
                            if let Err(e) = abrir_configuracoes(&app2) {
                                eprintln!("não consegui abrir as configurações: {e}");
                            }
                            return;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(200));
                    }
                });
            }
            if std::env::args().any(|a| a == ARG_REVISAO) {
                if let Err(e) = abrir_revisao(app.handle()) {
                    eprintln!("não consegui abrir a revisão: {e}");
                }
            }
            Ok(())
        })
        .on_window_event(|janela, evento| {
            if let tauri::WindowEvent::Destroyed = evento {
                if janela.label() == JANELA_PRINCIPAL {
                    parar(&janela.state::<Servico>());
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            estado_do_servico,
            iniciar_servico,
            liberar_porta,
            abrir_autor,
            revisar,
            configuracoes,
            encerrar
        ])
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o De Lá Pra Cá");
}
