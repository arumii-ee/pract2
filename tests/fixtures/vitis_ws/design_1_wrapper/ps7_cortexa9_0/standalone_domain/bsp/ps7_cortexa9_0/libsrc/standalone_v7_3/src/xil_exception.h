#define Xil_ExceptionEnable() Xil_ExceptionEnableMask(XIL_EXCEPTION_IRQ)
void Xil_ExceptionInit(void);
void Xil_ExceptionRegisterHandler(u32 Exception_id, Xil_ExceptionHandler Handler, void *Data);
