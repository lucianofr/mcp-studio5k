# mcp-studio5k

MCP local para autoria e operação de projetos Rockwell Studio 5000 (ControlLogix / CompactLogix, Logix Designer v31) a partir do Claude Code.

Duas camadas, unidas na borda das tools MCP (`server.py`):

- **Autoria offline de L5X** — Python puro, sem SDK. Parseia, valida (ST/RLL/FBD), faz diff e monta templates de L5X. Funciona mesmo sem a SDK instalada.
- **Sessão SDK ao vivo** (`ProjectSession`) — abre/edita/salva um `.ACD` de verdade via Logix Designer SDK, com um único lock serializando acesso ao engine (não-reentrante) e o padrão `backup → operate → reopen-to-verify → rollback` em toda mutação.

Todo tool retorna um envelope padrão `{ok, data, error, meta}` (`envelope.py`).

## O que dá pra fazer

- Ler um projeto aberto: programas, rotinas, tags (paginado), definição de UDT, assinatura de AOI, config de módulos I/O, valor de tag, export de L5X de qualquer nó.
- Validar e revisar L5X antes de aplicar: `validate_l5x`, `preview_import` (diff + tags referenciadas fora do projeto + change_token).
- Aplicar mudanças com portão de confirmação humana: importar rotina/tag/rungs/componente (AOI/UDT), com token de mudança e rollback automático em falha.
- Operar a sessão SDK: abrir/criar/salvar/fechar projeto, converter revisão do `.ACD`, ler/mudar modo do controlador, ir online/offline, download/upload com o controlador, ler/escrever tag em modo OFFLINE ou ONLINE.
- Recuperação de engine: `health` e `restart_engine` para o processo SDK (`LdSdkServer.exe` / serviço `LdSdkService`), disponíveis mesmo em modo somente-leitura.

Exclusões de segurança (`safety.py`) bloqueiam import que toque tags marcadas como safety, exceda o tamanho máximo, ou contenha DOCTYPE — checado **antes** de qualquer escrita em disco.

## Dependências

- Python `>=3.12,<3.14`.
- Runtime: `lxml`, `defusedxml`, `psutil`.
- Extra `[server]`: `fastmcp` (necessário para rodar o servidor MCP).
- Extra `[dev]`: `pytest`, `pytest-asyncio`, `pytest-cov`.
- **Logix Designer SDK** (`logix_designer_sdk`) — pacote proprietário da Rockwell, **não está no PyPI**. Vem com a instalação do Studio 5000 em `C:\Program Files (x86)\Rockwell Software\Studio 5000\Logix Designer SDK`. Sem ela o servidor ainda sobe e lista as tools, mas qualquer operação em projeto ao vivo falha com erro claro.
- A SDK fala com o engine **compartilhado** (`RSLogix5000Services.exe` / serviço *Logix Designer SDK*) em loopback na porta `53204`, que segura a ativação do FactoryTalk. O MCP **não** sobe engine próprio por padrão — adota o serviço já rodando.
- **Limite do v31**: só um projeto V31 pode ficar aberto por vez na máquina inteira (regra da própria SDK). Autoria offline (L5X) pode rodar em paralelo à vontade; trabalho ao vivo via SDK é serializado a um projeto por máquina.

## Instalação

```bash
git clone https://github.com/lucianofr/mcp-studio5k.git
cd mcp-studio5k
pip install -e ".[dev,server]"
```

Rode a suíte de testes (não precisa da SDK real — usa um stand-in nos testes):

```bash
pytest
```

### Registrar no Claude Code

Adicione a entrada em `~/.claude.json` (`mcpServers`) ou `.mcp.json` do projeto:

```json
{
  "mcpServers": {
    "mcp-studio5k": {
      "command": "mcp-studio5k",
      "env": {
        "MCP_S5K_PROJECT_ROOT": "C:\\Projetos\\studio5000",
        "MCP_S5K_BACKUP_DIR": "C:\\Projetos\\studio5000\\.backups",
        "MCP_S5K_READ_ONLY": "true"
      }
    }
  }
}
```

`command` pode ser `mcp-studio5k` (se o console script estiver no PATH) ou o caminho completo do interpretador da venv, ex. `C:\Projetos\mcp-studio5k\.venv\Scripts\mcp-studio5k.exe`. Depois reconecte o servidor (`/mcp` no Claude Code, ou reinicie).

Variáveis de ambiente (`config.py`):

| Variável | Obrigatória | Descrição |
|---|---|---|
| `MCP_S5K_PROJECT_ROOT` | sim | diretório que limita todo caminho de projeto/backup (sem traversal, sem UNC, só `.acd`) |
| `MCP_S5K_BACKUP_DIR` | sim | diretório dos backups rotacionados do `.ACD` |
| `MCP_S5K_READ_ONLY` | não (default `true`) | `"false"` habilita as tools de escrita |
| `MCP_S5K_CHANGE_TOKEN_SALT` | sim se não read-only | segredo do servidor, ≥16 chars, assina os `change_token` |
| `MCP_S5K_PROJECT_FILE` | não | `.ACD` a abrir no boot (requer `MCP_S5K_AUTO_OPEN=1`) |
| `MCP_S5K_AUTO_OPEN` | não (default off) | `1` reativa abertura automática no start |
| `MCP_S5K_MAX_L5X_BYTES` | não | teto de tamanho de L5X aceito em import (default 5 MB) |
| `MCP_S5K_MAX_EXPORT_BYTES` | não | teto de export (default = `MAX_L5X_BYTES`) |
| `MCP_S5K_ALLOWED_PROPS` | não | allowlist (separada por vírgula) de propriedades de controlador editáveis |
| `MCP_S5K_SAFETY_EXCLUSIONS` | não | allowlist de nomes de tag safety excluídos de import |
| `MCP_S5K_SDK_PORT` | não | porta explícita do engine; por padrão conecta ao serviço compartilhado em `53204` |

Para trocar de projeto sem editar `~/.claude.json` à mão, use o script incluído (é o que o comando `/abrir-projeto` chama):

```bash
python scripts/set_project_env.py "C:\Projetos\studio5000\MeuProjeto.ACD"
```

Ele valida o `.ACD`, ajusta `MCP_S5K_PROJECT_ROOT`/`MCP_S5K_PROJECT_FILE`/`MCP_S5K_BACKUP_DIR` na entrada `mcp-studio5k`, preserva o resto (salt, read-only, limites) e faz backup em `~/.claude.json.bak`. Depois é preciso reconectar o servidor para o novo root valer.

## Uso

Roda como servidor stdio (`mcp-studio5k` ou `python -m mcp_studio5k`); diagnóstico vai para stderr, stdout é reservado pro transporte MCP.

Fluxo típico de sessão:

1. `open_project(path)` — abre o `.ACD` (path relativo a `MCP_S5K_PROJECT_ROOT`).
2. Leituras de reconhecimento antes de gerar lógica: `list_programs_routines`, `list_tags`, `get_udt_definition`, `get_aoi_signature`, `export_l5x` de uma rotina similar como modelo.
3. Gera o L5X, roda `validate_l5x`, depois `preview_import` — retorna diff e `change_token`.
4. Confirma com o humano, então aplica com `confirmed=True` e o `change_token`: `import_l5x` / `import_tag_l5x` / `import_rungs_l5x` / `import_routine_l5x` / `import_component_l5x` / `import_with_target_l5x`.
5. `save_project` (ou `save_project_as`) pra persistir.
6. `close_project` ao terminar.

Há um prompt MCP (`author_routine`) que já embute esses passos, e dois slash commands do projeto: `/abrir-projeto <caminho.ACD>` e `/fechar-projeto`.

Tools de escrita só existem quando `MCP_S5K_READ_ONLY=false`; toda mutação passa por `WriteRateLimiter` (limite por sessão + cooldown) e o padrão backup→operate→reopen-to-verify→rollback — uma falha nunca deixa um `.ACD` meio-escrito. `restart_engine` e `health` ficam disponíveis mesmo em modo leitura, como via de recuperação do engine.

### Tools disponíveis

**Leitura (sempre registradas)**
`health`, `list_programs`, `list_routines`, `list_programs_routines`, `list_tags`, `get_tag_value`, `get_udt_definition`, `get_aoi_signature`, `get_module_config`, `export_l5x`, `validate_l5x`, `preview_import`, `list_processor_types`, `get_communications_path`, `read_controller_mode`, `read_connected_state`, `is_safety_locked`, `get_safety_network_number`, `get_safety_signature`, `open_project`, `close_project`, `restart_engine`

**Escrita (só com `MCP_S5K_READ_ONLY=false`)**
`import_l5x`, `import_tag_l5x`, `import_component_l5x`, `import_routine_l5x`, `import_rungs_l5x`, `import_with_target_l5x`, `save_project`, `save_project_as`, `create_project`, `convert_project`, `set_communications_path`, `change_controller_type`, `change_controller_mode`, `go_online`, `go_offline`, `download_to_controller`, `upload_from_controller`, `upload_to_new_project`, `set_tag_value`

Recursos MCP: `l5x://template/{kind}` (template mínimo ST/LD/FBD) e `l5x://node/{xpath}` (export de um nó do projeto aberto).
