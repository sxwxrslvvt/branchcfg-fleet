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
- LAN всех филиалов не пересекаются между собой.
- Сети сервисных профилей (`policies`) описывают исключительно инфраструктурные сервисы и не должны пересекаться с LAN любого филиала, независимо от назначения профиля конкретному филиалу.
- Совпадение сетей между разными сервисными профилями допустимо (дубликаты в итоговом address-list филиала удаляются), повторение сети внутри одного профиля запрещено.
- Неизвестные поля, повторяющиеся YAML-ключи, дубли профилей и некорректные ссылки на профили отклоняются.

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

## Режимы работы и проверка изменений

Команды CLI:

- `generate` (по умолчанию) — валидирует инвентарь и атомарно записывает `.rsc` и `inventory.json`. Перед началом записи проверяет наличие устаревших файлов в управляемых каталогах `output/sites/` и `output/hub/`. Если найдены старые файлы, генерация прерывается с кодом `2` без внесения частичных изменений.
- `check` — сверяет ожидаемый результат с текущим содержимым каталога вывода без записи на диск.
- `--diff` — выводит построчный unified diff между существующими и генерируемыми файлами.

```bash
python main.py check --config config.yaml --output output --diff
```

| Код выхода | Значение |
| --- | --- |
| `0` | Успешная генерация или полное совпадение файлов в режиме `check` |
| `1` | Ошибка валидации инвентаря, синтаксиса шаблона или ошибка ФС |
| `2` | Обнаружены устаревшие файлы (в `generate` и `check`) или зафиксирован drift файлов (в `check`) |

Одинаковый инвентарь всегда даёт идентичный результат. Генератор не выполняет автоматическую очистку или удаление файлов: устаревшие `.rsc` перечисляются в логе и должны быть проверены и удалены оператором вручную.

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

- **Адресные списки не создают правила firewall**: генератор создаёт только записи `/ip firewall address-list`. Сами правила фильтрации трафика и NAT необходимо настраивать на оборудовании отдельно.
- **Генератор не настраивает VPN и маршрутизацию**: связность между филиалами и хабом (туннели, маршруты, BGP/OSPF) не конфигурируется этим инструментом.
- **Импорт `.rsc` не гарантирует идемпотентность**: повторный вызов `add list=...` на RouterOS может приводить к дублированию записей или ошибкам при совпадении параметров.
- **Удаление записи из YAML не удаляет её с RouterOS**: инструмент не имеет прямого подключения к оборудованию и не отправляет команды удаления.
- **Тесты в CI не подтверждают работу на реальном MikroTik**: автоматическое тестирование проверяет синтаксическую корректность шаблонов, структуру inventory и логику работы CLI, но не заменяет функциональное тестирование на RouterOS или CHR.
- Поддерживаются только IPv4 и схемы без наложения филиальных LAN. Конфигурации VRF, NAT-overlap и IPv6 не моделируются.
- Атомарная запись выполняется отдельно для каждого файла; транзакции на весь каталог целиком нет.

## Справочные материалы

- [MikroTik: Address-lists](https://help.mikrotik.com/docs/spaces/ROS/pages/130220135/Address-lists)
- [Python: ipaddress](https://docs.python.org/3/library/ipaddress.html)
- [Jinja: StrictUndefined](https://jinja.palletsprojects.com/en/stable/api/)
