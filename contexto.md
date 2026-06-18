# Naishoku Management System (NMS)

## 1. Visão Geral do Sistema
O NMS é uma plataforma SaaS multi-tenant projetada para pequenas fábricas e distribuidoras no Japão que terceirizam etapas de montagem, processamento ou acabamento industrial para trabalhadores domésticos (naishokusha). A cadeia de suprimentos física do sistema opera em um modelo de pirâmide: grandes matrizes industriais (ex: Yamaha, Honda, Suzuki) enviam grandes lotes de componentes para as pequenas fábricas contratadas. Estas subfábricas, por sua vez, fracionam os insumos e os distribuem para uma rede de trabalhadores domiciliares.

O sistema gerencia o ciclo completo desse fluxo: a entrada do lote bruto vindo da Matriz, o fracionamento controlado de componentes, a logística de entrega/recolhimento assistida por geolocalização, o controle rigoroso de estoque descentralizado, a inspeção de qualidade no retorno e o fechamento automático da folha de pagamento por produção.

## 2. Stack Tecnológica & Escolhas de Arquitetura
- **Backend & Core**: Django 5.x+ (Python) operando sob a filosofia estrita de Desenvolvimento Guiado por Testes (TDD).
  - **Tipagem Forte (Type Hinting Estrito)**: O código Python do backend será fortemente tipado ("C like"). Utilizaremos anotações de tipo rigorosas (`typing`, `mypy` no modo estrito) para todas as funções, assinaturas de métodos (incluindo retornos) e variáveis. Isso visa garantir a segurança de tipos no desenvolvimento, forçando a detecção precoce de incompatibilidades entre os dados de entrada (inputs), os modelos ORM/banco de dados e as regras de negócio em tempo de desenvolvimento/análise estática.
- **Banco de Dados**: PostgreSQL.
- **Estratégia Multi-Tenant**: Banco de dados único com Isolamento Lógico (Shared Schema) via campo `tenant_id` em todas as tabelas operacionais. Esta escolha prioriza a velocidade máxima na execução da suíte de testes (essencial para o fluxo TDD rápido de um desenvolvedor solo) e simplifica o gerenciamento de migrações em produção.
- **Frontend**: Django Templates + Tailwind CSS. Abordagem Server-Driven pura, eliminando os riscos de ataques de supply chain comuns em ecossistemas SPA baseados em dependências massivas do Node/NPM.

## 3. Mecanismo de Isolamento Lógico (Segurança Multi-Tenant)
Para mitigar erros humanos de vazamento de dados (data leak) por esquecimento de filtros no ORM, o sistema automatiza o isolamento de escopo em nível de aplicação:
- **Captura de Contexto (Middleware)**: A cada requisição HTTP, um middleware intercepta o usuário autenticado, extrai seu `tenant_id` e o armazena em uma variável assíncrona context-safe (`contextvars.ContextVar`), isolada por thread/corrotina.
- **Blindagem do ORM (Custom Manager)**: Todos os modelos do banco de dados que exigem isolamento herdarão de uma classe abstrata comum chamada `TenantModel`. Esta classe substitui o Manager padrão (`objects`) por um `TenantManager`.
- **Injeção de Cláusula**: O `TenantManager` subscreve o método `get_queryset()`, injetando de forma invisível e obrigatória a cláusula `WHERE tenant_id = X` em todas as operações de leitura.
- **Validação de Escrita**: O método `save()` do `TenantModel` intercepta as operações de escrita, garantindo que o `tenant_id` do objeto em persistência corresponda estritamente ao tenant ativo no contexto da thread.

## 4. Ontologia e Modelagem Relacional (Estrutura do Banco)

### 4.1. Camada Global / Tabelas Compartilhadas (Cross-Tenant)
Dados de catálogo corporativo e controle administrativo do SaaS, livres do filtro do Manager de Tenant.
- **ParentFactory (Matrizes Industriais)**:
  - `id`: UUIDField (Primary Key, UUIDv4)
  - `name`: CharField (ex: "Yamaha Motor Hamamatsu")
  - `industry_code`: CharField (Código de registro industrial único no Japão)
- **GlobalPart (Catálogo de Peças)**:
  - `id`: UUIDField (Primary Key, UUIDv4)
  - `parent_factory`: ForeignKey -> ParentFactory
  - `sku`: CharField (Código da peça definido pela matriz)
  - `name`: CharField (Nome técnico do componente)
  - `blueprint_url`: URLField (Link seguro para diagramas ou instruções de montagem)
  - `unit_weight`: DecimalField (Peso unitário em gramas para auditoria por balança de precisão)

### 4.2. Camada de Autenticação e Usuários Internos
- **CustomUser (Funcionário/Gerente da Subfábrica)**:
  - Herança: `AbstractUser` do Django.
  - `id`: UUIDField (Primary Key, UUIDv4)
  - `tenant_id`: ForeignKey -> Tenant (Vínculo mandatório com uma única subfábrica)

### 4.3. Camada Operacional Isolada (Escopo de Tenant)
- **Tenant (A Subfábrica Usuária do SaaS)**:
  - `id`: UUIDField (Primary Key, UUIDv4)
  - `corporate_name`: CharField
  - `postal_code`: CharField
  - `prefecture`: CharField
- **MatrixShipment (Lotes de Entrada da Matriz)**:
  - `id`: UUIDField (Primary Key)
  - `tenant_id`: ForeignKey -> Tenant
  - `parent_factory`: ForeignKey -> ParentFactory
  - `shipment_number`: CharField (Número da nota de entrega / Kanban da Matriz)
  - `status`: CharField (Choices: RECEIVED, FRACTIONED, RETURNED)
  - `received_at`: DateTimeField
- **MatrixShipmentItem (Itens do Lote da Matriz)**:
  - `id`: UUIDField
  - `tenant_id`: ForeignKey -> Tenant
  - `matrix_shipment`: ForeignKey -> MatrixShipment
  - `global_part`: ForeignKey -> GlobalPart
  - `quantity_expected`: IntegerField (Quantidade total enviada pela matriz)
- **Worker (Trabalhador Doméstico / Naishokusha)**:
  - `id`: UUIDField
  - `tenant_id`: ForeignKey -> Tenant
  - `name`: CharField
  - `phone`: CharField
  - `address_line`: CharField
  - `latitude`: DecimalField (max_digits=9, decimal_places=6, null=True)
  - `longitude`: DecimalField (max_digits=9, decimal_places=6, null=True)
  - `geo_updated_at`: DateTimeField (null=True)
- **ReusableQRCode (QR Codes Reutilizáveis/Plastificados)**:
  - `id`: UUIDField (Primary Key, UUIDv4)
  - `tenant_id`: ForeignKey -> Tenant
  - `code`: CharField (Identificador visual legível, ex: "QR-001")
  - `is_active`: BooleanField (Indica se está atualmente associado a uma ordem de serviço ativa)
- **NaishokuJobOrder (Ordem de Serviço Doméstica)**:
  - `id`: UUIDField (UUIDv4 embutido no QR Code de uso único)
  - `tenant_id`: ForeignKey -> Tenant
  - `reusable_qr_code`: ForeignKey -> ReusableQRCode (Null=True, para vincular a um QR Code plastificado)
  - `matrix_shipment_item`: ForeignKey -> MatrixShipmentItem
  - `worker`: ForeignKey -> Worker
  - `short_audit_code`: CharField (Unique por Tenant, código curto de fallback)
  - `quantity_assigned`: IntegerField (Peças entregues para a casa do trabalhador)
  - `payout_per_unit`: IntegerField (Valor em Ienes pago por unidade aprovada)
  - `status`: CharField (Choices descritas na Seção 5)
  - `assigned_at`: DateTimeField
  - `deadline`: DateField
- **StockMovementLedger (Diário de Bordo do Estoque - Auditoria Absoluta)**:
  Esta tabela representa o Modelo de Saldo Calculado (Extrato). Nenhuma coluna de saldo acumulado existe nas tabelas operacionais; o estoque é a soma histórica desta tabela.
  - `id`: UUIDField
  - `tenant_id`: ForeignKey -> Tenant
  - `global_part`: ForeignKey -> GlobalPart
  - `quantity`: IntegerField (Positivo para entradas na fábrica, negativo para saídas/distribuições)
  - `movement_type`: CharField (Choices: MATRIZ_IN, WORKER_DISTRIBUTION, WORKER_RETURN_APPROVED, WORKER_RETURN_REFUSE, LOSS_DIVIDEND)
  - `processed_by`: ForeignKey -> CustomUser (Rastreabilidade total do funcionário que executou a ação)
  - `worker`: ForeignKey -> Worker (Null=True, preenchido quando envolver logística externa)
  - `created_at`: DateTimeField (Auto_now_add)

## 5. Máquina de Estados e Regras de Balanço de Inventário
O ciclo de vida de uma ordem de trabalho doméstica (NaishokuJobOrder) segue uma máquina de estados linear e auditável:
`ASSIGNED -> IN_PRODUCTION -> [READY_DELIVERING | READY_PICKUP] -> COLLECTING -> IN_INSPECTION -> FINALIZED`

Regras de Invariância Matemática (Garantias do Sistema):
- **Trava de Alocação**: A soma de `quantity_assigned` de todas as `NaishokuJobOrders` ativas de um `MatrixShipmentItem` não pode exceder o valor de `quantity_expected` original.
- **Equação de Retorno e Fechamento**: No estado `IN_INSPECTION`, a conferência física do lote devolvido pelo trabalhador deve fechar obrigatoriamente a equação:
  `Quantidade Distribuída = Peças Aprovadas + Refugo (Defeito de Montagem) + Perda Física de Material`
- **Cálculo de Liquidação Financeira**: O cálculo do valor a ser pago ao trabalhador domiciliar no fechamento da folha baseia-se exclusivamente nas peças úteis:
  `Pagamento Total = Peças Aprovadas * Tarifa Unitária (payout_per_unit)`

## 6. Dinâmica de Comunicação Assíncrona e Logística Inteligente
### 6.1. O Fluxo do Link Assinado via QR Code (Tokenized Access)
Para garantir que trabalhadores de qualquer faixa etária utilizem o sistema sem o atrito de memorizar credenciais, a autenticação externa é sem senha:
- O QR Code aponta para o endpoint seguro `/go/<uuid4>/`, mapeando o UUID único daquela `NaishokuJobOrder` ou do `ReusableQRCode` correspondente.
- Se o UUID apontar para um `ReusableQRCode`, o sistema resolve o link localizando a `NaishokuJobOrder` ativa (status antes de `FINALIZED`) vinculada a este QR Code. Caso contrário, busca diretamente o UUID da `NaishokuJobOrder`.
- A URL é assinada criptograficamente via `django.core.signing`. O acesso concede permissões de Autoridade Mínima: o portador do link pode apenas visualizar o status da sua própria ordem de serviço atual, enviar suas coordenadas de GPS atuais via API do navegador e acionar as mudanças de estado (IN_PRODUCTION, READY_DELIVERING ou READY_PICKUP). Nenhum dado cross-tenant ou histórico financeiro completo fica exposto nesta rota.

### 6.2. Otimização de Coleta Geográfica (Multi-Stop Routing)
Quando múltiplos trabalhadores alteram seus status para READY_PICKUP, o painel do gerente da subfábrica consolida os registros visualmente. Ao despachar um motorista para recolhimento:
- O sistema executa uma query capturando as coordenadas (latitude, longitude) ativas de todos os trabalhadores selecionados para aquela rota.
- O Django compila esses pares geográficos e formata uma URL de intenção nativa para a API do Google Maps: `https://www.google.com/maps/dir/?api=1&parameters...`
- O motorista, ao abrir o link em seu smartphone, é direcionado imediatamente ao aplicativo do Google Maps com a rota otimizada multi-paradas já montada, eliminando a necessidade de conhecimento prévio ou treinamento logístico sobre a localização das residências.

## 7. Mecanismos de Anticolisão e Resiliência de Fallback
### 7.1. Anticolisão de URLs
O uso de UUIDv4 como chave primária de busca nas URLs públicas mitiga o risco de colisões matemáticas a níveis estatisticamente nulos (2^122 possibilidades), impedindo que acessos concorrentes ou geração massiva de ordens causem sobreposição de links.

### 7.2. O Plano B: Código Curto com Checksum (Fallback Humano)
Caso o QR Code impresso seja severamente danificado (acima dos 30% de tolerância), entra em ação o shortcode legível impresso imediatamente abaixo do código óptico.
- **Estrutura**: `[Prefixo do Tenant]-[ID Incremental da Ordem]-[Dígito Verificador]` (Ex: SUZU-1452-K).
- **O Fluxo de Contingência**: O trabalhador acessa a URL pública padrão da plataforma (`nms.jp/go/`) e digita o código curto.
- **Blindagem Algorítmica**: O dígito verificador final é um Checksum calculado via chave criptográfica do sistema. Se o trabalhador cometer um erro de digitação (Ex: trocar K por A), o Django valida a matemática do Checksum antes de consultar o banco de dados. Se não bater, a requisição é rejeitada na hora, evitando ataques de varredura (brute-force) e impedindo que o usuário visualize acidentalmente o lote de outro trabalhador.

## 8. Diretrizes e Matriz de Cobertura para TDD
Nenhum código de produção será aceito sem que sua respectiva suíte de testes em `pytest-django` (utilizando Factory Boy para geração de dados em memória) passe pelo ciclo Red-Green-Refactor.

Casos de Teste Mandatórios da Arquitetura:
- **Suíte A: Validação de Isolamento Multi-Tenant**
  - **A1**: Autenticar uma requisição como CustomUser do Tenant A e tentar ler/gravar registros do Tenant B. Garantir retorno HTTP 404/403 ou omissão completa de dados.
  - **A2**: Validar que queries genéricas disparadas pelo ORM injetem o parâmetro do banco sem a necessidade de cláusulas `.filter(tenant=...)` manuais nas Views.
- **Suíte B: Integridade de Movimentação de Estoque**
  - **B1**: Testar o cálculo de saldo do `StockMovementLedger`. Garantir que a inserção de movimentações positivas e negativas resulte no saldo correto em tempo real.
  - **B2**: Verificar se qualquer registro no ledger de estoque armazena de forma imutável o ID do `CustomUser` responsável, barrando inserções anônimas.
- **Suíte C: Validação de Invariantes e Máquina de Estados**
  - **C1**: Tentar criar ordens de serviço cuja soma de peças ultrapasse o teto estabelecido pelo lote original da Matriz industrial (`MatrixShipmentItem`). O sistema deve lançar um ValidationError.
  - **C2**: Validar que uma inspeção de qualidade que apresente disparidade matemática (Distribuído != Aprovado + Refugo + Perda) tenha seu salvamento rejeitado pelo banco.
- **Suíte D: Segurança Criptográfica de Links Periféricos**
  - **D1**: Simular uma requisição na rota pública do QR Code alterando um único caractere do UUIDv4 ou do token assinado. O sistema deve retornar HTTP 403.
  - **D2**: Testar o validador do Shortcode de contingência: garantir que códigos com dígito verificador corrompido falhem imediatamente na camada de validação do formulário, sem atingir a camada de persistência do PostgreSQL.
  - **D3**: Testar a rota de QR Code reutilizável: garantir que ao acessar `/go/<uuid_reusable_qr_code>/` o sistema retorne a `NaishokuJobOrder` ativa vinculada, e retorne HTTP 404 caso o QR Code não esteja associado a nenhuma ordem ativa.
