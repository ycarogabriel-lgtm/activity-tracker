# Activity Tracker

Rastreador automático de atividades para facilitar o apontamento de horas. Registra janela ativa, reuniões e chats do Teams, e abas do navegador a cada 10 segundos. Dashboard com navegação por semana e exportação CSV.

---

## Download

Baixe a versão mais recente em [**Releases → Actions → Build Activity Tracker**](https://github.com/ycarogabriel-lgtm/activity-tracker/actions):

| Sistema | Arquivo |
|---------|---------|
| macOS | `ActivityTracker-macOS.zip` → extrair → `ActivityTracker.app` |
| Windows | `ActivityTracker-Windows.zip` → extrair → `ActivityTracker.exe` |

---

## macOS

### Abrir o dashboard

1. Extraia o zip — você terá `ActivityTracker.app`
2. Duplo clique para abrir
3. Se aparecer aviso de segurança: clique com botão direito → **Abrir** → **Abrir**

O app rastreia e exibe os dados enquanto estiver aberto.

### Rodar em background (recomendado)

Para rastrear mesmo com o app fechado, instale o daemon como serviço de login:

```bash
# Na pasta do projeto (código-fonte):
chmod +x install_mac_daemon.sh
./install_mac_daemon.sh
```

O tracker passa a iniciar automaticamente no login e fica sempre rodando em segundo plano. Abra o `ActivityTracker.app` apenas quando quiser ver o dashboard.

Para desinstalar:
```bash
./install_mac_daemon.sh --uninstall
```

**Onde ficam os dados:** `~/Library/Application Support/ActivityTracker/activity_log.json`

---

## Windows

### Abrir o dashboard

1. Extraia o zip — você terá `ActivityTracker.exe`
2. Execute `ActivityTracker.exe`

### Iniciar automaticamente no login

Execute `REGISTRAR_INICIO_AUTOMATICO.bat` como administrador. O tracker passará a iniciar em segundo plano a cada login via Agendador de Tarefas.

Para iniciar manualmente sem abrir console: execute `INICIAR_SILENCIOSO.vbs`.

**Onde ficam os dados:** mesma pasta do executável (`activity_log.json`)

---

## Navegação no dashboard

- **‹ ›** — navega entre semanas com dados (pula semanas vazias automaticamente)
- Dias sem registro ficam desabilitados
- **Atualizar** — recarrega os dados manualmente
- **Exportar CSV** — exporta as atividades do dia selecionado (ou todas)

---

## Rodando pelo código-fonte

```bash
# Instalar dependências
pip install pywebview psutil

# macOS
pip install pywebview psutil
python3 start.py

# Windows
pip install pywebview psutil pywin32
python start.py
```

### Build do executável

```bash
python3 build.py
# macOS → dist/ActivityTracker.app
# Windows → dist/ActivityTracker.exe
```

Requer `brew install librsvg` no macOS para gerar o ícone.

---

## Dados e privacidade

Todos os dados ficam **localmente** na sua máquina. Nenhuma informação é enviada para a internet.

- macOS: `~/Library/Application Support/ActivityTracker/`
- Windows: mesma pasta do executável

O arquivo `activity_log.json` mantém os últimos **5.000 registros**.
