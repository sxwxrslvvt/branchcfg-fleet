# branchcfg: филиальные LAN для центрального RouterOS
# Список branchcfg_lans предназначен только для записей этого генератора.
# Скрипт не удаляет старые записи: сверяйте изменения до импорта.
/ip firewall address-list
add list=branchcfg_lans address=10.21.1.0/24 comment="branchcfg:branch-001"
add list=branchcfg_lans address=10.21.2.0/24 comment="branchcfg:branch-002"
add list=branchcfg_lans address=10.21.3.0/24 comment="branchcfg:branch-003"
