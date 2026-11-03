#include "ap_int.h"
#include "hls_stream.h"
// 두 벡터를 더하는 HLS 커널
void vadd(const int a[64], const int b[64], int out[64])
{
#pragma HLS INTERFACE m_axi port=a offset=slave
#pragma HLS INTERFACE s_axilite port=return
    for (int i = 0; i < 64; i++) {
#pragma HLS PIPELINE II=1
        out[i] = a[i] + b[i];
    }
}
