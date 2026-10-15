# branchcfg-fleet

Генератор адресных списков MikroTik RouterOS для сети филиалов. Источник данных — YAML-инвентарь; результат — готовые к проверке `.rsc` и сводный `inventory.json`.

[![CI](https://github.com/sxwxrslvvt/branchcfg-fleet/actions/workflows/ci.yml/badge.svg)](https://github.com/sxwxrslvvt/branchcfg-fleet/actions/workflows/ci.yml)

Python 3.12+ · Jinja2 · PyYAML · Docker Compose

## Задача

При большом количестве филиалов адреса LAN и перечни доступных сервисных сетей легко расходятся между таблицей учёта и конфигурациями оборудования. Здесь они описаны в одном файле. Перед генерацией проверяются формат CIDR, пересечения LAN, имена и профили доступа.

Генератор **не применяет настройки на устройствах** и не создаёт VPN, маршруты или правила firewall. Он готовит только записи `/ip firewall address-list`. Адресный список сам по себе не ограничивает доступ: его нужно использовать в правилах маршрутизатора.

## Запуск

Нужны Docker Engine и Compose plugin:

```bash
docker compose up --build
```

Конфигурации появятся в `output/`. Процесс выполнится один раз и завершится.

Проверка расхождений с уже сформированными файлами, без перезаписи:

```bash
docker compose run --rm branchcfg check --diff
```

Локальный запуск без Docker:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py generate --config config.example.yaml --output output
python main.py check --config config.example.yaml --output output --diff
python -m unittest discover -s tests -v
```

На Linux для записи в каталог текущим пользователем:

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose up --build
```

## Инвентарь

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

Полный пример — [`config.example.yaml`](config.example.yaml). Для собственных данных создай `config.yaml` (он добавлен в `.gitignore`). В Docker Compose поменяй `BRANCHCFG_CONFIG` на `/work/config.yaml`.

Правила валидации:

- `id` филиала и имя профиля: 2–32 символа, строчные латинские буквы, цифры и дефис; начало — буква, конец — буква или цифра.
- LAN и сервисные сети: точные IPv4 CIDR внутри RFC1918, без установленных host-битов.
- LAN филиалов не пересекаются между собой. LAN филиала не должна пересекаться с сервисными сетями **назначенных ему** профилей.
- Неизвестные поля, повторяющиеся YAML-ключи, дубли профилей и некорректные ссылки на профили отклоняются.
- Сети разных профилей могут совпадать: при генерации дубликаты удаляются.

## Результат

```text
output/
├── hub/branch-lans.rsc
├── sites/
│   ├── branch-001.rsc
│   ├── branch-002.rsc
│   └── branch-003.rsc
└── inventory.json
```

Фрагмент списка сетей филиалов на центральном маршрутизаторе:

```routeros
/ip firewall address-list
add list=branchcfg_lans address=10.21.1.0/24 comment="branchcfg:branch-001"
```

Фрагмент списка сетей сервисов для филиала:

```routeros
/ip firewall address-list
add list=branchcfg_allowed address=10.200.10.0/24 comment="branchcfg:branch-001"
```

Содержимое `examples/generated/` — эталон вывода для тестового инвентаря. CI сверяет его с текущими шаблонами. Если меняются шаблоны или `config.example.yaml`, пересоздай эталон:

```bash
python main.py generate --output examples/generated
```

## Проверка изменений

```bash
python main.py check --config config.yaml --output output --diff
```

| Код выхода | Значение |
| --- | --- |
| `0` | Успешная генерация или совпадение результатов в режиме `check` |
| `1` | Некорректные данные, ошибка шаблона или операция с файлами |
| `2` | `check` обнаружил отсутствующий, изменённый или устаревший файл |

Одинаковый инвентарь даёт одинаковый результат. При генерации изменённые файлы заменяются атомарно, по одному. Лишние `.rsc` в `output/hub/` и `output/sites/` обнаруживаются, но не удаляются.

## Переменные окружения

| Переменная | Значение по умолчанию | Назначение |
| --- | --- | --- |
| `BRANCHCFG_CONFIG` | `config.example.yaml` | Путь к инвентарю |
| `BRANCHCFG_OUTPUT` | `output` | Каталог результата |
| `BRANCHCFG_LOG_LEVEL` | `INFO` | Уровень логирования |
| `LOCAL_UID`, `LOCAL_GID` | `1000` | UID/GID контейнера при запуске через Compose |

CLI-параметры `--config`, `--output` и `--log-level` переопределяют переменные окружения.

## Устройство проекта

```text
config.example.yaml
       │
       ▼
  PyYAML + ipaddress ── проверка входных данных
       │
       ▼
  Jinja2 / StrictUndefined
       │
       ├── hub/*.rsc
       ├── sites/*.rsc
       └── inventory.json
       │
       ▼
  generate / check --diff
```

- `main.py` — парсинг и валидация, генерация, сравнение и запись файлов.
- `templates/` — шаблоны RouterOS.
- `tests/` — тесты входных данных, предсказуемости вывода и обнаружения расхождений.
- `.github/workflows/ci.yml` — тесты на Python 3.12 и 3.13, сверка эталона и сборка Docker.

### Ограничения

- Импорт `.rsc` **не идемпотентен**: повторный `add` может создать повторяющиеся записи. Записи, удалённые из YAML, не удаляются с маршрутизатора. Перед импортом сравни состояние устройства с ожидаемым и проверь скрипт на тестовой RouterOS/CHR.
- Это генератор адресных списков, а не система доставки конфигураций. Подключение к устройствам, rollback, маршрутизация и firewall-правила находятся вне проекта.
- Поддерживаются только IPv4 и сети без наложения филиальных LAN. Конфигурации VRF, NAT-overlap и IPv6 не моделируются.
- Атомарная запись выполняется отдельно для каждого файла; общей транзакции на весь каталог нет.

## Справочные материалы

- [MikroTik: Address-lists](https://help.mikrotik.com/docs/spaces/ROS/pages/130220135/Address-lists)
- [Python: ipaddress](https://docs.python.org/3/library/ipaddress.html)
- [Jinja: StrictUndefined](https://jinja.palletsprojects.com/en/stable/api/)
