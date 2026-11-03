#include <cstdio>
void vadd(const int a[64], const int b[64], int out[64]);
int main() {
    int a[64], b[64], o[64];
    vadd(a, b, o);
    printf("done\n");
    return 0;
}
