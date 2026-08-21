# Fixture policy

Fixtures должны быть маленькими, неизменяемыми и иметь понятное происхождение.

- `vmf/` содержит synthetic Valve KeyValues документы для будущего source-preserving parser/writer.
- `gameinfo/` содержит synthetic ordered SearchPaths без зависимости от локальной установки Steam.
- `scale/` содержит test-only oracle, извлечённый из вручную проверенной Hammer++ compatibility-карты.
- `mdl/` содержит декларативные synthetic cases для MDL versions, flags, `$cdmaterials`, skins и companions. Минимальные MDL/PHY строятся из этих данных во время теста; бинарные fixtures и outputs в репозитории не хранятся.
- `vmt/` содержит маленькую semantic matrix для обычного model material, существующего `$color2`, proxies, исходного Patch и неподдерживаемого shader. Те же байты используются folder- и synthetic VPK-тестами.
- QC, cache и end-to-end fixtures добавляются небольшими наборами перед реализацией соответствующего поведения. Минимальные VPK для contract-тестов генерируются детерминированно во временном каталоге через `srctools`.

Внешние VMF не должны становиться writable fixtures напрямую. Если из реального файла извлекается минимальный regression case, рядом фиксируются источник, hash снимка и смысл извлечённых данных.
