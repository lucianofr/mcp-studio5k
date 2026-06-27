---
description: Abre um projeto ControlLogix/CompactLogix (.ACD) via MCP pelo nome do arquivo
argument-hint: [nome-do-arquivo.ACD]
---

Abra o projeto Studio 5000 usando a ferramenta `open_project` do MCP server `mcp-studio5k`.

Argumento recebido: `$ARGUMENTS`

Passos:

1. Se `$ARGUMENTS` estiver vazio, **pare** e peça ao usuário o nome do arquivo `.ACD` antes de prosseguir. Não chame a ferramenta sem caminho.
2. Chame a ferramenta MCP `open_project` com `path` = `$ARGUMENTS` (pode ser nome relativo ao `MCP_S5K_PROJECT_ROOT` ou caminho absoluto `.ACD` dentro do root).
3. Interprete o envelope retornado:
   - Sucesso (`ok: true`): confirme `✅ Projeto $ARGUMENTS aberto` e mostre o campo `opened` retornado.
   - Erro/recusa (`ok: false`): mostre a mensagem exata. Se for `a project is already open; close it first`, avise o usuário para rodar `/fechar-projeto` antes de abrir outro.

Não invente caminhos nem feche o projeto atual sem o usuário pedir.
