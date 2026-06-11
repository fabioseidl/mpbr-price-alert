# mpbr-price-alert

> Os dados são obtidos por meio de uma API pública do governo associada ao seu CPF. Utilize este recurso de forma consciente e responsável.
> >
> Para evitar sobrecarga nos serviços consultados, recomendamos limitar o uso a uma ou duas consultas por dia. Evite requisições excessivas.

Alertas de queda de preço para o serviço **Menor Preço Brasil**.

## 1. Configurar

```bash
pip install requests
cp config.example.json config.json
```

Edite o `config.json`:

| campo        | significado                                                    |
|--------------|----------------------------------------------------------------|
| `latitude` / `longitude` | centro da sua busca                                |
| `raio_km`    | raio de busca em km (padrão 10)                               |
| `dias`       | janela de atualidade do preço em dias (1–3)                  |
| `webhook_url`| opcional — envia POST `{"text": ...}` (estilo Slack/Mattermost/Telegram-bot) |
| `produtos[]` | `nome`, `gtin` e `preco_alvo` (alerta se preço ≤ este; `null` = alerta em qualquer queda em relação à última execução) |

## 2. Login

```bash
python mpbr.py login
```

Isso imprime uma URL `https://sso.acesso.gov.br/authorize?...`. Abra no navegador e faça login com seu CPF (gov.br). Em seguida o navegador é redirecionado para `br.gov.rs.procergs.mpbr://oauth/auth?code=…` — ele não consegue abrir esse esquema personalizado, então mostra uma página de erro/em branco. Copie a barra de endereço inteira e cole de volta no prompt. O código é de uso único e expira rápido, então cole sem demora.

O token é salvo em `tokens.json` e vale ~30 dias, então rode o `login` novamente cerca deuma vez por mês.

### Alternativa: importar um token capturado

Se o fluxo do navegador estiver bloqueado, importe um token capturado do app em execução:

```bash
python mpbr.py import-ticket '{"access_token":"…","expires_in":2592000}'
```

## 3. Executar

```bash
python mpbr.py run
```

Consulta cada GTIN monitorado uma vez e alerta em quedas de preço ou quando atinge o alvo.
Os últimos preços vistos ficam em `state.json`, então uma "queda" significa mais barato que a execução anterior.

Para rodar em um agendamento (é uma API do governo vinculada ao seu CPF — mantenha leve, uma ou duas vezes ao dia):

```cron
0 9,18 * * *  cd /caminho/para/mpbr-price-alert && /usr/bin/python3 mpbr.py run >> run.log 2>&1
```
