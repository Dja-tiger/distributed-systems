# Distributed transactions homework

Набор сервисов демонстрирует реализацию распределённой транзакции по паттерну **Orchestrated Saga**. Заказ инициирует шаги оплаты, резервирования товара и бронирования доставки. При неуспехе любого шага оркестратор вызывает компенсирующие операции для уже завершённых шагов.

## Паттерн
- **Saga (оркестратор)** – сервис заказа управляет последовательностью шагов и хранит список компенсаций.
- Каждый сервис предоставляет пару REST-эндпоинтов: действие и компенсацию (`/reserve` + `/cancel`).
- При ошибке шага оркестратор вызывает компенсации в обратном порядке, обеспечивая откат распределённой транзакции.
- Для тестов предусмотрены флаги `force_*_failure`, чтобы эмулировать отказ конкретного шага.

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

### Кластер на Docker (kind)
Если нужно поднимать Kubernetes прямо на Docker, используйте kind:
1. Создайте кластер (проброс портов 80/443 описан в `kind-config.yaml`):
   ```bash
   kind create cluster --name saga --config kind-config.yaml
   ```
2. Соберите образ и загрузите его в кластер kind:
   ```bash
   docker build -t saga-demo:latest .
   kind load docker-image saga-demo:latest --name saga
   ```
3. Установите ingress-nginx для kind:
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
   kubectl wait --namespace ingress-nginx --for=condition=available deployment/ingress-nginx-controller
   ```
4. Примените манифесты приложения:
   ```bash
   kubectl apply -f k8s/namespace.yaml
   kubectl apply -f k8s/deployments.yaml
   kubectl apply -f k8s/services.yaml
   kubectl apply -f k8s/ingress.yaml
   ```
5. Пропишите `arch.homework` на localhost (Ingress проброшен на 80/443):
   ```bash
   echo "127.0.0.1 arch.homework" | sudo tee -a /etc/hosts
   ```
6. Проверка:
   ```bash
   curl http://arch.homework/health
   curl -X POST http://arch.homework/orders -H "Content-Type: application/json" \
     -d '{"order_id":"demo-1","amount":10,"sku":"SKU-1","quantity":1,"slot":"2024-05-20T10:00"}'
   ```

### Minikube (альтернативно)
1. Соберите образ и сделайте его доступным для кластера:
   ```bash
   eval "$(minikube docker-env)"
   docker build -t saga-demo:latest .
   ```
2. Создайте namespace и установите манифесты:
   ```bash
   kubectl apply -f k8s/namespace.yaml
   kubectl apply -f k8s/deployments.yaml
   kubectl apply -f k8s/services.yaml
   kubectl apply -f k8s/ingress.yaml
   ```
3. Ingress использует домен `arch.homework`. Добавьте запись в `/etc/hosts`, указывая на адрес ingress-контроллера (для Minikube: `minikube ip`).
4. Проверка:
   ```bash
   curl http://arch.homework/health
   curl -X POST http://arch.homework/orders -H "Content-Type: application/json" \
     -d '{"order_id":"demo-1","amount":10,"sku":"SKU-1","quantity":1,"slot":"2024-05-20T10:00"}'
   ```

## Postman тесты
Коллекция `postman/DistributedTransactions.postman_collection.json` содержит сценарии:
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
