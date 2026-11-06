"""Extract readable modules from an x86-64 ELF Bun standalone executable.

python tools/extract_runner.py localRunner/blitz-challenge-linux --out localRunner/research/extracted

Supports the 52-byte module records and graph flags through bit 8 used by this
runner. Format reference: oven-sh/bun, bun-v1.4.4,
src/standalone_graph/StandaloneModuleGraph.rs. No recovered code is executed.
"""

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath

ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
OFFSETS = struct.Struct("<Q6I")
MODULE = struct.Struct("<12I4B")
TRAILER = b"\n---- Bun! ----\n"
KNOWN_FLAGS = (1 << 9) - 1
PREFIX = "/$bunfs/root/"
FORMAT_SOURCE = (
    "https://github.com/oven-sh/bun/blob/bun-v1.4.4/"
    "src/standalone_graph/StandaloneModuleGraph.rs"
)


class ExtractionError(ValueError):
    pass


def bounded(data: bytes, offset: int, length: int, label: str) -> bytes:
    if offset < 0 or length < 0 or offset > len(data) or length > len(data) - offset:
        raise ExtractionError(f"{label} is outside its containing buffer")
    return data[offset:offset + length]


def c_string(data: bytes, offset: int, label: str) -> str:
    if offset < 0 or offset >= len(data):
        raise ExtractionError(f"{label} offset is outside its string table")
    end = data.find(b"\0", offset)
    if end == -1:
        raise ExtractionError(f"{label} has no terminator")
    try:
        return data[offset:end].decode("utf8")
    except UnicodeDecodeError as error:
        raise ExtractionError(f"{label} is not UTF-8") from error


def bun_section(data: bytes) -> tuple[int, bytes]:
    bounded(data, 0, ELF_HEADER.size, "ELF header")
    header = ELF_HEADER.unpack_from(data)
    ident, machine = header[0], header[2]
    if ident[:7] != b"\x7fELF\x02\x01\x01" or machine != 62 or header[3] != 1:
        raise ExtractionError("unsupported executable: expected little-endian x86-64 ELF")
    section_offset, header_size, entry_size, count, names_index = (
        header[6], header[8], header[11], header[12], header[13]
    )
    if header_size != ELF_HEADER.size or entry_size != SECTION_HEADER.size:
        raise ExtractionError("unsupported ELF header/section record size")
    if not count or names_index >= count:
        raise ExtractionError("missing or unsupported extended ELF section numbering")
    table = bounded(data, section_offset, count * entry_size, "ELF section table")
    sections = list(SECTION_HEADER.iter_unpack(table))
    names_header = sections[names_index]
    if names_header[1] != 3:
        raise ExtractionError("ELF section names are not a string table")
    names = bounded(data, names_header[4], names_header[5], "ELF section names")
    matches = [
        section for section in sections
        if c_string(names, section[0], "ELF section name") == ".bun"
    ]
    if len(matches) != 1 or matches[0][1] != 1:
        raise ExtractionError("expected exactly one .bun PROGBITS section")
    section = matches[0]
    return section[4], bounded(data, section[4], section[5], ".bun section")


def safe_name(name: str) -> str:
    relative = name[len(PREFIX):] if name.startswith(PREFIX) else name
    parts = relative.split("/")
    if (
        any(part in ("", ".", "..") for part in parts)
        or "\\" in relative
        or ":" in relative
        or any(ord(char) < 32 or ord(char) == 127 for char in relative)
    ):
        raise ExtractionError(f"unsafe embedded filename: {name!r}")
    return relative


def parse_executable(data: bytes) -> tuple[dict, list[tuple[str, bytes]]]:
    section_offset, section = bun_section(data)
    bounded(section, 0, 8, ".bun length header")
    graph_size = struct.unpack_from("<Q", section)[0]
    if graph_size != len(section) - 8:
        raise ExtractionError(".bun length does not match the section size")
    graph = bounded(section, 8, graph_size, "Bun graph")
    footer_at = len(graph) - len(TRAILER) - OFFSETS.size
    footer = bounded(graph, footer_at, OFFSETS.size, "Bun offsets footer")
    if graph[-len(TRAILER):] != TRAILER:
        raise ExtractionError("invalid Bun graph trailer")
    byte_count, table_at, table_size, entry, argv_at, argv_size, flags = OFFSETS.unpack(footer)
    if byte_count != footer_at:
        raise ExtractionError("Bun byte_count does not match footer position")
    if flags & ~KNOWN_FLAGS:
        raise ExtractionError(f"unsupported Bun graph flags: {flags:#x}")
    body = graph[:byte_count]
    table = bounded(body, table_at, table_size, "Bun module table")
    if not table_size or table_size % MODULE.size:
        raise ExtractionError("unsupported Bun module record size")
    count = table_size // MODULE.size
    if entry >= count:
        raise ExtractionError("entry point is outside the module table")
    argv = bounded(body, argv_at, argv_size, "compile arguments")
    if bounded(body, argv_at + argv_size, 1, "compile argument terminator") != b"\0":
        raise ExtractionError("compile arguments have no terminator")

    record_at = table_at + table_size

    def consume(length: int, label: str) -> bytes:
        nonlocal record_at
        result = bounded(body, record_at, length, label)
        record_at += length
        return result

    def pointer(record: bytes, label: str) -> tuple[int, bytes]:
        offset, length = struct.unpack("<II", record)
        return offset, bounded(body, offset, length, label)

    artifacts: list[tuple[str, bytes]] = []

    def artifact(path: str, payload: bytes) -> dict:
        artifacts.append((path, payload))
        return {
            "path": path,
            "length": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    hashes = []
    if flags & (1 << 5):
        hashes = list(struct.unpack(f"<{count}I", consume(4 * count, "source hashes")))
    builtin_count = 0
    if flags & (1 << 6):
        builtin_count = struct.unpack("<I", consume(4, "builtin bytecode count"))[0]
        records = consume(builtin_count * 12, "builtin bytecode records")
        for index in range(builtin_count):
            builtin_id, offset, length = struct.unpack_from("<III", records, index * 12)
            artifact(
                f"builtins/{index}-{builtin_id}.bin",
                bounded(body, offset, length, "builtin bytecode"),
            )
    if flags & (1 << 7):
        _, payload = pointer(consume(8, "bytecode string table pointer"), "bytecode string table")
        artifact("metadata/bytecode-strings.bin", payload)
    startup_count = None
    if flags & (1 << 8):
        startup_count = struct.unpack("<I", consume(4, "startup module count"))[0]
        if startup_count > count:
            raise ExtractionError("startup module count exceeds the module count")
    if argv_at != record_at or argv_at + argv_size + 1 != byte_count:
        raise ExtractionError("unsupported data between module metadata and footer")

    modules = []
    names = set()
    for index, record in enumerate(MODULE.iter_unpack(table)):
        regions = []
        for region_index, label in enumerate(
            ("name", "contents", "sourcemap", "bytecode", "module_info", "bytecode_origin")
        ):
            offset, length = record[2 * region_index:2 * region_index + 2]
            payload = bounded(body[:table_at], offset, length, f"module {index} {label}")
            regions.append((offset, payload))
        name_at, name_bytes = regions[0]
        name = c_string(body[:table_at], name_at, f"module {index} name")
        if name.encode("utf8") != name_bytes:
            raise ExtractionError("module name length does not match its terminator")
        relative = safe_name(name)
        if relative in names:
            raise ExtractionError(f"duplicate module filename: {name!r}")
        names.add(relative)
        encoding, loader, module_format, side = record[12:]
        if encoding not in (0, 1, 2) or module_format not in (0, 1, 2) or side not in (0, 1):
            raise ExtractionError("unsupported module encoding, format, or side")
        contents_at, contents = regions[1]
        if encoding == 2 and (contents_at % 2 or len(contents) % 2):
            raise ExtractionError("unaligned UTF-16 module contents")
        module = {
            "index": index,
            "name": name,
            "encoding": encoding,
            "loader": loader,
            "module_format": module_format,
            "side": side,
            "contents_graph_offset": contents_at,
            "contents_file_offset": section_offset + 8 + contents_at,
            "source_hash": hashes[index] if hashes else None,
            "contents": artifact(f"raw/{relative}", contents),
        }
        for label, (_, payload) in zip(
            ("sourcemap", "bytecode", "module_info", "bytecode_origin"), regions[2:]
        ):
            if payload:
                module[label] = artifact(f"{label}/{relative}.bin", payload)
        modules.append(module)

    paths = {path for path, _ in artifacts}
    for path in paths:
        if any(str(parent) in paths for parent in PurePosixPath(path).parents):
            raise ExtractionError(f"conflicting artifact paths: {path!r}")
    versions = sorted(set(
        match.decode("ascii")
        for match in re.findall(rb"bun-v(\d+\.\d+\.\d+)(?![\d.])", data)
    ))
    manifest = {
        "format": "Bun ELF64 graph / 52-byte modules / flags through bit 8",
        "format_source": FORMAT_SOURCE,
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "input_length": len(data),
        "embedded_bun_version_markers": versions,
        "section_offset": section_offset,
        "section_length": len(section),
        "graph_length": graph_size,
        "module_table_offset": table_at,
        "module_table_length": table_size,
        "flags": flags,
        "entry_point": entry,
        "startup_module_count": startup_count,
        "builtin_bytecode_count": builtin_count,
        "compile_arguments_hex": argv.hex(),
        "modules": modules,
    }
    return manifest, artifacts


def extract(source: Path, output: Path) -> dict:
    manifest, artifacts = parse_executable(source.read_bytes())
    # A fresh directory avoids following pre-existing output symlinks or overwriting data.
    output.mkdir(parents=True, exist_ok=False)
    for relative, payload in artifacts:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as file:
            file.write(payload)
    with (output / "manifest.json").open("x", encoding="utf8") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    try:
        manifest = extract(args.executable, args.out)
    except (ExtractionError, OSError) as error:
        parser.exit(1, f"extraction failed: {error}\n")
    print(f"Extracted {len(manifest['modules'])} module(s) into {args.out}")
    print(f"Input SHA-256: {manifest['input_sha256']}")


if __name__ == "__main__":
    main()
