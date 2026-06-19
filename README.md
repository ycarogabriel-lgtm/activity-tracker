# Activity Tracker

Rastreador automático de atividades para facilitar o apontamento de horas. Registra janela ativa, reuniões e chats do Teams, e abas do navegador a cada 10 segundos. Dashboard com navegação por semana e exportação CSV.

---

## Download

Baixe a versão mais recente em [**Releases**](https://github.com/ycarogabriel-lgtm/activity-tracker/releases/latest):

| Sistema | Arquivo |
|---------|---------|
| macOS | `ActivityTracker-macOS.zip` → extrair → `ActivityTracker.app` |
| Windows | `ActivityTracker-Windows.exe` |

---

## macOS

### Primeira abertura

Clique com botão direito no `ActivityTracker.app` → **Abrir** → **Abrir**.

> Isso é necessário apenas na primeira vez. O macOS exige essa confirmação para apps não assinados pela Apple Store.

### Assinar localmente (elimina o aviso de segurança)

Para remover o aviso de vez, rode uma vez no Terminal após extrair o app:

```bash
xattr -cr ActivityTracker.app
codesign --force --deep --sign - ActivityTracker.app
```

Depois abra normalmente com duplo clique.

### Rastrear em segundo plano

Acesse **⚙ Configurações** (ícone de engrenagem no canto superior direito do app) e ative **"Rastrear em segundo plano"**.

Com isso o tracker inicia automaticamente no login e continua rodando mesmo com o app fechado. Abra o app sempre que quiser ver o histórico — os dados estarão lá.

**Onde ficam os dados:** `~/Library/Application Support/ActivityTracker/`

---

## Windows

Execute `ActivityTracker-Windows.exe`.

Para rastrear em segundo plano, acesse **⚙ Configurações** e ative **"Rastrear em segundo plano"**. O tracker será registrado para iniciar automaticamente no login.

---

## Navegação

- **‹ ›** — navega entre semanas (pula semanas vazias automaticamente)
- Dias sem registro ficam desabilitados
- **Atualizar** — recarrega os dados
- **Exportar CSV** — exporta as atividades do dia selecionado (ou todas)

---

## Build pelo código-fonte

```bash
# Dependências
pip install pywebview psutil          # macOS
pip install pywebview psutil pywin32  # Windows

# Rodar em modo desenvolvimento
python3 start.py

# Gerar executável
python3 build.py
# → dist/ActivityTracker.app  (macOS)
# → dist/ActivityTracker.exe  (Windows)
```

Requer `brew install librsvg` no macOS para gerar o ícone.

---

## Dados e privacidade

Todos os dados ficam **localmente** na sua máquina. Nenhuma informação é enviada para a internet.
