---
description: Abre um projeto ControlLogix/CompactLogix (.ACD) via MCP pelo nome do arquivo
---

Abra o projeto Studio 5000 no MCP server `mcp-studio5k`.

Argumento recebido: `$ARGUMENTS`

O server `mcp-studio5k` lê o projeto a partir de variáveis de ambiente fixadas no
spawn (`MCP_S5K_PROJECT_ROOT` limita os caminhos; `MCP_S5K_PROJECT_FILE` é aberto no
boot). Um server já em execução **não** pode trocar essas variáveis sozinho — por
isso este comando primeiro grava a config e depois abre.

Passos:

1. Se `$ARGUMENTS` estiver vazio, **pare** e peça o caminho do `.ACD`. Não prossiga sem caminho.

2. **Preflight — fixar a env e validar o arquivo.** Rode:

   ```
   .venv/Scripts/python.exe scripts/set_project_env.py "$ARGUMENTS"
   ```

   Isso valida que o `.ACD` existe, define `MCP_S5K_PROJECT_ROOT` (pasta do `.ACD`),
   `MCP_S5K_PROJECT_FILE` (o `.ACD`) e `MCP_S5K_BACKUP_DIR` em `~/.claude.json`,
   preservando salt/read-only/limites, e faz backup em `~/.claude.json.bak`.
   - Saída `ok: false` → mostre o `error` exato e **pare**.
   - Saída `ok: true` → siga.

3. **Abrir.** Verifique se as ferramentas `mcp__mcp-studio5k__*` estão disponíveis nesta sessão:

   - **Server conectado:** chame `open_project` com `path` = `$ARGUMENTS`.
     - `ok: true` → confirme `✅ Projeto $ARGUMENTS aberto` e mostre `opened`.
     - `ok: false`:
       - `a project is already open; close it first` → o projeto provavelmente já
         abriu no boot via `MCP_S5K_PROJECT_FILE`. Confirme que é o `.ACD` certo; se
         for, está aberto. Se for outro, avise para rodar `/fechar-projeto` antes.
       - `path escapes PROJECT_ROOT` → o server ainda roda com o root antigo. Vá ao
         passo 4 (reconectar) para o novo root valer.
       - qualquer outro erro → mostre a mensagem exata.

   - **Server NÃO conectado** (nenhuma ferramenta `mcp__mcp-studio5k__*` nesta sessão):
     a env já foi gravada no passo 2. Instrua o usuário a **reconectar**: rode `/mcp`
     → reconecte `mcp-studio5k` (ou reinicie o Claude Code). No reconnect o server
     sobe com o root novo e **auto-abre** o `.ACD`. Depois, rode `/abrir-projeto`
     de novo para confirmar.

Não invente caminhos. Não feche o projeto atual sem o usuário pedir. Não edite
`~/.claude.json` à mão — use sempre o script do passo 2.
