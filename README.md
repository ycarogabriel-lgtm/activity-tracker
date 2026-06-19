# Activity Tracker

Rastreador automático de atividades para facilitar o apontamento de horas. Registra janela ativa, reuniões e chats do Teams, e abas do navegador a cada 10 segundos. Dashboard com navegação por semana e exportação CSV.

---

## Download

Baixe a versão mais recente em [**Releases**](https://github.com/ycarogabriel-lgtm/activity-tracker/releases/latest):


---

## macOS

### Primeira abertura

O macOS bloqueia apps não assinados pela App Store. Na primeira vez:

1. Tente abrir o `ActivityTracker.app` com duplo clique
2. Aparecerá um aviso dizendo que o app não pode ser aberto — clique **OK** ou **Concluído**
3. Vá em **Configurações do Sistema → Privacidade e Segurança**
4. Role para baixo até encontrar a mensagem _"ActivityTracker foi bloqueado"_ e clique em **Abrir Mesmo Assim**
5. Confirme com sua senha ou Touch ID

A partir daí o app abre normalmente com duplo clique.

> **macOS Sequoia (15+):** o método de botão direito → Abrir foi removido. O único fluxo é pelo menu Privacidade e Segurança acima.

### Alternativa via Terminal (abre sem nenhum aviso)

Entre na pasta onde o app está o seu executável pelo Terminal. Se estiver na sua pasta de Downloads, insira o comando:

```bash
cd ~/downloads
```
Logo depois, execute uma vez:
```bash
xattr -cr ActivityTracker.app
codesign --force --deep --sign - ActivityTracker.app
```

Depois é só dar duplo clique normalmente.

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
