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
    DWT->CYCCNT = 0;
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
    static const uint32_t out_pins[6] =
    {
        ZK42V_PIN_CS, ZK42V_PIN_RST, ZK42V_PIN_SCLK,
        ZK42V_PIN_SDI, ZK42V_PIN_DC, ZK42V_PIN_AUX,
    };
    app_io_init_t io;
    int i;

    delay_init();

    io.mode = APP_IO_MODE_OUTPUT;
    io.pull = APP_IO_NOPULL;
    io.mux  = APP_IO_MUX;

    for (i = 0; i < 6; i++)
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

    io.pin  = ZK42V_PIN_BUSY;
    io.mode = APP_IO_MODE_INPUT;
    io.pull = APP_IO_PULLUP;
    if (app_io_init(ZK42V_PORT, &io) != APP_DRV_SUCCESS)
    {
        g_dbg.gpio_err++;
        g_dbg.flags |= ZK_FLAG_GPIO_FAIL;
    }

    epd_delay_ms(10);
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

    epd_set_cursor();
    epd_cmd(0x24);                                   /* 黑白面 */
    for (i = 0; i < ZK42V_EPD_PLANE_BYTES; i++)
    {
        epd_data(buf[i]);
    }

    epd_set_cursor();
    epd_cmd(0x26);                                   /* 红面 */
    for (i = ZK42V_EPD_PLANE_BYTES; i < ZK42V_EPD_IMG_BYTES; i++)
    {
        epd_data(buf[i]);
    }
}

void epd_refresh_ex(uint8_t ctrl, int with_temp)
{
    if (with_temp)
    {
        /* 原厂 0x0100FE4E 那条路：先使能内部温度传感器、写一个温度值，再更新 */
        epd_cmd(0x18); epd_data(0x80);
        epd_cmd(0x1A); epd_data(0x55);
    }
    epd_cmd(0x22); epd_data(ctrl);
    epd_cmd(0x20);
    epd_wait_busy(30000);
}

void epd_refresh(void)
{
    epd_refresh_ex(0xC7, 0);
}

void epd_deep_sleep(void)
{
    epd_cmd(0x10);
    epd_data(0x01);
}
