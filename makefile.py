import os
import powermake
from concurrent.futures import ThreadPoolExecutor


userspace_programs = {
    "cat", "echo", "grep", "init", "kill", "ln", "ls", "mkdir",
    "rm", "sh", "stressfs", "usertests", "wc", "zombie"
}


def compile_bootblock(config: powermake.Config):
    config = config.copy()

    config.set_optimization("-Oz")  # if no optimization is used, the boot block will be too big.
    config.add_ld_flags("-N", "-estart", "-Ttext=0x7C00")
    config.add_c_flags("-nostdinc")
    config.add_as_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdinc")

    # WARNING, bootasm **NEED** to be before bootmain for the link
    # We use a list and not a set and powermake (version >= 1.10) will also return a list, preserving the order.
    objects = powermake.compile_files(config, ["bootasm.S", "bootmain.c"])

    bootblock_o = powermake.link_files(config, objects, executable_name="bootblock.o")

    if powermake.needs_update(outputfile="bootblock", dependencies={bootblock_o}, additional_includedirs=[]):
        powermake.run_command(config, ["objcopy", "-S", "-O", "binary", "-j", ".text", bootblock_o, "bootblock"])
        powermake.run_command(config, ["./sign.pl", "bootblock"])


def compile_initcode(config: powermake.Config):
    config = config.copy()

    config.add_ld_flags("-N", "-estart", "-Ttext=0")
    config.add_as_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdinc")

    objects = powermake.compile_files(config, {"initcode.S"})

    initcode_o = powermake.link_files(config, objects, executable_name="initcode.o.o")

    powermake.run_command_if_needed(config=config, outputfile="initcode", dependencies={initcode_o}, command=["objcopy", "-S", "-O", "binary", initcode_o, "initcode"])


def compile_entryother(config: powermake.Config):
    config = config.copy()

    config.add_ld_flags("-N", "-estart", "-Ttext=0x7000")
    config.add_as_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie", "-nostdinc")

    objects = powermake.compile_files(config, {"entryother.S"})

    entryother_o = powermake.link_files(config, objects, executable_name="entryother.o.o")

    powermake.run_command_if_needed(config, outputfile="entryother", dependencies={entryother_o}, command=["objcopy", "-S", "-O", "binary", "-j", ".text", entryother_o, "entryother"])


def build_xv6_img(config: powermake.Config):
    config = config.copy()

    compile_bootblock(config)
    compile_initcode(config)
    compile_entryother(config)

    config.add_as_flags("-gdwarf-2", "-Wa,-divide")

    powermake.run_command_if_needed(config=config, outputfile="vectors.S", dependencies={"vectors.pl"}, command="./vectors.pl > vectors.S", shell=True)

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

    config.add_ld_flags("-Tkernel.ld", "-bbinary", "initcode", "entryother")
    kernel_bin = powermake.link_files(config, objects)

    if powermake.needs_update(outputfile="xv6.img", dependencies={"bootblock", kernel_bin}, additional_includedirs=[]):
        powermake.run_command(config, "dd if=/dev/zero of=xv6.img count=10000", shell=True)
        powermake.run_command(config, "dd if=bootblock of=xv6.img conv=notrunc", shell=True)
        powermake.run_command(config, ["dd", f"if={kernel_bin}", "of=xv6.img", "seek=1", "conv=notrunc"])


def compile_user_prg(config: powermake.Config, files: set[str], deps_objects: set[str], program_name: str):
    objects = deps_objects.union(powermake.compile_files(config, files))
    return os.path.basename(powermake.link_files(config, objects, executable_name=program_name))


def build_mkfs(config: powermake.Config):
    config = config.empty_copy()

    config.add_c_flags("-Wall", "-Wextra")

    mkfs_objects = powermake.compile_files(config, {"mkfs.c"})
    return powermake.link_files(config, mkfs_objects, executable_name="mkfs")


def build_fs_img(config: powermake.Config):
    config = config.copy()
    config.add_ld_flags("-N", "-emain", "-Ttext=0")
    config.add_as_flags("-gdwarf-2", "-Wa,-divide")

    files_libc_restricted = {
        "ulib.c", "usys.S"
    }
    files_libc_extented = {
        "printf.c", "umalloc.c"
    }

    objects_libc_restricted = powermake.compile_files(config, files_libc_restricted)
    objects_libc = objects_libc_restricted.union(powermake.compile_files(config, files_libc_extented))


    config.exe_build_directory = ""  # mkfs need programs without any /

    with ThreadPoolExecutor(max_workers=config.nb_jobs) as executor:
        programs = executor.map(lambda prg_name: compile_user_prg(config, {f"{prg_name}.c"}, objects_libc, f"_{prg_name}"), userspace_programs)
    programs = set(programs)

    # forktest has less library code linked in
    # needs to be small in order to be able to max out the proc table.
    programs.add(compile_user_prg(config, {"forktest.c"}, objects_libc_restricted, "_forktest"))

    powermake.run_command_if_needed(config=config, outputfile="fs.img", dependencies={"README", *programs}, command=[build_mkfs(config), "fs.img", "README", *programs])


def on_build(config: powermake.Config):
    config.add_c_cpp_as_asm_flags("-Wall", "-Wextra")

    config.add_c_flags("-fno-pic", "-static", "-fno-builtin", "-fno-strict-aliasing", "-fno-omit-frame-pointer", "-fno-stack-protector", "-fno-pie", "-no-pie")
    config.add_ld_flags("-melf_i386")

    build_xv6_img(config)
    build_fs_img(config)


def on_clean(config: powermake.Config):
    powermake.delete_files_from_disk("build", "_*", "bootblock", "entryother", "initcode", "*.img", "vectors.S")


def on_test(config: powermake.Config, args):
    if args_parsed.gdb:
        powermake.run_command(config, "qemu-system-i386 -serial mon:stdio -drive file=fs.img,index=1,media=disk,format=raw -drive file=xv6.img,index=0,media=disk,format=raw -smp 2 -m 512 -S -gdb tcp::26000", shell=True)
    else:
        powermake.run_command(config, "qemu-system-i386 -serial mon:stdio -drive file=fs.img,index=1,media=disk,format=raw -drive file=xv6.img,index=0,media=disk,format=raw -smp 2 -m 512", shell=True)


parser = powermake.ArgumentParser()
parser.add_argument("--gdb", help="Run the kernel with gdb", action="store_true")

args_parsed = parser.parse_args()

powermake.run("kernel.bin", build_callback=on_build, clean_callback=on_clean, test_callback=on_test, args_parsed=args_parsed)
