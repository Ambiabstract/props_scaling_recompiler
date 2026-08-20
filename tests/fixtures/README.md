# Fixture policy

Fixtures должны быть маленькими, неизменяемыми и иметь понятное происхождение.

- `vmf/` содержит synthetic Valve KeyValues документы для будущего source-preserving parser/writer.
- `gameinfo/` содержит synthetic ordered SearchPaths без зависимости от локальной установки Steam.
- `scale/` содержит test-only oracle, извлечённый из вручную проверенной Hammer++ compatibility-карты.
- MDL, QC, cache, VPK и end-to-end fixtures добавляются небольшими наборами перед реализацией соответствующего поведения.
- VMT/Patch fixtures не добавляются до отдельного исследования покраски.

Внешние VMF не должны становиться writable fixtures напрямую. Если из реального файла извлекается минимальный regression case, рядом фиксируются источник, hash снимка и смысл извлечённых данных.
