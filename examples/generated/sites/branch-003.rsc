# branchcfg: сети сервисов, доступные филиалу branch-003
# Это только address-list. Правила firewall и маршруты настраиваются отдельно.
# Импорт повторно может создать дубликаты; предварительно сверьте состояние.
/ip firewall address-list
add list=branchcfg_allowed address=10.200.20.10/32 comment="branchcfg:branch-003"
add list=branchcfg_allowed address=10.200.30.0/24 comment="branchcfg:branch-003"
