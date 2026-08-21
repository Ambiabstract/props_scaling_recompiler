# Fixture policy

Fixtures должны быть маленькими, неизменяемыми и иметь понятное происхождение.

- `vmf/` содержит synthetic Valve KeyValues документы для будущего source-preserving parser/writer.
- `gameinfo/` содержит synthetic ordered SearchPaths без зависимости от локальной установки Steam.
- `scale/` содержит test-only oracle, извлечённый из вручную проверенной Hammer++ compatibility-карты: отдельно фиксирует 35 parsing cases и model-dependent geometry cases для one-bone non-static, static и multi-bone non-static MDL.
- `mdl/` содержит декларативные synthetic cases для MDL versions, flags, `$cdmaterials`, полной skin-reference table, отдельно заданных mesh-used material slots и companions. Минимальные MDL/PHY строятся из этих данных во время теста; бинарные fixtures и outputs в репозитории не хранятся.
- `vmt/` содержит маленькую semantic matrix для обычного model material, существующего `$color2`, proxies, исходного Patch и неподдерживаемого shader. Те же байты используются folder- и synthetic VPK-тестами.
- `cache/` содержит минимальный pre-release schema v0 для проверки миграции и намеренно повреждённый JSON для recovery-контракта.
- `qc/` содержит synthetic dynamic/physics и static QC с `$scale`, LOD, collision, skin families, line/block comments и разным formatting. Они проверяют token-aware/source-preserving contract без запуска Crowbar или studiomdl.
- End-to-end fixtures добавляются небольшими наборами перед реализацией соответствующего поведения. Минимальные VPK для contract-тестов генерируются детерминированно во временном каталоге через `srctools`.

Внешние VMF не должны становиться writable fixtures напрямую. Если из реального файла извлекается минимальный regression case, рядом фиксируются источник, hash снимка и смысл извлечённых данных.
