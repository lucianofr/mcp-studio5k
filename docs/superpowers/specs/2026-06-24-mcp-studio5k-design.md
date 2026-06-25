# Design — mcp-studio5k

**Data:** 2026-06-24
**Autor:** Luciano Rocha (LFR Automação) + Claude Code
**Status:** Aprovado para planejamento — revisado após revisão multi-agente (arquitetura, segurança, verificação técnica do SDK, convenções MCP)

## 1. Objetivo

Criar um **MCP server local** que permita ao Claude Code **criar e editar lógica de
controlador** (Ladder/LD, Function Block/FBD e Texto Estruturado/ST) em projetos
**Rockwell Studio 5000 Logix Designer**, de forma **offline** (sobre arquivos `.ACD`,
sem controlador físico).

O fluxo de autoria é **híbrido com exemplos**: o server exporta partes do projeto como
L5X para o Claude aprender o dialeto exato, o Claude gera novo L5X guiado por esses
exemplos, o server valida, mostra um **diff**, exige **confirmação humana** e faz import parcial.

> ⚠️ **Contexto de segurança:** estes projetos controlam máquinas industriais reais.
> Toda escrita é tratada como operação sensível: confirmação humana obrigatória,
> backup verificado, rollback automático e exclusão de tags de segurança. Ver seções 7 e 9.

### Escopo
- Edição **offline** de projetos `.ACD`.
- Abrir/criar/salvar projetos.
- Inspeção do projeto **via export+parse de L5X** (o SDK não tem API de enumeração — ver §2).
- Leitura de valor de tag individual (offline).
- Autoria de lógica LD/FBD/ST via import/export parcial de L5X.

### Fora de escopo (YAGNI por enquanto)
- Interação **online** com controlador (download, mudança de modo Program/Run, escrita de tags em runtime).
- Automação de build/deploy.
- Edição gráfica visual (FBD/LD) — apenas representação L5X.
- Edição de propriedades do controlador via setter direto (não existe na API — ver §5).

## 2. Contexto técnico (verificado contra a instalação real do SDK)

- **Cliente Python:** NÃO está no PyPI. É um **wheel local**:
  `C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl`
  - Nome de distribuição: `logix-designer-sdk`; **nome de import: `logix_designer_sdk`**.
  - Dependências: `pythonnet>=3.0.5,<4` e `numpy>=2.4.1,<3`. **Usa pythonnet/.NET**, não gRPC puro no lado Python.
  - **`Requires-Python: >=3.12,<3.14`** (classifiers 3.12 e 3.13).
- **Servidor:** `LdSdkServer.exe` (.NET 10 x86) em
  `C:\Program Files (x86)\Rockwell Software\Studio 5000\Logix Designer SDK`. Porta TCP **53204**.
- **Pré-requisitos:** **licença FactoryTalk Activation Professional** + FactoryTalk Services Platform +
  FactoryTalk Linx + **Logix Designer application v31+** instalado.
- **Modelo da API:** toda `async` (asyncio). `LogixProject` é a classe central; métodos de
  abrir/criar são `@staticmethod` async; o handle do projeto persiste entre chamadas.
- Ambiente atual: Windows 11; Python **3.14.6** no sistema → **incompatível** (ver Risco R1).

### Assinaturas reais confirmadas (fonte: Doxygen + exemplos do SDK)
```
LogixProject.open_logix_project(project_file_path, operation_events=None)            # async @staticmethod
LogixProject.create_new_project(project_file_path, major_revision,
                                processor_type_name, controller_name,
                                operation_events=None)                                # async @staticmethod
await project.save()
await project.save_as(save_path, force=False, detailed_l5x=False)
await project.close()
await project.partial_export_to_xml_file(x_path, file_path)                          # x_path = XPath; sem colisão
await project.partial_import_from_xml_file(x_path, xml_file_to_import,
                                           collision_option, continue_on_errors=False)
await project.get_tag_value_<tipo>(tag_path, mode=OperationMode.OFFLINE)             # _bool/_dint/_int/_real/_string...
# enums: ImportCollisionOptions = {CANCEL_ON_COLL, DISCARD_ON_COLL, OVERWRITE_ON_COLL}
#        OperationMode = {OFFLINE, ONLINE}
# x_path exemplo: "Controller/Programs/Program[@Name='MainProgram']/Routines/Routine[@Name='R1']"
```
Arquivos de referência: `...\python\Documentation\...LogixProject.html` (+ `-members.html`);
exemplos `...\python\Examples\{partial_import_offline,partial_export_offline,create_new_project,open_and_save_file,get_tag_value}.py`.

> **Descoberta que moldou o design:** `LogixProject` **não expõe** `list_programs`, `list_routines`,
> `list_tags`, `get_controller_properties`, `create_routine` nem `set_controller_property`.
> Não há API de enumeração estrutural. **Toda inspeção é feita exportando L5X de um nó e parseando o XML.**

## 3. Abordagem escolhida

**A — MCP server em Python (FastMCP) envolvendo o wheel `logix_designer_sdk`.**
FastMCP (stdio) → cliente Python (pythonnet → `LdSdkServer` na porta 53204) → `.ACD`.
Menor superfície de código, API mantida pela Rockwell.

Alternativas descartadas: (B) MCP em .NET sobre a DLL — tooling MCP imaturo, mais trabalho;
(C) falar com o servidor sem o cliente oficial — frágil e sem suporte.

## 4. Arquitetura

```
Claude Code ──MCP (stdio)──► mcp-studio5k (venv Python 3.12/3.13)
   ├── server.py            registro de tools/resources/prompts + lifecycle + gate read-only
   ├── project_session.py   handle do LogixProject ativo + asyncio.Lock + backup-verify-rollback
   ├── sdk_discovery.py     localiza wheel/LdSdkServer, valida versão, valida licença (puro/testável)
   ├── sdk_runtime.py       saúde/conexão do LdSdkServer (loopback 53204), reinício, PID/binário
   ├── inspect.py           "enumeração" via partial_export + parse L5X; get_tag_value(offline)
   ├── logic_authoring.py   orquestra: export exemplo → validar → diff → confirmar → import
   ├── l5x/                 parser ENDURECIDO (no-entity/no-network), validação, diff, templates
   ├── safety.py            allowlists, exclusão de tags de segurança, contador/rate-limit de escrita
   └── config.py            PROJECT_ROOT, BACKUP_DIR, read_only(default True), allowlists, limites
                                  │ pythonnet
                                  ▼
                       logix_designer_sdk ──► LdSdkServer.exe (127.0.0.1:53204) ──► .ACD
```

### Responsabilidades (arquivos pequenos e focados)
- `server.py` — entrada MCP; registra tools/resources/prompts; **não registra tools de escrita quando `read_only`**; shutdown limpo (fecha projeto sem salvar, loga pendências, libera handle do SDK).
- `project_session.py` — **um projeto ativo por sessão**; **`asyncio.Lock` único** envolvendo toda operação que toca o SDK/`.ACD` (serialização real). Expõe duas mutações sob o mesmo lock: `apply_l5x_import(...)` e `save(...)`. Padrão **backup→verificar backup→operar→reabrir/validar→em falha, restaurar backup e invalidar sessão**.
- `sdk_discovery.py` — localiza wheel e `LdSdkServer`, valida versão e licença FactoryTalk (estático, testável).
- `sdk_runtime.py` — garante `LdSdkServer` no ar e ligado só a loopback; valida PID/caminho do binário; recuperação após crash.
- `inspect.py` — como não há API de enumeração, exporta L5X de um nó (XPath) e **parseia** para listar programas/rotinas/tags; `get_tag_value(tag_xpath, type, mode=OFFLINE)`.
- `logic_authoring.py` — orquestra o fluxo híbrido; não contém lógica de validação (delega a `l5x/`).
- `l5x/` — parser XML endurecido; validação (XSD da Rockwell se disponível, senão estrutural) com **schema de erro estável**; geração de **diff legível por tipo de rotina**; templates LD/FBD/ST. Submódulos por dialeto: `l5x/rll.py` (LD), `l5x/st.py` (ST), `l5x/fbd.py` (FBD). A validação/diff de FBD é **estrutural** (integridade de grafo — ver apêndice §11), não semântica.
- `safety.py` — `ALLOWED_PROPERTY_NAMES`, `SAFETY_TAG_EXCLUSIONS`, contador de escrita por sessão e cooldown.
- `config.py` — caminhos, raiz de projeto, backups, limites, `read_only`, política de log.

**Princípios:** responsabilidade única; toda escrita passa por `project_session` sob lock; leitura nunca modifica nada; validação é função pura em `l5x/`.

## 5. Ferramentas MCP

Envelope consistente em todas: `{ ok, data, error, meta: { total?, page?, truncated?, size_bytes? } }`.
Erros **acionáveis pelo LLM** (validação, colisão, "nenhum projeto aberto", path inválido) voltam no envelope (`ok:false`); erros de **infra** (SDK fora do ar, versão/licença, `.ACD` corrompido) viram `ToolError` (sem stack trace gRPC cru).

**Sessão / projeto**
- `open_project(path)` — `readOnly:false`. Resolve `path` sob `PROJECT_ROOT`, exige extensão `.acd`. Vira ativo.
- `create_project(path, major_revision, processor_type_name, controller_name)` — mapeia para `create_new_project` (ordem correta). `processor_type_name` e `major_revision` validados por enum/lista.
- `save_project()` — `destructive`. Backup verificado + reabrir/validar + rollback automático.
- `save_project_as(path, overwrite=False)` — `destructive`. Recusa sobrescrever sem `overwrite=True`.
- `close_project()` / `project_status()` — status mostra projeto ativo, caminho e contador de escrita da sessão.

**Inspeção (somente leitura — `readOnlyHint:true, idempotentHint:true`)**
- `list_programs(page_size=100, cursor=None)` / `list_routines(program, page_size, cursor)` /
  `list_tags(scope, name_filter=None, page_size=100, cursor=None)` — **implementadas via `partial_export_to_xml_file` + parse** (não há API de enumeração); paginação por cursor + filtro server-side; devolvem resumo (`name`, `data_type`, `scope`), nunca dump completo.
- `get_tag_value(tag_xpath, data_type, mode="OFFLINE")` — lê uma tag tipada (método real do SDK).
- `export_l5x(x_path, max_bytes=...)` — exporta nó por XPath como exemplo/template. **Remove comentários/`Comment` do L5X** antes de devolver (anti prompt-injection); se exceder `max_bytes`, devolve via **resource URI** em vez de inline; envelope traz `size_bytes`/`truncated`.

**Autoria de lógica (escrita via L5X — exige `read_only=false`)**
- `validate_l5x(l5x_content)` — `readOnly`. Valida sem tocar no projeto; retorno de erro estável (`severity`, `path`, `message`, `line?`).
- `preview_import(l5x_content, x_path)` — `readOnly`. Valida + gera **diff legível conforme o tipo de rotina**: LD → rungs/instruções add/rem/alt + coils escritos; ST → linhas add/rem/alt; FBD → blocos (Type/Operand) e wires add/rem/alt. Em todos: **tags/operands referenciados que não existem no projeto** = sinal de alucinação. Retorna um `change_token`.
- `import_l5x(l5x_content, x_path, collision_option="CANCEL_ON_COLL", confirmed=False, change_token=None)` —
  `destructiveHint:true`. **Recusa** se `confirmed` ≠ True ou `change_token` ausente/divergente.
  Internamente reusa a validação de `l5x/`. `collision_option` restrito a `CANCEL_ON_COLL`/`DISCARD_ON_COLL`
  (sem `OVERWRITE_ON_COLL` pela tool — sobrescrever exige passo humano separado). Aplica `SAFETY_TAG_EXCLUSIONS`
  e valida `x_path` contra a árvore real do projeto + regex de formato.

**Descoberta (resources + prompt)**
- Resources estáticos: `l5x://template/{st|ld|fbd}` (esqueletos mínimos válidos).
- Resource dinâmico: `l5x://node/{xpath}` (espelha `export_l5x`, cacheável).
- Prompt `author_routine` — roteiro guiado (exportar exemplo → gerar → validar → preview → confirmar → import) para o LLM não pular etapas.

**Removidas/alteradas vs versão anterior do spec:**
- `create_routine` e `set_controller_property` **não existem na API** → removidas como setters diretos.
  Criar rotina vazia = `import_l5x` de um template. Editar propriedade do controlador = round-trip
  export→editar→import (incerto; ver §10), restrito por `ALLOWED_PROPERTY_NAMES` e desabilitado por padrão.

**Guardrails (resumo):** `read_only=true` por padrão (escrita é opt-in por sessão); confirmação humana
obrigatória em `import_l5x`; contador de escrita por sessão com re-confirmação após N (default 5) e cooldown;
toda escrita logada (hash do conteúdo, não o conteúdo).

## 6. Fluxo de dados (caso principal: nova rotina ST)

```
0. (sessão inicia em read_only; operador habilita escrita explicitamente)
1. open_project("<PROJECT_ROOT>\Linha1.acd")
2. list_programs() / list_routines(prog)            (via export+parse de L5X)
3. export_l5x("...Routine[@Name='Similar']")        → modelo sem comentários
4. Claude gera o L5X da nova rotina
5. validate_l5x(novo_l5x)                            → erros posicionais acionáveis ou ok
6. preview_import(novo_l5x, x_path)                  → diff + tags inexistentes; retorna change_token
7. (humano revisa o diff)
8. import_l5x(novo_l5x, x_path, "CANCEL_ON_COLL", confirmed=True, change_token=...)
9. save_project()                                    → backup verificado + salva + reabre/valida (rollback se falhar)
```

Tudo sob o `asyncio.Lock` da sessão. Projeto permanece aberto entre chamadas (handle do `LogixProject`).
Um projeto ativo por vez; `import_l5x`/`save_project` aceitam `expected_project_path` opcional como guarda de sanidade.

## 7. Tratamento de erros e segurança

- **Boundary MCP:** valida todo argumento (projeto aberto? `path` sob `PROJECT_ROOT` e `.acd`? `x_path` casa com a árvore real + regex? tipo ∈ {LD,FBD,ST}?). Mensagens acionáveis.
- **Path traversal (CRÍTICO):** `pathlib.resolve()` obrigatório sob `PROJECT_ROOT`; rejeitar UNC e device paths (`\\.\`); temp de import via `tempfile.mkstemp()` em dir controlado (anti-TOCTOU); logar caminho canônico.
- **XML endurecido (CRÍTICO):** `lxml` com `resolve_entities=False, no_network=True, load_dtd=False`; rejeitar `<!DOCTYPE>`; teto de tamanho de `l5x_content` (≈5 MB) antes de parsear; `defusedxml` como defesa extra. Mesmo endurecimento no parse do export.
- **Gate humano (CRÍTICO):** nenhuma tool recebe-e-aplica numa só chamada; `import_l5x` exige `confirmed=True` + `change_token` de um `preview_import` recente.
- **Prompt injection:** remover comentários/`Comment` do L5X exportado; conferir que a rotina importada bate com o alvo instruído; o diff humano é a mitigação final.
- **Integridade (backup→verify→operar→reabrir/validar→rollback):** backup em `BACKUP_DIR` isolado do projeto, verificado por tamanho antes de operar; rollback automático em falha de import/save; rotação (N=10) e checagem de espaço — se faltar, **aborta** em vez de operar sem backup.
- **gRPC local:** `sdk_runtime` garante bind em `127.0.0.1:53204`; valida PID/binário; regra de firewall opcional no setup.
- **Allowlists/exclusões:** `import_l5x` recusa tocar tags em `SAFETY_TAG_EXCLUSIONS`; edição de propriedade só nomes em `ALLOWED_PROPERTY_NAMES`.
- **Logging:** INFO = operação/timestamp/sucesso/bytes/hash; conteúdo L5X só em DEBUG; logs em `%LOCALAPPDATA%\mcp-studio5k\logs\`.
- **Rate limiting:** contador de escrita por sessão + cooldown entre imports; WARNING a cada incremento.
- **Concorrência:** `LdSdkServer`/`.ACD` não reentrantes → `asyncio.Lock` serializa; 2ª chamada durante escrita espera na fila (não rejeita).
- **Sem engolir erros:** tratar explicitamente ou propagar com contexto.

## 8. Testes

- **Unitários (sem SDK):** `l5x/` por dialeto — `rll.py`/`st.py`/`fbd.py` (parse endurecido, validação, **diff**, templates). Para **FBD**: integridade de grafo (IDs únicos por Sheet, todo `FromID`/`ToID` resolve, `ToParam` ∈ pinos do bloco), round-trip parse→serialize do sample. Também: parsing de export→lista, paginação, backup/rollback, boundary/path, `safety.py`. Mocks **fiéis às assinaturas confirmadas em §2**. Fixtures: samples L5X reais (`ST_GearChange.L5X`, `LD_Scale_Value.L5X`, `FBDLevelControlSimulation.L5X`).
- **Integração (SDK real, Windows + licença FactoryTalk):** `create_new_project` de fixture `.acd` (gerado no setup, não commitado) → `partial_export`/parse → `validate`→`preview`→`import` de uma rotina de **cada tipo (ST, LD, FBD)** → `save` → reabrir e conferir; idempotência; rollback de backup. Marcados para rodar só onde SDK+licença existem (não roda em CI comum).
- **E2E (manual/script):** fluxo completo via cliente MCP, incluindo o gate de confirmação.
- **Cobertura alvo:** ≥80% na lógica pura; camada SDK por integração.

## 9. Riscos

- **R1 — Python:** SDK exige `>=3.12,<3.14`; sistema tem 3.14 (incompatível). Mitigação: **venv dedicado 3.12 ou 3.13**.
- **R2 — Dialeto L5X por versão:** schema varia por versão. Mitigação: fluxo híbrido (export real) + validação tolerante com schema de erro estável.
- **R3 — FBD complexo (em escopo):** FBD é a representação L5X mais difícil — não é texto linear (LD/ST), mas um **grafo** de blocos + wires com IDs únicos por Sheet e integridade referencial (todo `FromID`/`ToID` deve existir; `ToParam` deve ser um pino válido do bloco). Mitigação: gerar a partir do **template FBD** e de exemplos exportados; validação estrutural de grafo em `l5x/fbd.py` (ver §11); usar o sample `FBDLevelControlSimulation.L5X` como fixture. Ordem de entrega sugerida (risco crescente): **ST → LD → FBD**, mas os três são escopo desta versão.
- **R4 — Dependências/ativação:** wheel local (não PyPI); pythonnet+numpy; .NET 10 x86; porta 53204; **licença FactoryTalk Professional + Logix Designer v31+**. `sdk_discovery` valida tudo no startup; integração não roda em CI sem licença.
- **R5 — Sem API de enumeração (estrutural):** inspeção depende de export+parse de L5X; XPaths e schema podem mudar por versão. Mitigação: parse defensivo, paginação, testes sobre L5X real.
- **R6 — Segurança industrial (human-in-the-loop):** lógica gerada por LLM controla máquina real. Mitigação: `read_only` por padrão, gate de confirmação com diff, exclusão de tags de segurança, rate-limit.
- **R7 — Prompt injection / superfície XML:** mitigada por strip de comentários, parser endurecido e diff humano.

## 10. Decisões em aberto para a fase de plano
- Confirmar XPaths exatos aceitos por `partial_export_to_xml_file` para enumerar programas/rotinas/tags (testar contra um `.acd` real).
- Confirmar se há XSD do L5X distribuído com o SDK para validação forte; senão, definir validação estrutural.
- Confirmar se edição de propriedade do controlador é viável via round-trip L5X; se não, marcar como fora de escopo.
- Confirmar formato/granularidade do `change_token` (hash do conteúdo + xpath + timestamp) e janela de validade.
- Confirmar o `OperationEvent`/logger esperado por `open_logix_project`/`create_new_project`.
- Para FBD: confirmar quais `Type` de `<Block>` (ADD, SUB, MUL, HLL, DEDT, PIDE, SCL, …) e seus pinos são aceitos pela versão instalada; e se `partial_import` aceita uma única `<Sheet>` ou exige a `<Routine Type="FBD">` completa.

## 11. Apêndice — Estrutura L5X por tipo de rotina (referência para `l5x/`)

Fonte: samples reais do SDK (`...\Studio 5000\Samples\`). Raiz de todo L5X:
`<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="..." ...>` → `<Controller>` → `.../Routines/Routine`.

### Structured Text (ST) — texto linear
```xml
<Routine Name="..." Type="ST">
  <STContent>
    <Line Number="0"><![CDATA[IF input THEN]]></Line>
    <Line Number="1"><![CDATA[  state := NextState;]]></Line>
    <Line Number="2"><![CDATA[END_IF;]]></Line>
  </STContent>
</Routine>
```
Diff/validação: por linha (texto). Mais simples.

### Ladder (LD/RLL) — rungs lineares
```xml
<Routine Name="..." Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N">
      <Comment><![CDATA[...]]></Comment>
      <Text><![CDATA[CPT(Output, Input * Rate + Offset);]]></Text>
    </Rung>
  </RLLContent>
</Routine>
```
Diff/validação: por rung; `<Text>` é o mnemônico neutro. Moderado.

### Function Block (FBD) — **grafo** (o mais complexo, agora em escopo)
```xml
<Routine Name="..." Type="FBD">
  <FBDContent SheetSize="Tabloid - 11 x 17 in" SheetOrientation="Landscape">
    <Sheet Number="1">
      <IRef  ID="0" X="160" Y="420" Operand="FlowIntoTank"/>            <!-- entrada (tag externa) -->
      <OCon  ID="1" X="520" Y="280" Name="TankLevel"/>                  <!-- saída p/ outra sheet -->
      <Block Type="ADD" ID="2" X="300" Y="100" Operand="ADD_01"
             VisiblePins="SourceA SourceB Dest"/>                       <!-- bloco de função -->
      <Wire FromID="0" ToID="2" ToParam="SourceA"/>                     <!-- conexão -->
      <Wire FromID="2" FromParam="Dest" ToID="1"/>
      <FeedbackWire FromID="2" FromParam="Dest" ToID="2" ToParam="SourceB"/>
    </Sheet>
  </FBDContent>
</Routine>
```

**Elementos:** `<Sheet Number>`; nós `<Block Type ID X Y Operand VisiblePins>` (pode ter `<Array>` filho p/ pinos de array), `<IRef ID X Y Operand>` (entrada), `<OCon ID X Y Name>` (saída cross-sheet), `<ICon ID X Y Name>` (entrada cross-sheet); arestas `<Wire FromID [FromParam] ToID [ToParam]>` e `<FeedbackWire ...>`.

**Regras estruturais que `l5x/fbd.py` deve validar (integridade de grafo):**
1. `ID` único **por Sheet** (não global) em todo Block/IRef/OCon/ICon.
2. Todo `FromID`/`ToID` de Wire/FeedbackWire referencia um `ID` existente na mesma Sheet.
3. `FromParam` (saída) e `ToParam` (entrada) devem ser pinos válidos do `Type` do bloco; pinos usados devem estar em `VisiblePins`.
4. `Operand` de cada Block/IRef referencia uma tag existente (senão → flag de tag inexistente no `preview_import`).
5. Atributos obrigatórios: Block(`ID,Type,X,Y,Operand`), IRef(`ID,X,Y,Operand`), OCon/ICon(`ID,X,Y,Name`), Wire(`FromID,ToID`); X/Y inteiros.
6. `FBDContent` exige `SheetSize` e `SheetOrientation`.

O `get_l5x_template("fbd")` retorna uma `<Routine Type="FBD">` com uma `<Sheet>` mínima válida (1 IRef + 1 Block + 1 OCon + wires) como ponto de partida para o Claude.
