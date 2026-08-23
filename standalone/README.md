# WB/Ozon price & ad agents — standalone

Независимый запуск обоих агентов для личных кабинетов, без MARKETSHELPER.

## Setup

```bash
pip install -r ../tenant-app/requirements.txt requests pyyaml
cp config.example.yaml config.yaml
```

Заполните `config.yaml` реальными ключами:
- **WB**: кабинет продавца → Настройки → Доступ к API → токен со scope «Цены и скидки», «Продвижение», «Аналитика».
- **Ozon**: кабинет продавца → Настройки → Seller API → Client-Id + Api-Key.

## Запуск

```bash
python run_agents.py --once                  # только рекомендации (dry-run), ничего не применяет
python run_agents.py --once --auto-apply      # применяет рекомендации в рамках лимитов из config.yaml
python run_agents.py --loop --interval 60     # повторяет каждые 60 минут
```

Все рекомендации и решения пишутся в `standalone/agents_store.sqlite3` (таблицы `agent_candidates`,
`agent_decisions`, `agent_audit_log`) — можно открыть любым SQLite-клиентом.

## Текущий статус (важно)

Реальные методы записи (`set_price`, `set_campaign_budget`, `pause_campaign`, ...) в
`agents/marketplaces/wb_client.py` и `agents/marketplaces/ozon_client.py` пока
**не подключены к боевым API** — при `--auto-apply` они поднимут `NotImplementedError`
с указанием, какой именно эндпоинт нужно реализовать. Их нужно доделать и
проверить по документации после получения реальных ключей WB (scope
«Цены и скидки» + «Продвижение») и Ozon Seller API.

До этого агенты полностью рабочие в режиме предложений — можно смотреть,
что бы они сделали, без риска для реальных цен/рекламы.
