# Distributed transactions homework

Набор сервисов демонстрирует реализацию распределённой транзакции по паттерну **Orchestrated Saga**. Заказ инициирует шаги оплаты, резервирования товара и бронирования доставки. При неуспехе любого шага оркестратор вызывает компенсирующие операции для уже завершённых шагов.

## Паттерн
Набор сервисов демонстрирует реализацию распределённой транзакции по паттерну **Orchestrated Saga**.

Паттерн **Orchestrated Saga** предполагает, что есть централизованный оркестратор (в нашем случае — сервис заказа), который:

- отвечает за запуск шагов распределённой транзакции в нужной последовательности;
- хранит список компенсирующих операций для уже выполненных шагов;
- при неуспехе любого шага инициирует вызов компенсирующих операций в обратном порядке.

В данной работе:

- сервис заказа инициирует шаги:
  - оплата (`payment`),
  - резервирование товара (`inventory`),
  - бронирование доставки (`delivery`);
- каждый сервис предоставляет пару REST-эндпоинтов: действие и компенсацию (`/reserve` + `/cancel`);
- при ошибке шага оркестратор вызывает соответствующие `/cancel` для уже успешно выполненных `/reserve`;
- для тестирования предусмотрены флаги `force_*_failure`, которые позволяют искусственно эмулировать отказ конкретного шага (например, склада).

## Локальный запуск
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Запуск сервисов в отдельных терминалах
ROLE=payment uvicorn app.main:app --host 0.0.0.0 --port 8001
ROLE=inventory uvicorn app.main:app --host 0.0.0.0 --port 8002
ROLE=delivery uvicorn app.main:app --host 0.0.0.0 --port 8003
ROLE=order PAYMENT_URL=http://localhost:8001 INVENTORY_URL=http://localhost:8002 DELIVERY_URL=http://localhost:8003 \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Kubernetes развёртывание

### Кластер на Docker (k3d)

Если нужен Kubernetes прямо в Docker, используйте k3d (k3s в контейнерах Docker).

Пример `k3d-config.yaml`:

```yaml
apiVersion: k3d.io/v1alpha5
kind: Simple
metadata:
  name: saga
servers: 1
agents: 1
ports:
  - port: 80:80
    nodeFilters:
      - loadbalancer
```

1. Создайте кластер (проброс портов 80/443 описан в `k3d-config.yaml`):
   ```bash
   k3d cluster create --config k3d-config.yaml
   ```
2. Соберите образ и импортируйте его в кластер k3d:
   ```bash
   docker build -t saga-demo:latest .
   k3d image import saga-demo:latest --cluster saga
   ```
3. Traefik ставится вместе с k3s в k3d, поэтому дополнительные действия для ingress не нужны. Примените манифесты приложения:

   ```bash
    kubectl apply -f k8s/namespace.yaml; \
    kubectl apply -f k8s/deployments.yaml; \
    kubectl apply -f k8s/services.yaml; \
    kubectl apply -f k8s/ingress.yaml
   ```

4. Пропишите `arch.homework` на localhost (Ingress проброшен на 80/443 через k3d load balancer):
   ```bash
   echo "127.0.0.1 arch.homework" | sudo tee -a /etc/hosts
   ```
5. Проверка:

   ```bash
   curl http://arch.homework/health
   curl -X POST http://arch.homework/orders -H "Content-Type: application/json" \
     -d '{"order_id":"demo-1","amount":10,"sku":"SKU-1","quantity":1,"slot":"2024-05-20T10:00"}'
   ```

## Postman тесты
Коллекция `postman/DistributedTransactions.postman_collection.json` содержит сценарии:
baseUrl = http://arch.homework
- **Health check** – `GET {{baseUrl}}/health`.
- **Create order - success** – успешная сага.
- **Create order - inventory failure triggers compensation** – демонстрация отката при сбое склада.
- **Get order status** – запрос состояния заказа.

Обязательно используйте переменную среды `baseUrl` со значением `http://arch.homework`.

## Структура API
- `POST /orders` – запускает сагу, вызывает:
  - `POST /payment/reserve` → `POST /payment/cancel`
  - `POST /inventory/reserve` → `POST /inventory/cancel`
  - `POST /delivery/reserve` → `POST /delivery/cancel`
- `GET /orders/{id}` – возвращает собранное состояние заказа.
- `GET /health` – проверка доступности сервиса.

Компенсации выполняются «best effort». В реальной системе список шагов нужно сохранять во внешнем сторе и повторять при сбоях.
