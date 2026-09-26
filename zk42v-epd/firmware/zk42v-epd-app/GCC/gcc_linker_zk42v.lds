/* =====================================================================
 *  ZK42V 价签（GR5513BEND）自研固件链接脚本
 *
 *  跟 SDK 自带的 gcc_linker_gr5513.lds 只有三处不同：
 *    1) FLASH 起始 = 0x0100A000  ← 原厂 bootloader 找 APP 就看这个地址
 *    2) FLASH 长度收到 0x75000   ← 到 0x0107F000（NVDS）为止，写飞了会报错
 *    3) RAM 顶留给调试状态块 4KB ← 0x3001F000，栈从它下面往下长
 * ===================================================================== */
MEMORY
{
    /* 0x0100A000 .. 0x0107F000 = 480KB，够我们用（原厂 APP 才 126KB） */
    FLASH (rx)   : ORIGIN = 0x0100A000, LENGTH = 0x00075000

    /* RAM 布局（GR5513 一共 128KB，从 0x30000000 到 0x30020000）：
     *   0x30000000 ~ 0x30004000  ROM 运行时自留（SDK 要的，不能占）
     *   0x30004000 ~ 0x30014000  应用 RAM：.data / .bss / heap / 栈（栈顶 0x30014000）
     *   0x30014000 ~ 0x3001C000  **B2-B 共享内存信箱**（控制块 + 30000 字节图）
     *                            固定地址，见 board/zk_dbg.h 的 ZK_MB_ADDR；
     *                            不放进 .bss，免得被启动代码清零或跟别的撞上
     *   0x3001C000 ~ 0x3001F000  空着（留着以后用）
     *   0x3001F000 ~ 0x30020000  调试状态块 RAM_DBG（栈往上的保留区）
     */
    RAM (rwx)    : ORIGIN = 0x30000000 + 0x4000, LENGTH = 0x00010000

    /* 调试状态块：固定 0x3001F000，栈顶也是这里，栈往下长所以永远不会踩到 */
    RAM_DBG (rw) : ORIGIN = 0x3001F000, LENGTH = 0x00001000
}

GROUP(libgcc.a libc.a libm.a libnosys.a)
ENTRY(Reset_Handler)

SECTIONS
{
    .text :
    {
        KEEP(*(.vectors))
        __Vectors_End = .;
        __Vectors_Size = __Vectors_End - __Vectors;
        __end__ = .;
        . = ALIGN(0x200);
        KEEP(*(.app_info))
        *( .text*)
        KEEP(*(.init))
        KEEP(*(.fini))
        *crtbegin.o(.ctors)
        *crtbegin?.o(.ctors)
        *(EXCLUDE_FILE(*crtend?.o *crtend.o) .ctors)
        *(SORT(.ctors.*))
        *(.ctors)
        *crtbegin.o(.dtors)
        *crtbegin?.o(.dtors)
        *(EXCLUDE_FILE(*crtend?.o *crtend.o) .dtors)
        *(SORT(.dtors.*))
        *(.dtors)
        *(.rodata*)
        KEEP(*(.eh_frame*))
    } > FLASH

    .ARM.extab :
    {
        *(.ARM.extab* .gnu.linkonce.armextab.*)
    } > FLASH

    /* 固件标记字符串：验收脚本靠搜这个字符串确认「片上跑的确实是我们的固件」。
       必须 KEEP，否则 --gc-sections 会把没人引用的它收掉。 */
    .zk_tag :
    {
        KEEP(*(.zk_tag))
    } > FLASH

    __exidx_start = .;
    .ARM.exidx :
    {
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
    } > FLASH

    __exidx_end = .;
    __etext = .;

    .data : AT (__etext)
    {
        __data_start__ = .;
        *(vtable)
        *(.data*)
            *(.ramfunc)
            *(RAM_CODE)
            *(TINY_RAM_SPACE)
                . = ALIGN(4);
        PROVIDE_HIDDEN (__preinit_array_start = .);
        KEEP(*(.preinit_array))
        PROVIDE_HIDDEN (__preinit_array_end = .);
        . = ALIGN(4);
        PROVIDE_HIDDEN (__init_array_start = .);
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        PROVIDE_HIDDEN (__init_array_end = .);
        . = ALIGN(4);
        PROVIDE_HIDDEN (__fini_array_start = .);
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array))
        PROVIDE_HIDDEN (__fini_array_end = .);
        KEEP(*(.jcr*))
        . = ALIGN(4);
        __data_end__ = .;
    } > RAM

    .my_section :
    {
        . = ALIGN(32);
        *(FPB*)
    } > RAM

    .bss :
    {
        . = ALIGN(4);
        __bss_start__ = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end__ = .;
    } > RAM

    .heap (COPY):
    {
        __HeapBase = .;
        __end__ = .;
        end = __end__;
        KEEP(*(.heap*))
        __HeapLimit = .;
    } > RAM

    .stack_dummy (COPY):
    {
        KEEP(*(.stack*))
    } > RAM

    /* 调试状态块：不加载、不清零，固定落在 0x3001F000 */
    .dbg_status (NOLOAD) :
    {
        . = ALIGN(16);
        KEEP(*(.dbg_status))
        . = ALIGN(16);
    } > RAM_DBG

    __StackTop = ORIGIN(RAM) + LENGTH(RAM);
    __StackLimit = __StackTop - SIZEOF(.stack_dummy);

    __sstack = __StackLimit;
    __estack = __StackTop;

    PROVIDE(__stack = __StackTop);
    ASSERT(__StackLimit >= __HeapLimit, "region RAM overflowed with stack")
}
