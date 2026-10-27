"""Rebuild small, nonexecuted native fixtures with LLVM and LLD.

Run from the repository root. Set LLVM_BIN and LLD_BIN to their bin directories;
the defaults discover tools from PATH. Cross-linking needs no target sysroots.
Mach-O dSYM generation additionally needs dsymutil. Fixture hashes and the
exact compiler version are recorded in manifest.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESTINATION = ROOT / "tests/fixtures/binary"
SOURCE = DESTINATION / "source.c"
TARGETS = (
    ("i386", "i386-linux-gnu", "i386", "", ()),
    ("x86_64", "x86_64-linux-gnu", "x86_64", "", ()),
    ("arm", "armv7-linux-gnueabihf", "arm", "arm", ("-marm",)),
    ("thumb", "armv7-linux-gnueabihf", "arm", "thumb", ("-mthumb",)),
    ("aarch64", "aarch64-linux-gnu", "aarch64", "", ()),
    ("riscv32", "riscv32-linux-gnu", "riscv32", "", ()),
    ("riscv64", "riscv64-linux-gnu", "riscv64", "", ()),
    ("ppc64le", "powerpc64le-linux-gnu", "ppc64le", "", ()),
    ("ppc64", "powerpc64-linux-gnu", "ppc64", "", ("-mabi=elfv2",)),
    ("s390x", "s390x-linux-gnu", "s390x", "", ()),
    ("mipsel", "mipsel-linux-gnu", "mipsel", "mips32r2", ("-march=mips32r2",)),
    ("mips", "mips-linux-gnu", "mips", "mips32r2", ("-march=mips32r2",)),
    ("mips64el", "mips64el-linux-gnuabi64", "mips64el", "mips64r2", ("-march=mips64r2",)),
    ("mips64", "mips64-linux-gnuabi64", "mips64", "mips64r2", ("-march=mips64r2",)),
    ("loongarch64", "loongarch64-linux-gnu", "loongarch64", "", ("-mno-relax",)),
)


def tool(name: str, *, linker: bool = False) -> str:
    directory = os.environ.get("LLD_BIN" if linker else "LLVM_BIN")
    path = str(Path(directory) / name) if directory else shutil.which(name)
    if not path:
        raise ValueError(f"Fixture generation requires {name}")
    return path


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True)  # noqa: S603


def main() -> None:
    clang = tool("clang")
    linker = tool("ld.lld", linker=True)
    common = [
        "-g",
        "-gdwarf-4",
        "-O2",
        "-ffreestanding",
        "-fno-stack-protector",
        "-fno-optimize-sibling-calls",
        "-fdebug-compilation-dir=/trailmark-binary-fixture",
        f"-fdebug-prefix-map={ROOT}=/trailmark-binary-fixture",
    ]
    records: list[dict[str, object]] = []
    for name, triple, architecture, mode, extra in TARGETS:
        filename = f"elf-{name}"
        command = [
            clang,
            f"--target={triple}",
            f"--ld-path={linker}",
            *common,
            *extra,
            "-nostdlib",
            "-Wl,-e,entry",
            "-Wl,--build-id=sha1",
            "tests/fixtures/binary/source.c",
            "-o",
            str(DESTINATION / filename),
        ]
        try:
            run(command)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Cannot build {name}: {error.stderr.decode()}") from error
        records.append(
            {
                "file": filename,
                "format": "ELF",
                "architecture": architecture,
                "mode": mode,
                "triple": triple,
                "flags": [*common[:5], *extra],
            }
        )
    # N32 is an ELF32 container carrying MIPS64 instructions.
    n32 = DESTINATION / "elf-mipsn32"
    run(
        [
            clang,
            "--target=mips64el-linux-gnuabin32",
            f"--ld-path={linker}",
            *common,
            "-march=mips64r2",
            "-nostdlib",
            "-Wl,-e,entry",
            "-Wl,--build-id=sha1",
            "tests/fixtures/binary/source.c",
            "-o",
            str(n32),
        ]
    )
    records.append(
        {"file": n32.name, "format": "ELF", "architecture": "mips64el", "mode": "mips64r2"}
    )
    run(
        [
            clang,
            "--target=armv7-linux-gnueabihf",
            f"--ld-path={linker}",
            *common,
            "-nostdlib",
            "-Wl,-e,arm_caller",
            "-Wl,--build-id=sha1",
            "tests/fixtures/binary/mixed-arm.c",
            "-o",
            str(DESTINATION / "elf-mixed-arm"),
        ]
    )
    run(
        [
            clang,
            "--target=x86_64-linux-gnu",
            f"--ld-path={linker}",
            *common,
            "-nostdlib",
            "-shared",
            "-fPIC",
            "-Wl,--build-id=sha1",
            "tests/fixtures/binary/source.c",
            "-o",
            str(DESTINATION / "elf-x86_64.so"),
        ]
    )
    objcopy, strip = tool("llvm-objcopy"), tool("llvm-strip")
    base = DESTINATION / "elf-x86_64"
    debug = DESTINATION / "elf-x86_64.debug"
    run([objcopy, "--only-keep-debug", str(base), str(debug)])
    stripped = DESTINATION / "elf-x86_64-stripped"
    run([strip, "--strip-all", "-o", str(stripped), str(base)])
    run([objcopy, f"--add-gnu-debuglink={debug}", str(stripped)])
    run(
        [
            objcopy,
            "--remove-section=.note.gnu.build-id",
            str(stripped),
            str(DESTINATION / "elf-x86_64-debuglink"),
        ]
    )
    sectionless = DESTINATION / "elf-x86_64-sectionless"
    payload = bytearray(stripped.read_bytes())
    struct.pack_into("<Q", payload, 40, 0)
    struct.pack_into("<HHH", payload, 58, 0, 0, 0)
    sectionless.write_bytes(payload)
    with tempfile.TemporaryDirectory(prefix="trailmark-fixtures-") as temp:
        build = Path(temp)
        run(
            [
                clang,
                "--target=x86_64-linux-gnu",
                f"--ld-path={linker}",
                *common,
                "-ffunction-sections",
                "-fno-pie",
                "-no-pie",
                "-nostdlib",
                "-Wl,-e,folded_entry",
                "-Wl,--icf=all",
                "-Wl,--build-id=sha1",
                "tests/fixtures/binary/folded.c",
                "-o",
                str(DESTINATION / "elf-folded"),
            ]
        )
        run(
            [
                clang,
                "--target=aarch64-linux-gnu",
                f"--ld-path={linker}",
                *(flag for flag in common if flag != "-fno-optimize-sibling-calls"),
                "-shared",
                "-fPIC",
                "-nostdlib",
                "-Wl,--build-id=sha1",
                "tests/fixtures/binary/linkage.c",
                "-o",
                str(DESTINATION / "elf-linkage.so"),
            ]
        )
        linkage_obj = build / "linkage.o"
        run(
            [
                clang,
                "--target=arm64-apple-macos11",
                "-c",
                *(flag for flag in common if flag != "-fno-optimize-sibling-calls"),
                "tests/fixtures/binary/linkage.c",
                "-o",
                str(linkage_obj),
            ]
        )
        run(
            [
                tool("ld64.lld", linker=True),
                "-arch",
                "arm64",
                "-platform_version",
                "macos",
                "11.0",
                "11.0",
                "-dylib",
                "-undefined",
                "dynamic_lookup",
                "-oso_prefix",
                f"{build}/",
                str(linkage_obj),
                "-o",
                str(DESTINATION / "macho-linkage.dylib"),
            ]
        )
        run(
            [
                tool("dsymutil"),
                str(DESTINATION / "macho-linkage.dylib"),
                "-o",
                str(build / "linkage.dSYM"),
            ],
            cwd=build,
        )
        shutil.copyfile(
            build / "linkage.dSYM/Contents/Resources/DWARF/macho-linkage.dylib",
            DESTINATION / "macho-linkage.dsym",
        )
        cold_obj = build / "cold.o"
        run(
            [
                clang,
                "--target=arm64-apple-macos11",
                "-c",
                "tests/fixtures/binary/cold-arm64.s",
                "-o",
                str(cold_obj),
            ]
        )
        run(
            [
                tool("ld64.lld", linker=True),
                "-arch",
                "arm64",
                "-platform_version",
                "macos",
                "11.0",
                "11.0",
                "-dylib",
                "-undefined",
                "dynamic_lookup",
                str(cold_obj),
                "-o",
                str(DESTINATION / "macho-cold.dylib"),
            ]
        )
        descriptor_obj = build / "descriptors.o"
        run(
            [
                clang,
                "--target=powerpc64-linux-gnu",
                "-c",
                "tests/fixtures/binary/descriptors.S",
                "-o",
                str(descriptor_obj),
            ]
        )
        # LLD rejects ELFv1 flags. This assembly uses only local code branches
        # and absolute descriptor relocations, which have identical linking
        # semantics in ELFv2; restore the descriptor ABI after linking.
        descriptor_data = bytearray(descriptor_obj.read_bytes())
        struct.pack_into(">I", descriptor_data, 48, 2)
        descriptor_obj.write_bytes(descriptor_data)
        run(
            [
                linker,
                "-m",
                "elf64ppc",
                "-e",
                "descriptor_entry",
                "--build-id=sha1",
                str(descriptor_obj),
                "-o",
                str(DESTINATION / "elf-ppc64-descriptors"),
            ]
        )
        descriptor_image = DESTINATION / "elf-ppc64-descriptors"
        descriptor_data = bytearray(descriptor_image.read_bytes())
        struct.pack_into(">I", descriptor_data, 48, 1)
        descriptor_image.write_bytes(descriptor_data)
        for architecture, triple in (
            ("arm64", "arm64-apple-macos11"),
            ("x86_64", "x86_64-apple-macos11"),
        ):
            obj = build / f"{architecture}.o"
            run(
                [
                    clang,
                    f"--target={triple}",
                    *common,
                    "-c",
                    "tests/fixtures/binary/source.c",
                    "-o",
                    str(obj),
                ]
            )
            image = DESTINATION / f"macho-{architecture}"
            run(
                [
                    clang,
                    f"--target={triple}",
                    f"--ld-path={tool('ld64.lld', linker=True)}",
                    "-nostdlib",
                    "-Wl,-e,_entry",
                    f"-Wl,-oso_prefix,{build}/",
                    str(obj),
                    "-o",
                    str(image),
                ]
            )
            # dsymutil resolves the OSO name relative to cwd after oso_prefix.
            dsym = build / f"{architecture}.dSYM"
            run([tool("dsymutil"), str(image), "-o", str(dsym)], cwd=build)
            shutil.copyfile(
                dsym / "Contents/Resources/DWARF" / image.name,
                DESTINATION / f"macho-{architecture}.dsym",
            )
            records.append(
                {
                    "file": image.name,
                    "format": "Mach-O",
                    "architecture": "aarch64" if architecture == "arm64" else architecture,
                    "mode": "",
                    "debug_file": f"macho-{architecture}.dsym",
                }
            )
        run(
            [
                tool("llvm-lipo"),
                "-create",
                str(DESTINATION / "macho-arm64"),
                str(DESTINATION / "macho-x86_64"),
                "-output",
                str(DESTINATION / "macho-universal"),
            ]
        )
        # Two slices of the same CPU family, with exact ALL and Haswell subtypes.
        thin = (DESTINATION / "macho-x86_64").read_bytes()
        haswell = bytearray(thin)
        struct.pack_into("<I", haswell, 8, 8)
        second = (4096 + len(thin) + 4095) & ~4095
        fat = bytearray(second + len(haswell))
        struct.pack_into(">II", fat, 0, 0xCAFEBABE, 2)
        subtype = struct.unpack_from("<I", thin, 8)[0]
        struct.pack_into(">IIIII", fat, 8, 0x1000007, subtype, 4096, len(thin), 12)
        struct.pack_into(">IIIII", fat, 28, 0x1000007, 8, second, len(haswell), 12)
        fat[4096 : 4096 + len(thin)] = thin
        fat[second:] = haswell
        (DESTINATION / "macho-same-cpu").write_bytes(fat)
        obj = build / "windows.obj"
        run(
            [
                clang,
                "--target=x86_64-pc-windows-msvc",
                *(flag for flag in common if flag != "-gdwarf-4"),
                "-gcodeview",
                "-gno-codeview-command-line",
                "-object-file-name=/trailmark-binary-fixture/windows.obj",
                "-c",
                "tests/fixtures/binary/source.c",
                "-o",
                str(obj),
            ]
        )
        image = DESTINATION / "pe-x86_64.exe"
        pdb = DESTINATION / "pe-x86_64.pdb"
        run(
            [
                tool("lld-link", linker=True),
                "/entry:entry",
                "/nodefaultlib",
                "/subsystem:console",
                "/debug",
                "/Brepro",
                "/export:entry",
                "/export:helper",
                "/export:dispatch",
                f"/out:{image.name}",
                f"/pdb:{pdb.name}",
                "/pdbaltpath:pe-x86_64.pdb",
                "/pdbsourcepath:/trailmark-binary-fixture",
                "/implib:unused.lib",
                obj.name,
            ],
            cwd=build,
        )
        shutil.copyfile(build / image.name, image)
        shutil.copyfile(build / pdb.name, pdb)
        records.append(
            {
                "file": image.name,
                "format": "PE",
                "architecture": "x86_64",
                "mode": "",
                "debug_file": pdb.name,
            }
        )
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DESTINATION.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    version = subprocess.run(  # noqa: S603 -- Locally configured compiler.
        [clang, "--version"], capture_output=True, check=True, text=True
    ).stdout.splitlines()[0]
    manifest = {
        "schema_version": 1,
        "compiler": version,
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "images": records,
        "sha256": files,
    }
    (DESTINATION / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built {len(records)} linked image profiles; hashed {len(files)} files")


if __name__ == "__main__":
    main()
