/******************************************************************************
* Copyright (C) 2010 - 2020 Xilinx, Inc.  All rights reserved.
* SPDX-License-Identifier: MIT
******************************************************************************/
#include "xparameters.h"
#include "xil_cache.h"
#include "platform_config.h"

void
enable_caches()
{
#ifdef __PPC__
    Xil_ICacheEnableRegion(CACHEABLE_REGION_MASK);
#elif __MICROBLAZE__
    Xil_ICacheEnable();
#endif
    Xil_ICacheEnable();
    Xil_DCacheEnable();
}

void
disable_caches()
{
    Xil_DCacheDisable();
    Xil_ICacheDisable();
}

void
init_uart()
{
    /* Bootrom/BSP configures PS7/PSU UART to 115200 bps */
}

void
init_platform()
{
    enable_caches();
    init_uart();
}

void
cleanup_platform()
{
    disable_caches();
}
