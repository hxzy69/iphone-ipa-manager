# Supplemental notices

Some upstream source archives and Python wheels do not include a separate license
file. `notices/` preserves available notices from pinned upstream revisions and
the metadata provided by the corresponding release. `notices/SOURCES.json`
records retrieved upstream text locations. Metadata-based MIT notices identify
the original package author and declared license without changing that license.

The build also collects notices packaged with installed Python distributions and
Cargo crates. Its inventories distinguish the full build environment from the
smaller set of libraries actually bundled in the executable. Unavailable notice
files are represented by an empty notice list and their declared license remains
recorded; do not interpret a missing text file as a license exemption.

GPL transport sources and the LGPL signing source are supplied under `vendor/`,
with their license texts, rather than being replaced by these supplemental files.
