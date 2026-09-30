/*
 * ZK42V 4.2" 400x300 三色墨水屏驱动
 * =====================================================================
 *  这份代码里每一个字节都有出处，全部来自原厂固件逆向：
 *
 *    init / reset   原厂 0x01010802（屏型 'C' 专用） + 0x01011FC8
 *    cmd / data     原厂 0x010115D8 / 0x01011648
 *                    （CS 低 -> SCLK 低 -> 设 DC -> 8 位 MSB first -> CS 高）
 *    写图           原厂 0x0100FF04（两半，先 0x24 黑白面再 0x26 红面）
 *    刷新           原厂 0x0100FE20（0x22=0xC7 -> 0x20 -> 等 BUSY）
 *
 *  引脚表（flash 0x01057000 的引脚表 {02,FF,07,03,04,05,06,18}）：
 *    CS=P0_2   RST=P0_7   SCLK=P0_3   SDI=P0_4   DC=P0_5   BUSY=P0_6   AUX=P0_24
 * =====================================================================
 */
#include "epd_zk42v.h"
#include "zk_dbg.h"

#include "app_io.h"
#include "gr55xx.h"          /* DWT / CoreDebug / SystemCoreClock */

/* ------------------------------------------------------------------ 延时 */

static uint32_t s_cyc_per_us = 0;    /* 非 0 = DWT 可用 */
static uint32_t s_loop_per_us = 8;   /* DWT 不可用时的循环次数（粗糙但够用） */

/* 把 DWT 的周期计数器打开，并标定「1 微秒 = 多少周期」。
 *
 * ⚠ 这里**故意不清零** CYCCNT（build 25 改的）：它是个自由计数器，
 *   一旦被清零，所有"记录起始值、事后相减"的计时就全废了。
 *   之前 epd_gpio_init() 每次都会调到这儿，而 epd_refresh_ex() 内部也会调
 *   epd_gpio_init() —— 于是"写图 + 刷新"的耗时被算成两个无关计数器相减，
 *   报出过一个可笑的 5537ms（真实是 16 秒多，看 BUSY 轮询次数就知道）。
 *   自由计数器 32 位、16MHz 时 268 秒绕一圈，差值用无符号减法算就没事。 */
static void delay_init(void)
{
    uint32_t clk = SystemCoreClock;

    if (clk < 1000000u || clk > 128000000u)
    {
        clk = 64000000u;     /* 兜底：SDK 没填就按 64MHz 算 */
    }
    s_loop_per_us = clk / 4000000u;
    if (s_loop_per_us == 0)
    {
        s_loop_per_us = 1;
    }

    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    {
        volatile uint32_t i;
        uint32_t a = DWT->CYCCNT;
        for (i = 0; i < 200; i++)
        {
        }
        if (DWT->CYCCNT != a)
        {
            s_cyc_per_us = clk / 1000000u;
            g_dbg.flags |= ZK_FLAG_DWT_OK;
        }
    }
}

/* 单独把「延时/计时基准」初始化拿出来做成公开函数。
   为什么要它：delay_init() 原来只挂在 epd_gpio_init() 里，而 BLE 实验版
   故意不碰屏的引脚（怕影响射频），于是 DWT 从来没开过 —— 状态块里
   flags 的 bit2 一直没置位，主循环里 tick_ms() 恒为 0，没法用「真毫秒」
   做超时。build 19 起在 main() 里单独调一次，两条路都能拿到准确时间。 */
void epd_timer_init(void)
{
    delay_init();
}

void epd_delay_us(uint32_t us)
{
    if (us == 0)
    {
        return;
    }

    if (s_cyc_per_us)
    {
        uint32_t start = DWT->CYCCNT;
        uint32_t ticks = us * s_cyc_per_us;
        while ((uint32_t)(DWT->CYCCNT - start) < ticks)
        {
        }
    }
    else
    {
        volatile uint32_t n = us * (s_loop_per_us + 1);
        while (n--)
        {
        }
    }
}

void epd_delay_ms(uint32_t ms)
{
    while (ms--)
    {
        epd_delay_us(1000);
    }
}

/* ------------------------------------------------------------- 软件 SPI */
/* 一个位周期约 3us（≈330kHz）。屏控制器能跑几 MHz，我们故意慢，
   慢对墨水屏没有坏处，但接错线/时钟不对的时候更容易看出来。 */

static void spi_send_bit(uint8_t b)
{
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_SDI,
                     b ? APP_IO_PIN_SET : APP_IO_PIN_RESET);
    epd_delay_us(1);
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_SCLK, APP_IO_PIN_SET);
    epd_delay_us(1);
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_SCLK, APP_IO_PIN_RESET);
    epd_delay_us(1);
}

static void spi_send_byte(uint8_t v, int is_data)
{
    int i;

    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_CS, APP_IO_PIN_RESET);
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_SCLK, APP_IO_PIN_RESET);
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_DC,
                     is_data ? APP_IO_PIN_SET : APP_IO_PIN_RESET);

    for (i = 7; i >= 0; i--)
    {
        spi_send_bit((uint8_t)((v >> i) & 1u));
    }

    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_CS, APP_IO_PIN_SET);
}

static void epd_cmd(uint8_t c)
{
    spi_send_byte(c, 0);
}

static void epd_data(uint8_t d)
{
    spi_send_byte(d, 1);
}

/* 给 BLE 协议用的裸接口（网页的 SEND_CMD / SEND_DATA 那两个调试命令要直接
   往屏上发字节）。注意：调用前得先把脚配好（epd_gpio_init）。 */
void epd_cmd_raw(uint8_t c)
{
    epd_cmd(c);
}

void epd_data_raw(const uint8_t *d, uint32_t n)
{
    while (n--)
    {
        epd_data(*d++);
    }
}

/* ---------------------------------------------------------------- BUSY */

int epd_wait_busy(uint32_t timeout_ms)
{
    const uint32_t step_us = 200;
    uint32_t waited_us = 0;
    uint32_t limit_us = timeout_ms * 1000u;
    int saw_high = 0;

    /* 两种语义都兜：
     *   正常（SSD1680/UC81xx 这一族）：BUSY=1 表示在忙 -> 等它变 0
     *   万一这块屏是反的：一直读到 0、也没见过 1 -> 认为「本来就不忙」，直接过
     * 见没见过两种电平都记在调试块里，回头一看就知道是哪一种。 */
    while (waited_us < limit_us)
    {
        app_io_pin_state_t lv = app_io_read_pin(ZK42V_PORT, ZK42V_PIN_BUSY);
        g_dbg.busy_polls++;

        if (lv == APP_IO_PIN_SET)
        {
            saw_high = 1;
            g_dbg.busy_levels |= 0x2u;
        }
        else
        {
            g_dbg.busy_levels |= 0x1u;
            if (saw_high)
            {
                return 0;               /* 忙完了 */
            }
            if (waited_us >= 2000u)
            {
                return 0;               /* 从头到尾没忙过 */
            }
        }

        epd_delay_us(step_us);
        waited_us += step_us;
    }

    g_dbg.busy_timeouts++;
    g_dbg.flags |= ZK_FLAG_BUSY_TIMEOUT;
    return -1;
}

/* ------------------------------------------------------------ 引脚 / 复位 */

void epd_gpio_init(void)
{
    static const uint32_t out_pins[5] =
    {
        ZK42V_PIN_CS, ZK42V_PIN_RST, ZK42V_PIN_SCLK,
        ZK42V_PIN_SDI, ZK42V_PIN_DC,
    };
    app_io_init_t io;
    int i;

    delay_init();

    io.mode = APP_IO_MODE_OUTPUT;
    io.pull = APP_IO_NOPULL;
    io.mux  = APP_IO_MUX;

    for (i = 0; i < 5; i++)
    {
        io.pin = out_pins[i];
        if (app_io_init(ZK42V_PORT, &io) != APP_DRV_SUCCESS)
        {
            g_dbg.gpio_err++;
            g_dbg.flags |= ZK_FLAG_GPIO_FAIL;
        }
        /* 原厂 pins_init 把这几根都先拉高（CS/RST/SCLK/SDI/DC/AUX = 1） */
        app_io_write_pin(ZK42V_PORT, out_pins[i], APP_IO_PIN_SET);
    }

    /* 辅助脚（原厂编号 24 = P1_8）单独走 APP_IO_TYPE_NORMAL —— 见 board 头文件 */
    io.pin = ZK42V_PIN_AUX;
    if (app_io_init(ZK42V_PIN_AUX_TYPE, &io) != APP_DRV_SUCCESS)
    {
        g_dbg.gpio_err++;
        g_dbg.flags |= ZK_FLAG_GPIO_FAIL;
    }
    app_io_write_pin(ZK42V_PIN_AUX_TYPE, ZK42V_PIN_AUX, APP_IO_PIN_SET);

    io.pin  = ZK42V_PIN_BUSY;
    io.mode = APP_IO_MODE_INPUT;
    io.pull = APP_IO_PULLUP;
    if (app_io_init(ZK42V_PORT, &io) != APP_DRV_SUCCESS)
    {
        g_dbg.gpio_err++;
        g_dbg.flags |= ZK_FLAG_GPIO_FAIL;
    }

    /* 原厂 pins_init 也是把这根脚拉高后就开始复位屏；这里多等 50ms，
       让屏的供电/使能稳定下来（对墨水屏没有坏处）。 */
    epd_delay_ms(50);
}

void epd_reset(void)
{
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_RST, APP_IO_PIN_RESET);
    epd_delay_ms(1);
    app_io_write_pin(ZK42V_PORT, ZK42V_PIN_RST, APP_IO_PIN_SET);
    epd_delay_ms(1);

    epd_cmd(0x12);                    /* 软复位 */
    epd_wait_busy(15000);
}

void epd_init_sequence(void)
{
    epd_cmd(0x74); epd_data(0x54);
    epd_cmd(0x7E); epd_data(0x3B);
    epd_cmd(0x2B); epd_data(0x04); epd_data(0x63);
    epd_cmd(0x0C); epd_data(0x8E); epd_data(0x8C); epd_data(0x85); epd_data(0x3F);
    epd_cmd(0x01); epd_data(0x2B); epd_data(0x01); epd_data(0x00);   /* 300 行 */
    epd_cmd(0x11); epd_data(0x01);                                   /* 数据进入模式 */
    epd_cmd(0x44); epd_data(0x00); epd_data(0x31);                   /* X: 0..49 */
    epd_cmd(0x45); epd_data(0x2B); epd_data(0x01);
                   epd_data(0x00); epd_data(0x00);                   /* Y: 299..0 */
    epd_cmd(0x3C); epd_data(0x01);                                   /* 边框波形 */
    epd_cmd(0x18); epd_data(0x80);                                   /* 内部温度传感器 */
    epd_cmd(0x22); epd_data(0xB1);                                   /* 更新控制 2 */
    epd_cmd(0x20);                                                   /* 激活 */
    epd_wait_busy(15000);
}

/* ------------------------------------------------------------------ 画图 */

static void epd_set_cursor(void)
{
    uint32_t rows = ZK42V_EPD_HEIGHT;

    epd_cmd(0x4E); epd_data(0x00); epd_data(0x00);
    epd_cmd(0x4F); epd_data((uint8_t)((rows - 1) & 0xFF));
                   epd_data((uint8_t)(((rows - 1) >> 8) & 0xFF));
}

void epd_write_image(const uint8_t *buf)
{
    uint32_t i;
    int32_t  row;
    const uint32_t rows = ZK42V_EPD_HEIGHT;
    const uint32_t rb   = ZK42V_EPD_ROW_BYTES;

    /* ---- 为什么要按行倒着发 ------------------------------------------
     * 原厂的初始化把数据进入模式设成 0x11=0x01（X 递增、**Y 递减**），
     * RAM 的 Y 范围是 299..0，游标先 0x4E/0x4F 设到 (0, 299)。
     * 于是"第一个发出去的字节"落在 RAM 第 299 行，而实测**物理屏的最下面
     * 就是 RAM 第 299 行**（B1.6 那张方向图：黑条画在 buffer 第 0 行，
     * 显示在屏的最下面，红条画在第 0 列，显示在最左边）。
     * 也就是说：buffer 第 0 行本来该在最上面，却被送到了最下面。
     * 修法很简单 —— 发的时候把行序倒过来：
     *     buffer 第 299 行 先发 -> 落 RAM 299 = 物理最下面  ✓
     *     buffer 第 0 行   后发 -> 落 RAM 0   = 物理最上面  ✓
     * 列不用动（X 递增，实测左边就是左边）。
     * ------------------------------------------------------------------ */

    epd_gpio_init();          /* 原厂每次操作前都 pins_init（上次末尾 release 过） */

    epd_set_cursor();
    epd_cmd(0x24);                                   /* 黑白面 */
    for (row = (int32_t)rows - 1; row >= 0; row--)
    {
        for (i = 0; i < rb; i++)
        {
            epd_data(buf[(uint32_t)row * rb + i]);
        }
    }

    epd_set_cursor();
    epd_cmd(0x26);                                   /* 红面 */
    for (row = (int32_t)rows - 1; row >= 0; row--)
    {
        for (i = 0; i < rb; i++)
        {
            epd_data(buf[ZK42V_EPD_PLANE_BYTES + (uint32_t)row * rb + i]);
        }
    }
}

void epd_refresh_ex(uint8_t ctrl, int with_temp)
{
    epd_gpio_init();

    if (with_temp)
    {
        /* 原厂 0x0100FE4E 那条路：先使能内部温度传感器、写一个温度值，再更新 */
        epd_cmd(0x18); epd_data(0x80);
        epd_cmd(0x1A); epd_data(0x55);
    }

    /* 只负责"发激活 + 等忙"。**真假判据和重试在 epd_flush_frame() 里** ——
     * build 47 的教训：只重发激活没用（3 次全被屏忽略，BUSY 合计才 1.4 秒），
     * 得整轮重来（复位+初始化+写图+激活）。 */
    epd_cmd(0x22); epd_data(ctrl);
    epd_cmd(0x20);
    epd_wait_busy(30000);
}

/* ------------------------------------------------------------------
 * 一整套「写图 + 刷新」，带**整轮重试**（build 48，2026-09-30）
 *
 * 实机证据（outputs/pyocd/status-20260930-*.log）：
 *   11:04:04  7073 ms  BUSY  1632 次(≈0.3s)  ← 假刷，屏没变
 *   12:38:33  7187 ms  BUSY  2397 次(≈0.5s)  ← 假刷，屏没变
 *   11:05:44 24360 ms  BUSY 84907 次(≈17s)   ← 真刷
 *   13:30:18  8752 ms  BUSY  7093 次(≈1.4s)  ← build 47 的"重发激活"3 次，全被忽略
 * 规律：**一次同步里第一轮永远是假刷，紧接着的第二轮才真刷**
 *       （用户看到的就是"发两条命令（时间+天气）才刷得动屏，只发一条永远不刷"）。
 *
 * 所以这里照抄那个能工作的场景：激活后按 BUSY 差值判真假（忙够 1 秒 = 5000 次轮询），
 * **没真刷就断电、缓 600ms、整轮重来**（重新上电 + 复位 + 初始化 + 写图 + 激活），最多 3 轮。
 * 返回：第几轮成功的（1..3）；0 = 三轮都没真刷（状态块会记成 panel_state=3）。
 * ------------------------------------------------------------------ */
int epd_flush_frame(const uint8_t *buf, uint8_t ctrl, int with_temp)
{
    int pass;

    for (pass = 1; pass <= EPD_FLUSH_TRIES; pass++)
    {
        uint32_t p0 = g_dbg.busy_polls;

        epd_gpio_init();                 /* 上电（含 P1_8/AUX） */
        epd_reset();                     /* 硬复位 + 软复位 0x12 */
        epd_init_sequence();             /* 面板参数 + 一次"打底"激活 */
        if (buf != 0)
        {
            epd_write_image(buf);        /* 两半：0x24 黑白面 + 0x26 红面 */
        }
        epd_refresh_ex(ctrl, with_temp); /* 0x22/0x20 + 等 BUSY；with_temp=1 时先写 0x18/0x1A */

        if ((g_dbg.busy_polls - p0) >= 5000u)   /* 5000 × 200us ≈ 1 秒 = 真刷 */
        {
            return pass;
        }

        /* 假刷：把电断掉，让下一轮变成和"第二条命令"一样的场景 */
        epd_pins_release();
        epd_delay_ms(600);
    }
    return 0;
}

void epd_pins_release(void)
{
    static const uint32_t outs[5] =
    {
        ZK42V_PIN_CS, ZK42V_PIN_RST, ZK42V_PIN_SCLK, ZK42V_PIN_SDI, ZK42V_PIN_DC,
    };
    int i;

    for (i = 0; i < 5; i++)
    {
        app_io_deinit(ZK42V_PORT, outs[i]);
    }
    app_io_deinit(ZK42V_PIN_AUX_TYPE, ZK42V_PIN_AUX);   /* 关键：把 P1_8 也松开 */
    app_io_deinit(ZK42V_PORT, ZK42V_PIN_BUSY);
}

void epd_refresh(void)
{
    epd_refresh_ex(0xC7, 0);
}

/* 原厂那条**快刷**路径（build 51）。
 * ------------------------------------------------------------------
 * 反汇编依据（work 里的 app.asm）：
 *   func 0x0100FE4E:  0x18→0x80 ; 0x1A→0x55 ; 0x22→0xD7 ; 0x20
 *   func 0x0100FE84:  调上面那条 → 读一个寄存器取低 3 位当档位 →
 *                     查表 0x0100D8A0（64/48/16/24/16/32/0/0）→ ×10000 tick
 *                     → **轮询内部计时器等够这段时间**（0.16~0.64 秒），
 *                     **完全不看 BUSY**（这点跟 0xC7 全刷的 17 秒 BUSY 完全不同）。
 *
 * 结论：原厂这条不是"带温度的全刷"，而是"按温度补偿的**短延时快刷**"。
 * 我们上次拿它做过实验，但因为塞进了 build 48 的"BUSY 不够 1 秒就整轮重来"，
 * 反被判成假刷、连做 3 轮，结果 172 秒 + 画面错。所以这里严格照原厂：
 * 不看 BUSY、不重试，只等一个保守的 640ms（表里最大的那一档）。 */
void epd_refresh_fast(uint8_t drv)
{
    epd_gpio_init();
    epd_cmd(0x18); epd_data(0x80);      /* 内部温度传感器使能 */
    epd_cmd(0x1A); epd_data(drv);       /* 写温度值/驱动强度（原厂固定 0x55；这里可调） */
    epd_cmd(0x22); epd_data(0xD7);      /* 更新控制 2：原厂第二条路 */
    epd_cmd(0x20);                      /* 激活 */
    epd_delay_ms(640);                  /* 原厂按档位等 0.16~0.64 秒，这里取上限 */
}

/* 局部窗口 + 快刷（build 52，实验用）。
 *
 * UC8176/SSD1680 家族的做法：
 *   0x90 <8 个坐标字节> <1 个扫描标志>   = 设局部窗口（x 以像素列计、y 以行计）
 *   0x91                                = partial in（进入局部模式）
 *   （然后在窗口内写图数据）
 *   0x18/0x1A + 0x22=0xD7 + 0x20        = 快刷激活（跟整屏快刷同一条）
 *   0x92                                = partial out（退出局部模式）
 *
 * 参数单位：x 用**像素列**（0~399）、y 用行（0~299）—— 调用方负责换算
 * （固件里 x 用"字节列"更省事，所以传进来前 ×8）。 */
void epd_refresh_fast_window(uint8_t drv, int x0, int y0, int x1, int y1)
{
    epd_gpio_init();

    epd_cmd(0x90);
    epd_data((uint8_t)(x0 & 0xFF));
    epd_data((uint8_t)((x0 >> 8) & 0xFF));
    epd_data((uint8_t)(x1 & 0xFF));
    epd_data((uint8_t)((x1 >> 8) & 0xFF));
    epd_data((uint8_t)(y0 & 0xFF));
    epd_data((uint8_t)((y0 >> 8) & 0xFF));
    epd_data((uint8_t)(y1 & 0xFF));
    epd_data((uint8_t)((y1 >> 8) & 0xFF));
    epd_data(0x01);                     /* 扫描方向标志（原厂/常见实现都写 1） */

    epd_cmd(0x91);                      /* partial in */

    epd_cmd(0x18); epd_data(0x80);
    epd_cmd(0x1A); epd_data(drv);
    epd_cmd(0x22); epd_data(0xD7);
    epd_cmd(0x20);
    epd_delay_ms(640);

    epd_cmd(0x92);                      /* partial out */
}

void epd_deep_sleep(void)
{
    epd_cmd(0x10);
    epd_data(0x01);
}
