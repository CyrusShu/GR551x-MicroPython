#############################################################################
#  ZK42V 价签（GR5513BEND）自研固件 —— GCC Makefile
#
#  跟 SDK 自带的 projects/peripheral/gpio/app_gpio/GCC/Makefile 是一套参数，
#  只改了三件事：
#    · 目标名 zk42v_epd
#    · 链接脚本用本目录的 gcc_linker_zk42v.lds（APP 落在 0x0100A000）
#    · 源码列表换成我们自己的 main.c / 屏驱动 / 测试图
#
#  用法：
#      make            # 出 out/zk42v_epd.bin（这就是要烧进 0x0100A000 的镜像）
#      make clean
#      make SDK_ROOT=/别的/路径/GR551x-SDK
#############################################################################

SDK_ROOT ?= /Users/mac/Documents/Codex/2026-09-15/a/GR551x-SDK

MAKE_TARGET_NAME := zk42v_epd

# ---------------------------------------------------------------- 源码
PRJ_C_SRC_FILES := \
$(SDK_ROOT)/platform/soc/common/gr_interrupt.c \
$(SDK_ROOT)/components/sdk/ble.c \
$(SDK_ROOT)/platform/soc/common/gr_system.c \
$(SDK_ROOT)/platform/soc/common/gr_platform.c \
$(SDK_ROOT)/platform/soc/src/gr_soc.c \
$(SDK_ROOT)/platform/boards/board_SK.c \
../Src/board/zk_bat.c \
$(SDK_ROOT)/components/libraries/app_key/app_key_core.c \
$(SDK_ROOT)/components/libraries/app_key/app_key.c \
$(SDK_ROOT)/components/libraries/app_timer/app_timer.c \
$(SDK_ROOT)/drivers/src/app_dma.c \
$(SDK_ROOT)/drivers/src/app_pwr_mgmt.c \
$(SDK_ROOT)/drivers/src/app_gpiote.c \
$(SDK_ROOT)/drivers/src/app_io.c \
$(SDK_ROOT)/drivers/src/app_uart.c \
$(SDK_ROOT)/drivers/src/app_uart_dma.c \
$(SDK_ROOT)/components/libraries/utility/utility.c \
$(SDK_ROOT)/components/libraries/ring_buffer/ring_buffer.c \
$(SDK_ROOT)/components/libraries/app_log/app_log.c \
$(SDK_ROOT)/components/libraries/app_assert/app_assert.c \
$(SDK_ROOT)/components/libraries/pmu_calibration/pmu_calibration.c \
$(SDK_ROOT)/external/segger_rtt/SEGGER_RTT.c \
../Src/main.c \
../Src/epd/epd_zk42v.c \
../Src/img/testimg.c \
../Src/img/zkgui.c \
../Src/img/lunar.c \
../Src/img/zk_opt.c \
../Src/ble/zk_ble.c \
../Src/ble/zk_epd_svc.c \

PRJ_C_INCLUDE_PATH := \
../Src \
../Src/board \
../Src/config \
../Src/epd \
../Src/img \
../Src/ble \
$(SDK_ROOT)/components/boards \
$(SDK_ROOT)/components/drivers_ext/gr55xx \
$(SDK_ROOT)/components/drivers_ext/st7735 \
$(SDK_ROOT)/components/drivers_ext/vs1005 \
$(SDK_ROOT)/components/libraries/app_alarm \
$(SDK_ROOT)/components/libraries/app_assert \
$(SDK_ROOT)/components/libraries/app_error \
$(SDK_ROOT)/components/libraries/app_key \
$(SDK_ROOT)/components/libraries/app_log \
$(SDK_ROOT)/components/libraries/app_queue \
$(SDK_ROOT)/components/libraries/app_timer \
$(SDK_ROOT)/components/libraries/at_cmd \
$(SDK_ROOT)/components/libraries/dfu_master \
$(SDK_ROOT)/components/libraries/dfu_port \
$(SDK_ROOT)/components/libraries/gui \
$(SDK_ROOT)/components/libraries/gui/gui_config \
$(SDK_ROOT)/components/libraries/hal_flash \
$(SDK_ROOT)/components/libraries/hci_uart \
$(SDK_ROOT)/components/libraries/pmu_calibration \
$(SDK_ROOT)/components/libraries/ring_buffer \
$(SDK_ROOT)/components/libraries/sensorsim \
$(SDK_ROOT)/components/libraries/bsp \
$(SDK_ROOT)/components/libraries/fault_trace \
$(SDK_ROOT)/components/libraries/utility \
$(SDK_ROOT)/components/patch/ind \
$(SDK_ROOT)/components/sdk/ \
$(SDK_ROOT)/drivers/inc \
$(SDK_ROOT)/drivers/inc/hal \
$(SDK_ROOT)/external/freertos/include \
$(SDK_ROOT)/external/segger_rtt \
$(SDK_ROOT)/platform/boards \
$(SDK_ROOT)/platform/include \
$(SDK_ROOT)/platform/soc/include \
$(SDK_ROOT)/platform/arch/arm/cortex-m/cmsis/core/include \
$(SDK_ROOT)/components/app_drivers/inc \

PRJ_C_MICRO_DEFINES := \

# ---------------------------------------------------------------- 工具
CROSS_COMPILE ?= arm-none-eabi-
CC      = $(CROSS_COMPILE)gcc
ASM     = $(CROSS_COMPILE)gcc
LINK    = $(CROSS_COMPILE)gcc
OBJCOPY = $(CROSS_COMPILE)objcopy
SIZE    = $(CROSS_COMPILE)size

COMMON_COMPILE_FLAGS += -std=gnu99 --inline
COMMON_COMPILE_FLAGS += -ggdb3
# -Os：**这条是必须的，不是优化癖好**。
# 原厂 bootloader 对 APP 镜像有硬上限：0x01003934 那句
#     cmp r5, #0x26000        ; 155648 字节
# 超了就打印 "Fw load data err" 然后把 APP 判成无效、进 DFU 分支
# （实测症状：芯片停在 bootloader 的断言里，我们的状态块一个字节都不会被写）。
# 反推：bin_size + 0x1040 向上取整到扇区后 ≤ 0x26000，也就是
#     bin_size ≤ 0x24FC0 = 151488 字节（约 148 KB）
# build 26 那版没开优化（默认 -O0）、又加了日历/时钟的字模，涨到 156164
# 就超限了 —— 板子因此起不来。开 -Os 之后体积回到 100KB 出头。
COMMON_COMPILE_FLAGS += -ffunction-sections -fdata-sections
COMMON_COMPILE_FLAGS += -Os
COMMON_COMPILE_FLAGS += -mfloat-abi=softfp -mfpu=fpv4-sp-d16 -mapcs-frame -mthumb-interwork -mthumb -mcpu=cortex-m4
COMMON_COMPILE_FLAGS += -gdwarf-2 -MD

CFLAGS += $(foreach inc,$(PRJ_C_INCLUDE_PATH),-I $(inc))
CFLAGS += $(foreach md,$(PRJ_C_MICRO_DEFINES),-D$(md))
CFLAGS += $(COMMON_COMPILE_FLAGS)

ASMFLAGS += $(foreach inc,$(PRJ_C_INCLUDE_PATH),-I $(inc))
ASMFLAGS += $(COMMON_COMPILE_FLAGS)

LINK_SCRIPT = ./gcc_linker_zk42v.lds
PATCH_FILE  = $(SDK_ROOT)/platform/soc/linker/gcc/rom_symbol_gcc.txt
GCC_STARTUP_ASM_FILE = $(SDK_ROOT)/platform/arch/arm/cortex-m/gcc/startup_gr55xx.s

LDFLAGS += -Wl,--gc-sections
# newlib-nano：默认那份 libc 的 printf/snprintf 机制（dtoa/mprec/svfprintf…）
# 会白白吃掉 8KB 左右 —— 我们自己一个字节都不打印，但 SDK 的 gr_interrupt.c
# （fault 时 printf）和 app_log.c（用 snprintf）把它们拖了进来。
# 换 nano 之后同一份代码少 6KB 多，镜像体积从"顶到 bootloader 上限"变成有余量。
LDFLAGS += -specs=nano.specs
LDFLAGS += -L$(SDK_ROOT)/components/libraries/app_crypto/gcc/ -lapp_crypto
LDFLAGS += -L$(SDK_ROOT)/platform/soc/linker/gcc/ -lble_sdk

BUILD     ?= out
BUILD_OBJ ?= $(BUILD)/obj
BUILD_LST ?= $(BUILD)/lst

SRC_C   := $(PRJ_C_SRC_FILES)
SRC_ASM := $(GCC_STARTUP_ASM_FILE)
OBJ_C   := $(SRC_C:.c=.o)
OBJ_ASM := $(SRC_ASM:.s=.o)
OBJ     := $(OBJ_C) $(OBJ_ASM)
OBJ_ADJUST = $(patsubst %.o,$(BUILD_OBJ)/%.o,$(notdir $(OBJ)))

V ?= 0
ifeq ($(V),0)
    V_CC = @echo "  CC  " $(notdir $<);
    V_ASM = @echo "  AS  " $(notdir $<);
    V_LINK = @echo "  LD  " $(notdir $@);
    V_OBJCOPY = @echo "  BIN " $@;
else
    V_OPT = '-v'
endif

SRC_PATH += $(dir $(SRC_C))
SRC_PATH += $(dir $(SRC_ASM))
MAKE_PATH = $(foreach n,$(SRC_PATH),:$(n))
vpath %.c ./$(MAKE_PATH)
vpath %.s ./$(MAKE_PATH)

all: mk_path $(BUILD)/$(MAKE_TARGET_NAME).bin

$(BUILD_OBJ)/%.o : %.c
	$(V_CC) $(CC) $(CFLAGS) -c $< -o $@

$(BUILD_OBJ)/%.o : %.s
	$(V_ASM) $(ASM) $(ASMFLAGS) -c $< -o $@

$(BUILD)/$(MAKE_TARGET_NAME).bin: $(BUILD_LST)/$(MAKE_TARGET_NAME).elf
	$(V_OBJCOPY) $(OBJCOPY) -O binary $< $@

$(BUILD_LST)/$(MAKE_TARGET_NAME).elf: $(OBJ_ADJUST)
	$(V_LINK) $(LINK) $(CFLAGS) -T $(LINK_SCRIPT) $(PATCH_FILE) $(OBJ_ADJUST) $(LDFLAGS) -Wl,-Map=$(BUILD_LST)/$(MAKE_TARGET_NAME).map -o $@
	@$(SIZE) $@
	@$(OBJCOPY) -O ihex $@ $(BUILD)/$(MAKE_TARGET_NAME).hex
	@echo "  ===> $(BUILD)/$(MAKE_TARGET_NAME).bin"

mk_path :
	@mkdir -p $(BUILD) $(BUILD_OBJ) $(BUILD_LST)

# 把 gcc -MD 生成的依赖文件读进来 —— 否则改一个头文件（比如 custom_config.h）
# 不会触发任何重编，二进制看起来"改了没反应"。（2026-09-27 踩到过：
# CFG_LPCLK_INTERNAL_EN 从 0 改成 1，重编出来 SHA 一模一样。）
-include $(wildcard $(BUILD_OBJ)/*.d)

clean:
	rm -rf $(BUILD)

.PHONY: all mk_path clean
