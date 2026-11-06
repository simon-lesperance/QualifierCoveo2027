"""Synthetic Bun ELF fixtures; no launcher or recovered code is required."""

import struct
import tempfile
import unittest
from pathlib import Path

import extract_runner as runner


def fixture(names=("/$bunfs/root/main.js",), *, flags=0, contents=b"export default 42;\n"):
    body = bytearray(contents + b"\0")
    modules = []
    for name in names:
        encoded = name.encode("utf8")
        name_at = len(body)
        body.extend(encoded + b"\0")
        modules.append(runner.MODULE.pack(
            name_at, len(encoded), 0, len(contents), 0, 0, 0, 0, 0, 0, 0, 0,
            1, 1, 1, 0,
        ))
    table_at = len(body)
    table = b"".join(modules)
    body.extend(table)
    if flags & (1 << 5):
        body.extend(b"\0" * (4 * len(names)))
    if flags & (1 << 6):
        body.extend(struct.pack("<I", 0))
    if flags & (1 << 7):
        body.extend(struct.pack("<II", 0, 0))
    if flags & (1 << 8):
        body.extend(struct.pack("<I", len(names)))
    argv_at = len(body)
    body.append(0)
    footer = runner.OFFSETS.pack(len(body), table_at, len(table), 0, argv_at, 0, flags)
    graph = bytes(body) + footer + runner.TRAILER
    section = struct.pack("<Q", len(graph)) + graph
    names_table = b"\0.shstrtab\0.bun\0"
    section_at = runner.ELF_HEADER.size + len(names_table)
    table_offset = section_at + len(section)
    header = runner.ELF_HEADER.pack(
        b"\x7fELF\x02\x01\x01" + b"\0" * 9, 2, 62, 1, 0, 0, table_offset, 0,
        runner.ELF_HEADER.size, 0, 0, runner.SECTION_HEADER.size, 3, 1,
    )
    sections = bytes(runner.SECTION_HEADER.size)
    sections += runner.SECTION_HEADER.pack(
        1, 3, 0, 0, runner.ELF_HEADER.size, len(names_table), 0, 0, 1, 0
    )
    sections += runner.SECTION_HEADER.pack(
        11, 1, 0, 0, section_at, len(section), 0, 0, 8, 0
    )
    return header + names_table + section + sections


def change_footer(data, field, value):
    data = bytearray(data)
    section_at, section = runner.bun_section(data)
    footer_at = section_at + len(section) - len(runner.TRAILER) - runner.OFFSETS.size
    fields = list(runner.OFFSETS.unpack_from(data, footer_at))
    fields[field] = value
    runner.OFFSETS.pack_into(data, footer_at, *fields)
    return bytes(data)


class ExtractRunnerTest(unittest.TestCase):
    def test_extracts_exact_bytes_with_optional_metadata_and_stable_manifest(self):
        data = fixture(flags=0x17C)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "runner"
            source.write_bytes(data)
            first = runner.extract(source, root / "one")
            second = runner.extract(source, root / "two")
            self.assertEqual(first, second)
            self.assertEqual(
                (root / "one/raw/main.js").read_bytes(), b"export default 42;\n"
            )
            module = first["modules"][0]
            offset = module["contents_file_offset"]
            self.assertEqual(data[offset:offset + module["contents"]["length"]],
                             (root / "one/raw/main.js").read_bytes())
            with self.assertRaises(FileExistsError):
                runner.extract(source, root / "one")

    def test_rejects_truncated_headers_sections_and_graphs(self):
        data = fixture()
        for candidate in (b"", data[:63], data[:-1], data[:100]):
            with self.subTest(length=len(candidate)), self.assertRaises(runner.ExtractionError):
                runner.parse_executable(candidate)
        section_at, _ = runner.bun_section(data)
        bad = bytearray(data)
        struct.pack_into("<Q", bad, section_at, len(data) * 2)
        with self.assertRaisesRegex(runner.ExtractionError, "length"):
            runner.parse_executable(bad)

    def test_rejects_invalid_pointers_entry_points_flags_and_trailer(self):
        for field, value in ((0, 0), (1, 0xFFFFFFFF), (2, 51), (3, 1),
                             (4, 0xFFFFFFFF), (6, 1 << 9)):
            with self.subTest(field=field), self.assertRaises(runner.ExtractionError):
                runner.parse_executable(change_footer(fixture(), field, value))
        bad = bytearray(fixture())
        section_at, section = runner.bun_section(bad)
        bad[section_at + len(section) - 1] = 0
        with self.assertRaisesRegex(runner.ExtractionError, "trailer"):
            runner.parse_executable(bad)
        bad = bytearray(fixture())
        struct.pack_into("<I", bad, section_at + 8 + len(b"export default 42;\n\0")
                         + len(b"/$bunfs/root/main.js\0") + 12, 0xFFFFFFFF)
        with self.assertRaisesRegex(runner.ExtractionError, "contents"):
            runner.parse_executable(bad)

    def test_rejects_unsafe_duplicate_and_conflicting_names_before_writing(self):
        for name in ("/tmp/x.js", "/$bunfs/root/../x.js", "/$bunfs/root/a/../x",
                     "/$bunfs/root/a\\x", "C:/x", "/$bunfs/root/", "a//b", "a\0b"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "runner"
                output = Path(folder) / "output"
                source.write_bytes(fixture((name,)))
                with self.assertRaises(runner.ExtractionError):
                    runner.extract(source, output)
                self.assertFalse(output.exists())
        for names in (("x.js", "x.js"), ("a", "a/b")):
            with self.subTest(names=names), self.assertRaises(runner.ExtractionError):
                runner.parse_executable(fixture(names))

    def test_rejects_unsupported_executable_and_missing_metadata(self):
        data = bytearray(fixture())
        data[4] = 1
        with self.assertRaisesRegex(runner.ExtractionError, "unsupported executable"):
            runner.parse_executable(data)
        with self.assertRaisesRegex(runner.ExtractionError, "unsupported data"):
            runner.parse_executable(change_footer(fixture(flags=0x17C), 6, 0))


if __name__ == "__main__":
    unittest.main()
