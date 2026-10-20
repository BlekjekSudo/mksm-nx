# Xbox executable compatibility

The current public Switch NRO was generated from this development executable:

| Field | Recorded value |
| --- | --- |
| Title | Mortal Kombat: Shaolin Monks |
| Platform | Original Xbox |
| Title ID | `4D570029` |
| Entry point | `000F40CA` |
| File size | 5,644,288 bytes |
| SHA-256 | `6153c7e525626daf9fef5a88f3186ca1ec5310d4dfb40598c08c3894b4db39c8` |
| Certificate version field | `3` |
| Certificate region mask | `0x7` |

The certificate values alone do not identify a retail disc revision, language edition or patch history.
Do not label this executable as game version 1.03 based on the certificate field.
The public NRO's exact hash requirement is the supported binary identity.

The desktop upstream documents a different reference SHA-256:
`48240902bf4a04eb89376217c529531032d3370749bcce91a194878cc49db74a`.
That executable is rejected by the current Switch NRO's whole-file check.
This does not show that the user's game copy is damaged.

## Why the title alone is insufficient

The NRO contains translated functions and address mappings generated from one XBE.
Startup loads the user's original headers and sections into guest memory and calls those translated functions.
The shared title and entry point do not establish that every function address, constant, import or initial data value matches.
Removing the identity check alone does not generate support for another executable.

A different whole-file hash also does not prove that machine code differs.
Header metadata, signatures, packing or executable patches can affect the whole-file hash.
Compare the actual files before changing the accepted set.

## Generate a report for another executable

From the repository root, run:

```powershell
python tools/report_switch_xbe.py --xbe "<your game folder>/default.xbe" --output ".private/xbe-report.json"
```

Use a new output filename if it already exists.
The report contains scalar metadata, layout fields and SHA-256 digests for each section.
It exports no game bytes, certificate keys or original file paths.
The checked-in `config/switch-xbe-reference.json` contains only the same metadata and digests for the development executable.

Share the JSON report for compatibility analysis without sharing the XBE.
`layout_and_section_hashes_match` means the compared layout fields and raw section digests match.
It does not prove identical header behavior or runtime compatibility and does not bypass the NRO guard.
Differences in code/data require a matching translation and an audit of the fixed ABI overrides.
An alternate build must be tested separately before listing it as supported.
