# Unicode comparison data

Display diagnostics only. Never normalize, replace or accept an identifier
based on these mappings. Full Unicode 17.0.0 confusables data are used, not a
hand-written Latin/Cyrillic subset.

Primary sources:

- https://www.unicode.org/Public/security/latest/confusables.txt (17.0.0 header;
  the local copy is immutable and SHA-256 pinned, not fetched at runtime)
- https://www.unicode.org/Public/17.0.0/ucd/DerivedCoreProperties.txt
- https://www.unicode.org/reports/tr39/tr39-32.html#Confusable_Detection
- https://www.unicode.org/license.txt

The Python and TypeScript helpers use one generated JSON dataset. Their
comparison is the `internalSkeleton` transform (NFD, Default_Ignorable removal,
prototype mapping, NFD), not the full bidirectional rendering algorithm.
Unicode mappings suggest possible visual confusion; they do not guarantee
identical rendering in every font. Normalization uses the host's standard
Unicode implementation. No skeleton is displayed or written back as an ID.

Source files and license are pinned below; source hashes, version and mapping
count are recorded in the generated dataset. The generator validates complete
source data and can check that the committed dataset is reproducible.
