# branchcfg-fleet

**Inventory-driven генератор RouterOS address-lists для распределённой сети филиалов.**

Python 3.12+ · Jinja2 · PyYAML · Docker Compose · unittest · GitHub Actions

Один YAML хранит филиальные LAN и профили доступа. Генератор проверяет адресацию и формирует воспроизводимые `.rsc` для центрального маршрутизатора и филиалов, плюс JSON-инвентарь для интеграций.

> Проект генерирует **адресные списки**, а не готовую VPN или firewall. Список сам по себе **не разрешает и не запрещает трафик**: RouterOS filter rules и маршрутизация создаются и проверяются отдельно.

## Возможности

- Валидация структуры YAML, имён филиалов, IPv4 CIDR и RFC1918.
- Выявление дублирующихся филиалов, пересечения LAN и конфликтов с целевыми сетями.
- Профили доступа (`pos`, `office`), объединение адресов без дублей.
- Детерминированный вывод: одинаковый inventory → одинаковые файлы.
- `check` для CI: код возврата `2` при изменении или устаревшем `.rsc`.
- `--diff`: unified diff, без изменения файлов в `check`.
- Атомарная замена изменившихся файлов; старые `.rsc` не удаляются автоматически.
- Контейнер без сети и Linux capabilities, с read-only rootfs.

## Структура

```text
branchcfg-fleet/
├── main.py                     # CLI, парсер и проверки, генератор, diff
├── config.example.yaml         # Демонстрационная топология (3 филиала)
├── templates/
│   ├── hub.rsc.j2              # Список LAN филиалов для хаба
│   └── site.rsc.j2             # Разрешённые сети сервисов для филиала
├── tests/test_main.py          # Тесты проверок, drift и детерминизма
├── examples/generated/         # Эталонный вывод; сверяется в CI
├── .github/workflows/ci.yml    # Проверка на Python 3.12 и 3.13
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
└── .gitignore
```

## Быстрый старт

Требуются Docker Engine и Compose plugin:

```bash
docker compose up --build
```

Это **одноразовая задача**: контейнер генерирует файлы в `./output/` и завершает работу. Он не разворачивает демон и не подключается к маршрутизаторам.

На Linux, если UID/GID пользователя отличаются от `1000:1000`, запустите:

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose up --build
```

Проверка расхождения без перезаписи:

```bash
docker compose run --rm branchcfg check --diff
```

Локально, без Docker:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py generate --config config.example.yaml --output output
python main.py check --config config.example.yaml --output output --diff
python -m unittest discover -s tests -v
```

## Входная конфигурация

```yaml
version: 1
policies:
  pos:
    - 10.200.10.0/24
    - 10.200.20.10/32
  office:
    - 10.200.30.0/24
sites:
  - id: branch-001
    lan: 10.21.1.0/24
    profiles: [pos]
  - id: branch-002
    lan: 10.21.2.0/24
    profiles: [pos, office]
```

Скопируйте `config.example.yaml` в `config.yaml` и отредактируйте. При локальном запуске укажите `--config config.yaml`. Для Docker Compose замените путь `BRANCHCFG_CONFIG` на `/work/config.yaml` в `docker-compose.yml` (файл монтируется вместе с каталогом проекта).

- `id` и названия профилей: строчные латинские буквы, цифры и дефисы, от 2 до 32 символов; первый — буква.
- Все сети должны быть точными IPv4 CIDR внутри RFC1918, без host-битов (`10.21.1.0/24`, не `10.21.1.4/24`).
- LAN филиалов не могут пересекаться друг с другом или с сетями, доступ к которым задаётся через профиль.
- Пересечение между сетями **разных профилей** допустимо: при генерации они объединяются.
- Пустые списки и неизвестные поля считаются ошибкой.

## Результат

```text
output/
├── hub/
│   └── branch-lans.rsc
├── sites/
│   ├── branch-001.rsc
│   ├── branch-002.rsc
│   └── branch-003.rsc
└── inventory.json
```

В `hub/branch-lans.rsc`:

```routeros
/ip firewall address-list
add list=branchcfg_lans address=10.21.1.0/24 comment="branchcfg:branch-001"
```

В `sites/branch-001.rsc`:

```routeros
/ip firewall address-list
add list=branchcfg_allowed address=10.200.10.0/24 comment="branchcfg:branch-001"
add list=branchcfg_allowed address=10.200.20.10/32 comment="branchcfg:branch-001"
```

Сгенерированные скрипты **additive-only**: команда `add` добавляет записи; повторный импорт может создать дубликаты, удалённые из YAML сети останутся в RouterOS. Это намеренное ограничение: генератор не удаляет настройки удалённого маршрутизатора без контроля оператора. При изменениях сравнивайте списки и удаляйте старые записи вручную.

**Не импортируйте на рабочий маршрутизатор без ревью.** Для проверки используйте отдельное тестовое устройство/CHR. Требуются отдельные правила firewall, маршруты и проверка того, что объекты списков используются в нужных цепочках.

## CLI и коды возврата

```bash
python main.py generate --config config.yaml --output output
python main.py check --config config.yaml --output output --diff
```

| Код | Значение |
| --- | --- |
| `0` | Успех, либо `check` не нашёл расхождений |
| `1` | Ошибка входных данных, чтения, записи или шаблона |
| `2` | `check`: файлы отсутствуют/отличаются или есть старые `.rsc` |

Устаревшие файлы в `output/hub/` и `output/sites/` обнаруживаются, но не удаляются. Файлы в других папках не контролируются. Один файл заменяется атомарно, но **нет атомарной транзакции для всех файлов сразу**.

## Переменные окружения

| Имя | По умолчанию | Для чего |
| --- | --- | --- |
| `BRANCHCFG_CONFIG` | `config.example.yaml` | YAML inventory |
| `BRANCHCFG_OUTPUT` | `output` | Каталог результатов |
| `BRANCHCFG_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOCAL_UID`, `LOCAL_GID` | `1000`, `1000` | Пользователь процесса в Compose (Linux bind mount) |

Аргументы `--config`, `--output`, `--log-level` имеют приоритет над окружением. Секреты и WireGuard private keys в inventory **не нужны**; не добавляйте конфигурации реальных сетей с внутренними данными в публичный GitHub.

## Архитектура и ограничения

```text
config.yaml
   │
   ▼
PyYAML safe_load → типы/поля → ipaddress/проверка пересечений
   │
   ▼
Jinja2 StrictUndefined → сортировка/дедупликация
   │
   ├── hub/*.rsc
   ├── sites/*.rsc
   └── inventory.json
   │
   ▼
check/diff или атомарная запись
```

Список филиальных сетей на хабе и списки разрешённых сетей на филиалах генерируются офлайн. Управление доступом зависит от отдельно настроенного сетевого оборудования. Модель рассчитана на IPv4 и плоскую адресацию без VRF/NAT-overlap; для иных топологий нужна отдельная схема inventory.

В `examples/generated/` сохранён эталонный вывод для демонстрационного inventory. CI проверяет, что он соответствует `config.example.yaml`. После изменения примера обновите его командой `python main.py generate --output examples/generated` и включите обновлённые файлы в тот же PR.

Рекомендованный процесс для команды: **изменение YAML → генерация → PR → CI → просмотр diff → ручной импорт в тест → согласованное внедрение**.

## Документация

- [RouterOS address-lists](https://help.mikrotik.com/docs/spaces/ROS/pages/130220135/Address-lists)
- [Jinja2 API и StrictUndefined](https://jinja.palletsprojects.com/en/stable/api/)
- [Python ipaddress](https://docs.python.org/3/library/ipaddress.html)
- [Docker Compose build](https://docs.docker.com/reference/compose-file/build/)
