/*
 * ZK42V 价签（GR5513BEND）自研固件 —— SDK 配置
 *
 * 跟 SDK 样例 projects/peripheral/gpio/app_gpio/Src/config/custom_config.h
 * 只有四处不同，都在下面的「跟样例不一样的地方」里标了。
 */
#ifndef __CUSTOM_CONFIG_H__
#define __CUSTOM_CONFIG_H__

// <h> Basic configuration

// 芯片族（GR5513 也走 GR5515 这套族宏，SDK 全系列都这么写）
#ifndef SOC_GR5515
#define SOC_GR5515
#endif

// <<< 跟样例不一样 ①>>> 芯片型号：6 = GR5513BEND（样例是 4 = GR5515RGBD）
// <0=> GR5515IGND  <1=> GR5515IENDU  <2=> GR5515I0ND  <3=> GR5515I0NDA
// <4=> GR5515RGBD  <5=> GR5515GGBD   <6=> GR5513BEND <7=> GR5513BENDU
#ifndef CHIP_TYPE
#define CHIP_TYPE  6
#endif

#ifndef ENCRYPT_ENABLE
#define ENCRYPT_ENABLE  0
#endif

#ifndef EXT_EXFLASH_ENABLE
#define EXT_EXFLASH_ENABLE       0
#endif

#ifndef PLATFORM_INIT_ENABLE
#define PLATFORM_INIT_ENABLE      1
#endif

#ifndef PLATFORM_REMOVE_AON_WDT_PROT_ENABLE
#define PLATFORM_REMOVE_AON_WDT_PROT_ENABLE      1
#endif

#ifndef SYS_FAULT_TRACE_ENABLE
#define SYS_FAULT_TRACE_ENABLE    1
#endif

#ifndef ENABLE_BACKTRACE_FEA
#define ENABLE_BACKTRACE_FEA      0
#endif

#ifndef APP_DRIVER_USE_ENABLE
#define APP_DRIVER_USE_ENABLE     1
#endif

// <<< 跟样例不一样 ②>>> 关掉 APP_LOG
// 样例走 UART0 打印，但这块价签的 UART 引脚我们还没挖出来，
// 贸然打开会去初始化一个不知道接在哪的 UART。看不到就算了 ——
// 我们的「输出」是屏本身，出问题靠 0x3001F000 的调试状态块来查。
#ifndef APP_LOG_ENABLE
#define APP_LOG_ENABLE            0
#endif

#ifndef APP_LOG_PORT
#define APP_LOG_PORT              0
#endif

#ifndef APP_LOG_STORE_ENABLE
#define APP_LOG_STORE_ENABLE      0
#endif

#if     (CHIP_TYPE <= 5)
#ifndef SK_GUI_ENABLE
#define SK_GUI_ENABLE             1
#endif
#endif

#ifndef DEBUG_MONITOR
#define DEBUG_MONITOR             0
#endif

#ifndef DTM_TEST_ENABLE
#define DTM_TEST_ENABLE           0
#endif

#ifndef FLASH_PROTECT_PRIORITY
#define FLASH_PROTECT_PRIORITY    0
#endif

// NVDS：CHIP_TYPE 6/7 会自动落到 0x0107F000，跟原厂布局一模一样
#ifndef NVDS_START_ADDR
#if (CHIP_TYPE == 1) || (CHIP_TYPE == 6) || (CHIP_TYPE == 7)
#define NVDS_START_ADDR         0x0107F000
#else
#define NVDS_START_ADDR         0x010FF000
#endif
#endif

#ifndef NVDS_NUM_SECTOR
#define NVDS_NUM_SECTOR          1
#endif

#ifndef SYSTEM_STACK_SIZE
#define SYSTEM_STACK_SIZE       0x4000
#endif

#ifndef SYSTEM_HEAP_SIZE
#define SYSTEM_HEAP_SIZE        0x1000
#endif

// </h>

// <h> Boot info configuration

#ifndef CHIP_VER
#define CHIP_VER                0x5515
#endif

// <<< 跟样例不一样 ③>>> 代码位置：原厂 bootloader 就是在 0x0100A000 找 APP 的
// （样例默认 0x01002000 是 SDK 自己那套 bootloader 的布局，这块价签不是）
#ifndef APP_CODE_LOAD_ADDR
#define APP_CODE_LOAD_ADDR      0x0100A000
#endif

#ifndef APP_CODE_RUN_ADDR
#define APP_CODE_RUN_ADDR       0x0100A000
#endif

#ifndef SYSTEM_CLOCK
#define SYSTEM_CLOCK            0
#endif

#ifndef CFG_LF_ACCURACY_PPM
#define CFG_LF_ACCURACY_PPM     500
#endif

#ifndef CFG_LPCLK_INTERNAL_EN
#define CFG_LPCLK_INTERNAL_EN   0
#endif

#ifndef CFG_CRYSTAL_DELAY
#define CFG_CRYSTAL_DELAY       100
#endif

#ifndef BOOT_LONG_TIME
#define BOOT_LONG_TIME          1
#endif

#ifndef BOOT_CHECK_IMAGE
#define BOOT_CHECK_IMAGE        1
#endif

#ifndef EXFLASH_WAKEUP_DELAY
#define EXFLASH_WAKEUP_DELAY              0
#endif

// </h>

// <h> BLE resource configuration（用不到，留着是为了跟 SDK 头文件对得上）
#ifndef CFG_MAX_PRFS
#define CFG_MAX_PRFS             10
#endif

#ifndef CFG_MAX_BOND_DEVS
#define CFG_MAX_BOND_DEVS        4
#endif

#ifndef CFG_SCAN_DUP_FILT_LIST_NUM
#define CFG_SCAN_DUP_FILT_LIST_NUM       0
#endif

#ifndef CFG_MAX_CONNECTIONS
#define CFG_MAX_CONNECTIONS      5
#endif

#ifndef CFG_MAX_ADVS
#define CFG_MAX_ADVS             1
#endif

#ifndef CFG_MAX_ADV_DATA_LEN_SUPPORT
#define CFG_MAX_ADV_DATA_LEN_SUPPORT        0
#endif

#ifndef CFG_MAX_PER_ADVS
#define CFG_MAX_PER_ADVS          0
#endif

#ifndef CFG_MAX_SYNCS
#define CFG_MAX_SYNCS             0
#endif

#ifndef CFG_MAX_SCAN
#define CFG_MAX_SCAN              1
#endif

#ifndef CFG_BT_BREDR
#define CFG_BT_BREDR                      0
#endif

#ifndef CFG_MUL_LINK_WITH_SAME_DEV
#define CFG_MUL_LINK_WITH_SAME_DEV        0
#endif

#ifndef CFG_CAR_KEY_SUPPORT
#define CFG_CAR_KEY_SUPPORT               0
#endif
// </h>

// <h>  MESH support configuration
#ifndef CFG_MESH_SUPPORT
#define CFG_MESH_SUPPORT          0
#endif
// </h>

// <h>  LCP support configuration
#ifndef CFG_LCP_SUPPORT
#define CFG_LCP_SUPPORT           0
#endif
// </h>

// <h>  Security configuration
#ifndef SECURITY_CFG_VAL
#define SECURITY_CFG_VAL         0
#endif
// </h>

#endif //__CUSTOM_CONFIG_H__
