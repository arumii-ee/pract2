/** Initializes a specific timer/counter instance/driver. */
int XTmrCtr_Initialize(XTmrCtr * InstancePtr, u16 DeviceId) { return 0; }
void XTmrCtr_SetHandler(XTmrCtr *InstancePtr, XTmrCtr_Handler FuncPtr, void *CallBackRef) {}
void XTmrCtr_SetOptions(XTmrCtr * InstancePtr, u8 TmrCtrNumber, u32 Options) {}
void XTmrCtr_SetResetValue(XTmrCtr * InstancePtr, u8 TmrCtrNumber, u32 ResetValue) {}
void XTmrCtr_Start(XTmrCtr * InstancePtr, u8 TmrCtrNumber) {}
void XTmrCtr_InterruptHandler(void *InstancePtr) {}
