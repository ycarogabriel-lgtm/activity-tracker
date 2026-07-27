# Activity Tracker

Rastreador automático de atividades para facilitar o apontamento de horas. Registra janela ativa, reuniões e chats do Teams, e abas do navegador a cada 10 segundos. Dashboard com navegação por semana e exportação CSV.

---

## Download

Baixe a versão mais recente em [**Releases**](https://github.com/ycarogabriel-lgtm/activity-tracker/releases/latest):


---

## macOS

### Instalação

1. Baixe o `ActivityTracker-macOS.dmg` na página de [Releases](https://github.com/ycarogabriel-lgtm/activity-tracker/releases/latest)
2. Abra o `.dmg` e arraste o **ActivityTracker** pra pasta **Applications**
3. Abra o app pela pasta Applications (não pela janela do `.dmg`)

### Primeira abertura

O `.dmg` já sai assinado (ad-hoc, sem certificado de Developer ID — isso exigiria conta paga da Apple) no build, mas isso é assinado na máquina que compilou, não na sua — o Gatekeeper do macOS ainda bloqueia o app na primeira vez porque ele foi baixado da internet. A assinatura que resolve isso de verdade é feita **no seu próprio Mac**, depois que o app já está na pasta Applications. Abra o Terminal e cole:

```bash
xattr -cr /Applications/ActivityTracker.app
codesign --force --deep --sign - /Applications/ActivityTracker.app
```

Depois é só abrir com duplo clique normalmente, sem nenhum aviso.

<details>
<summary>Prefere sem usar o Terminal?</summary>

1. Tente abrir o `ActivityTracker.app` com duplo clique
2. Aparecerá um aviso dizendo que o app não pode ser aberto — clique **OK** ou **Concluído**
3. Vá em **Configurações do Sistema → Privacidade e Segurança**
4. Role para baixo até encontrar a mensagem _"ActivityTracker foi bloqueado"_ e clique em **Abrir Mesmo Assim**
5. Confirme com sua senha ou Touch ID

> **macOS Sequoia (15+):** o método de botão direito → Abrir foi removido. O único fluxo é pelo menu Privacidade e Segurança acima.

</details>

Na primeira vez que o app tentar ler a janela ativa, o macOS também vai pedir permissão de **Acessibilidade** — clique em **Permitir**. Isso só é pedido uma vez por instalação (não precisa repetir a cada abertura).

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
