/******************************************************************************
* Copyright (C) 2009 - 2020 Xilinx, Inc.  All rights reserved.
* SPDX-License-Identifier: MIT
******************************************************************************/
/*
 * main.c: 타이머 인터럽트마다 LED를 토글하는 예제
 */
#include <stdio.h>
#include "platform.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xscugic.h"
#include "xtmrctr.h"
#include "xil_exception.h"
#include "led.h"

#define GPIO_DEVICE_ID   XPAR_AXI_GPIO_0_DEVICE_ID
#define TMR_DEVICE_ID    XPAR_AXI_TIMER_0_DEVICE_ID
#define INTC_DEVICE_ID   XPAR_PS7_SCUGIC_0_DEVICE_ID
#define TMR_INTR_ID      XPAR_FABRIC_AXI_TIMER_0_INTERRUPT_INTR
#define RESET_VALUE      0xF8000000

XGpio Gpio;
XScuGic Intc;
static XTmrCtr Timer;
volatile int tick_count = 0, done = 0;

static int setup_interrupt(XScuGic *intc, XTmrCtr *tmr);

/*
 * 타이머 인터럽트 핸들러.
 * 인터럽트마다 LED를 토글하고 카운트를 증가시킨다.
 */
void timer_isr(void *CallBackRef, u8 TmrCtrNumber)
{
    led_toggle(&Gpio);
    tick_count++;
    if (tick_count >= 10) {
        done = 1;
    }
}

static int setup_timer(XTmrCtr *tmr)
{
    if (XTmrCtr_Initialize(tmr, TMR_DEVICE_ID) != XST_SUCCESS)
        return XST_FAILURE;
    XTmrCtr_SetHandler(tmr, timer_isr, tmr);
    XTmrCtr_SetOptions(tmr, 0, XTC_INT_MODE_OPTION | XTC_AUTO_RELOAD_OPTION);
    XTmrCtr_SetResetValue(tmr, 0, RESET_VALUE);
    return XST_SUCCESS;
}

/* GIC 초기화 및 타이머 인터럽트 연결 */
static int setup_interrupt(XScuGic *intc, XTmrCtr *tmr)
{
    XScuGic_Config *cfg = XScuGic_LookupConfig(INTC_DEVICE_ID);
    XScuGic_CfgInitialize(intc, cfg, cfg->CpuBaseAddress);
    Xil_ExceptionInit();
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_INT,
            (Xil_ExceptionHandler)XScuGic_InterruptHandler, intc);
    XScuGic_Connect(intc, TMR_INTR_ID,
            (Xil_ExceptionHandler)XTmrCtr_InterruptHandler, tmr);
    XScuGic_Enable(intc, TMR_INTR_ID);
    Xil_ExceptionEnable();
    return XST_SUCCESS;
}

int main()
{
    init_platform();
    xil_printf("LED timer demo // start\n\r");

    if (led_init(&Gpio, GPIO_DEVICE_ID) != XST_SUCCESS) {
        xil_printf("GPIO init failed\n\r");
        return -1;
    }
    setup_timer(&Timer);
    setup_interrupt(&Intc, &Timer);
    XTmrCtr_Start(&Timer, 0);

    while (!done);

    while (1) {
        xil_printf("tick=%d\n\r", tick_count);
        for (int i = 0; i < 1000000; i++) { }
    }

    cleanup_platform();
    return 0;
}
