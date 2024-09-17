import powermake


def compile_bootblock(config: powermake.Config):
    config = config.copy()

    config.set_optimization("-Oz")  # if no optimization is used, the boot block will be too big.
    config.add_ld_flags("-N", "-e", "start", "-Ttext", "0x7C00")
    config.add_c_flags("-nostdinc")
    config.add_as_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdinc")

    objects = powermake.compile_files(config, {"bootmain.c", "bootasm.S"})

    objects = sorted(list(objects))  # Apparently, bootasm **NEED** to be before bootmain for the link

    bootblock_o = powermake.link_files(config, objects, executable_name="bootblock.o")

    powermake.run_command(config, ["objcopy", "-S", "-O", "binary", "-j", ".text", bootblock_o, "bootblock"])

    powermake.run_command(config, ["./sign.pl", "bootblock"])


def compile_initcode(config: powermake.Config):
    config = config.copy()

    config.add_ld_flags("-N", "-e", "start", "-Ttext", "0")
    config.add_as_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdinc")

    objects = powermake.compile_files(config, {"initcode.S"})

    initcode_o = powermake.link_files(config, objects, executable_name="initcode.o.o")

    powermake.run_command(config, ["objcopy", "-S", "-O", "binary", initcode_o, "initcode"])


def compile_entryother(config: powermake.Config):
    config = config.copy()

    config.add_ld_flags("-N", "-e", "start", "-Ttext", "0x7000")
    config.add_as_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdinc")

    objects = powermake.compile_files(config, {"entryother.S"})

    entryother_o = powermake.link_files(config, objects, executable_name="entryother.o.o")

    powermake.run_command(config, ["objcopy", "-S", "-O", "binary", "-j", ".text", entryother_o, "entryother"])


def build_xv6_img(config: powermake.Config):
    config = config.copy()

    compile_bootblock(config)
    compile_initcode(config)
    compile_entryother(config)

    config.add_as_flags("-gdwarf-2", "-Wa,-divide")

    if powermake.needs_update("vector.S", {"vectors.pl"}, []):
        powermake.run_command(config, "./vectors.pl > vectors.S", shell=True)

    files = {
        "bio.c", "console.c", "entry.S", "exec.c",
        "file.c", "fs.c", "ide.c", "ioapic.c",
        "kalloc.c", "kbd.c", "lapic.c", "log.c",
        "main.c", "mp.c", "picirq.c", "pipe.c",
        "proc.c", "sleeplock.c", "spinlock.c", "string.c",
        "swtch.S", "syscall.c", "sysfile.c", "sysproc.c",
        "trapasm.S", "trap.c", "uart.c", "vectors.S",
        "vm.c"
    }

    objects = powermake.compile_files(config, files)

    config.add_ld_flags("-T", "kernel.ld", "-b", "binary", "initcode", "entryother")
    kernel_bin = powermake.link_files(config, objects)

    powermake.run_command(config, "dd if=/dev/zero of=xv6.img count=10000", shell=True)
    powermake.run_command(config, "dd if=bootblock of=xv6.img conv=notrunc", shell=True)
    powermake.run_command(config, ["dd", f"if={kernel_bin}", "of=xv6.img", "seek=1", "conv=notrunc"])


def compile_user_prg(config: powermake.Config, files: set[str], deps_objects: set[str], program_name: str):
    objects = deps_objects.union(powermake.compile_files(config, files))
    return powermake.link_files(config, objects, executable_name=program_name)


def build_fs_img(config: powermake.Config, mkfs_prg: str):
    config = config.copy()
    config.add_ld_flags("-N", "-e", "main", "-Ttext", "0")
    config.add_as_flags("-gdwarf-2", "-Wa,-divide")

    files_libc_restricted = {
        "ulib.c",
        "usys.S",
    }
    files_libc_extented = {
        "printf.c",
        "umalloc.c"
    }

    objects_libc_restricted = powermake.compile_files(config, files_libc_restricted)
    objects_libc = objects_libc_restricted.union(powermake.compile_files(config, files_libc_extented))

    programs = set()

    config.exe_build_directory = "."

    for name in ("cat", "echo", "grep", "init", "kill", "ln", "ls", "mkdir", "rm", "sh", "stressfs", "usertests", "wc", "zombie"):
        programs.add(compile_user_prg(config, {f"{name}.c"}, objects_libc, f"_{name}"))

    # forktest has less library code linked in
    # needs to be small in order to be able to max out the proc table.
    programs.add(compile_user_prg(config, {"forktest.c"}, objects_libc_restricted, "_forktest"))

    op = powermake.operation.Operation("fs.img", {"README", *programs}, config, command=[mkfs_prg, "fs.img", *programs])
    op.execute(force=config.rebuild)


def build_mkfs(config: powermake.Config):
    config = config.copy()

    config.c_compiler = powermake.compilers.CompilerGCC()
    config.linker = powermake.linkers.LinkerGCC()

    mkfs_objects = powermake.compile_files(config, {"mkfs.c"})
    return powermake.link_files(config, mkfs_objects, executable_name="mkfs")


def on_build(config: powermake.Config):
    mkfs_prg = build_mkfs(config)

    config.add_c_cpp_as_asm_flags("-Wall", "-Wextra")

    config.add_c_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie")
    config.add_ld_flags("-m", "elf_i386")

    build_xv6_img(config)
    build_fs_img(config, mkfs_prg)

    powermake.run_command(config, "qemu-system-i386 -serial mon:stdio -drive file=fs.img,index=1,media=disk,format=raw -drive file=xv6.img,index=0,media=disk,format=raw -smp 2 -m 512", shell=True)


def on_clean(config: powermake.Config):
    powermake.delete_files_from_disk(
        "_cat", "_echo", "_forktest", "_grep", "_init", "_kill", "_ln", "_ls",
        "_mkdir", "_rm", "_sh", "_stressfs", "_usertests", "_wc", "_zombie",
        "bootblock", "entryother", "initcode", "fs.img", "xv6.img",
        "vectors.S"
    )

    powermake.default_on_clean(config)


powermake.run("kernel.bin", build_callback=on_build, clean_callback=on_clean)
