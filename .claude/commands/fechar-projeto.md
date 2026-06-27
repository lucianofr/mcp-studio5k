---
description: Fecha o projeto ControlLogix/CompactLogix atualmente aberto via MCP
argument-hint: (sem argumento — fecha o projeto aberto)
---

Feche o projeto Studio 5000 ativo usando a ferramenta `close_project` do MCP server `mcp-studio5k`.

Argumento recebido: `$ARGUMENTS`

Atenção: fechar **descarta edições não salvas** (não há save implícito). Se houver trabalho não salvo, salve antes (`save_project`).

Passos:

1. `close_project` não recebe parâmetros — fecha sempre o projeto atualmente aberto na sessão (é no-op se nenhum estiver aberto). Ignore `$ARGUMENTS`; se o usuário passou um nome esperando fechar outro projeto, avise que só há um projeto por sessão e que esta ação fecha o que está aberto.
2. Chame a ferramenta MCP `close_project` (sem argumentos).
3. Interprete o envelope:
   - Sucesso (`ok: true`): confirme `✅ Projeto fechado`.
   - Erro (`ok: false`): mostre a mensagem exata.
